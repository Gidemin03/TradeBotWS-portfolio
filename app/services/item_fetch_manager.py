# app/services/item_fetch_manager.py

from __future__ import annotations

import asyncio
from collections import deque
from typing import Dict, List, Set, TYPE_CHECKING
from urllib.parse import urlparse

from app.domain.models import LiveSearchPreset, Event, EventLevel, FetchedItem
from app.domain.state import BotContext
from app.infra.http_session import HttpSessionFactory

if TYPE_CHECKING:
    from app.services.bot_manager import BotManager


def build_fetch_url(search_url: str, item_ids: List[str]) -> str:
    """
    Строит URL вида:
    https://www.pathofexile.com/api/trade2/fetch/<ids>?query=<search_id>&realm=<realm>

    Из URL поиска:
    https://www.pathofexile.com/trade2/search/<realm>/<league>/<search_id>
    """
    parsed = urlparse(search_url)
    parts = parsed.path.strip("/").split("/")

    # ожидаем .../trade2/search/<realm>/<league>/<search_id>
    try:
        search_idx = parts.index("search")
    except ValueError as exc:
        raise ValueError(f"Unexpected PoE search url: {search_url}") from exc

    if len(parts) < search_idx + 3:
        raise ValueError(f"Too short PoE search url: {search_url}")

    trade_segment = parts[0]          # 'trade' или 'trade2'
    realm = parts[search_idx + 1]     # poe / poe2
    query_id = parts[-1]              # последний сегмент — id поискового запроса

    ids_str = ",".join(item_ids)

    return (
        f"{parsed.scheme}://{parsed.netloc}/api/{trade_segment}/fetch/"
        f"{ids_str}?query={query_id}&realm={realm}"
    )


def build_http_headers(
    referer: str,
    poe_sessid: str | None,
    cf_clearance: str | None,
) -> Dict[str, str]:
    """
    Минимальный набор заголовков для /api/trade2/fetch —
    максимально похож на браузер.
    """
    cookies: list[str] = []
    if poe_sessid:
        cookies.append(f"POESESSID={poe_sessid}")
    if cf_clearance:
        cookies.append(f"cf_clearance={cf_clearance}")

    headers: Dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Cookie": "; ".join(cookies),
        "Referer": referer,
    }

    return headers


class ItemFetchManager:
    """
    Быстрый мост:
      PoE WS -> result_ids -> /fetch -> (позже) BotManager.

    Требования:
    - НЕ блокировать WS-луп (всё через create_task).
    - Минимум логики: last_item_id per preset, защита от дублей по item_id.
    """

    def __init__(
        self,
        ctx: BotContext,
        http_factory: HttpSessionFactory,
        bot_manager: "BotManager",
    ) -> None:
        self._ctx = ctx
        self._http_factory = http_factory
        self._bot_manager = bot_manager

        # последние обработанные item_id по каждому пресету (кольцевой буфер)
        self._last_item_ids: Dict[str, deque[str]] = {}

        # item_id, по которым прямо сейчас идёт fetch
        self._inflight: Set[str] = set()

    # --- публичное API ---

    def submit_from_ws(self, preset: LiveSearchPreset, result_ids: List[str]) -> None:
        """
        Вызывается из PoeLiveSearchClient при приходе нового WS-сообщения.

        ВАЖНО: метод sync, без await — чтобы не тормозить _ws_loop.
        Внутри создаём отдельную async-таску.
        """
        if not result_ids:
            return

        buf = self._last_item_ids.get(preset.id)
        if buf is None:
            buf = deque(maxlen=5)
            self._last_item_ids[preset.id] = buf

        new_ids: List[str] = []
        for rid in result_ids:
            # пропускаем, если id уже был в последних N или сейчас в работе
            if rid in buf or rid in self._inflight:
                continue
            new_ids.append(rid)
            buf.append(rid)

        if not new_ids:
            return

        for current_id in new_ids:
            self._inflight.add(current_id)
            asyncio.create_task(
                self._fetch_single_item(preset, current_id),
                name=f"item-fetch-{preset.name}-{current_id[:6]}",
            )

    # --- внутренняя логика ---

    async def _fetch_single_item(self, preset: LiveSearchPreset, item_id: str) -> None:
        if not self._ctx.poe_sessid:
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"[{preset.name}] fetch {item_id[:6]}..: POESESSID не задан",
                )
            )
            self._inflight.discard(item_id)
            return
        try:
            session = await self._http_factory.get_session()
            url = build_fetch_url(str(preset.url), [item_id])
            headers = build_http_headers(
                referer=str(preset.url),
                poe_sessid=self._ctx.poe_sessid,
                cf_clearance=self._ctx.cf_clearance,
            )

            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    self._ctx.push_event(
                        Event(
                            level=EventLevel.ERROR,
                            message=(
                                f"[{preset.name}] fetch {item_id[:6]}.. "
                                f"failed: status={resp.status}, body={body[:200]}"
                            ),
                        )
                    )
                    return

                data = await resp.json()

        except Exception as exc:  # noqa: BLE001
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=(
                        f"[{preset.name}] fetch {item_id[:6]}.. "
                        f"exception: {exc!r}"
                    ),
                )
            )
            return
        finally:
            self._inflight.discard(item_id)

        results = data.get("result") or []
        if not results:
            self._ctx.push_event(
                Event(
                    level=EventLevel.WARNING,
                    message=f"[{preset.name}] fetch {item_id[:6]}.. вернул пустой result",
                )
            )
            return

        raw_item = results[0]
        if raw_item is None:
            # тут предмет для этого id не найден — либо id битый, либо не тот query/realm
            self._ctx.push_event(
                Event(
                    level=EventLevel.WARNING,
                    message=(
                        f"[{preset.name}] fetch {item_id[:6]}..: "
                        f"result[0] == null (listing не найден для этого id)"
                    ),
                )
            )
            return
        listing = raw_item.get("listing") or {}
        stash = listing.get("stash") or {}

        hideout_token = listing.get("hideout_token")
        x = stash.get("x")
        y = stash.get("y")

        if not hideout_token or x is None or y is None:
            self._ctx.push_event(
                Event(
                    level=EventLevel.WARNING,
                    message=(
                        f"[{preset.name}] fetch {item_id[:6]}..: "
                        f"нет hideout_token/x/y"
                    ),
                )
            )
            return

        item = FetchedItem(
            preset_id=preset.id,
            item_id=raw_item.get("id") or item_id,
            hideout_token=hideout_token,
            stash_x=int(x),
            stash_y=int(y),
        )

        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=(
                    f"[{preset.name}] item {item.item_id[:6]}.. "
                    f"loc=({item.stash_x}, {item.stash_y})"
                ),
            )
        )

        # передаем в BotManager для дальнейших действий в игре
        self._bot_manager.submit_item(preset, item)
