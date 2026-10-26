import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from urllib.parse import urlparse

import websockets
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from app.services.item_fetch_manager import ItemFetchManager
from app.domain.state import BotContext
from app.domain.models import (
    LiveSearchPreset,
    LiveSearchStatus,
    Event,
    EventLevel,
)
from infra.http_session import HttpSessionFactory

logger = logging.getLogger(__name__)

# Политика PoE из твоего сниппета (секунды)
RECONNECT_DELAYS = [15, 45, 120, 300, 900, 1800, 3600]
RECONNECT_DECAY_TIMEOUTS = [60, 180, 420, 900, 1800, 2700, 3600]


def build_live_ws_url(search_url: str) -> str:
    """
    Из PoE trade URL делаем live WS URL.

    https://www.pathofexile.com/trade2/search/poe2/League/ID
      -> wss://www.pathofexile.com/api/trade2/live/poe2/League/ID
    """
    parsed = urlparse(search_url)
    parts = parsed.path.split("/")  # ['', 'trade2', 'search', 'poe2', 'League', 'ID']

    try:
        search_idx = parts.index("search")
    except ValueError as exc:
        raise ValueError(f"URL не похож на PoE trade search: {search_url}") from exc

    trade_segment = parts[1]          # 'trade' или 'trade2'
    tail = "/".join(parts[search_idx + 1 :])  # 'poe2/League/ID'

    return f"wss://{parsed.netloc}/api/{trade_segment}/live/{tail}"


def build_ws_headers(
    search_url: str,
    poe_sessid: str | None,
    cf_clearance: str | None,
) -> dict:
    """
    Заголовки максимально похожие на те, что шлёт браузер с сайта PoE.
    """
    parsed = urlparse(search_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    cookies: list[str] = []
    if poe_sessid:
        cookies.append(f"POESESSID={poe_sessid}")
    if cf_clearance:
        cookies.append(f"cf_clearance={cf_clearance}")

    return {
        "Origin": origin,
        "Cookie": "; ".join(cookies),
        # при желании можешь переопределить User-Agent в config
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }


async def teleport_to_hideout(
    hideout_token: str,
    search_url: str,
    http_factory: HttpSessionFactory,
    poe_sessid: str | None,
    cf_clearance: str | None,
) -> None:
    """
    Делает POST /api/trade2/whisper (как на сайте), чтобы телепортироваться к продавцу.
    """
    parsed = urlparse(search_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    cookies: list[str] = []
    if poe_sessid:
        cookies.append(f"POESESSID={poe_sessid}")
    if cf_clearance:
        cookies.append(f"cf_clearance={cf_clearance}")
    cookies.append("continue=true")

    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": origin,
        "Referer": search_url,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": "; ".join(cookies),
    }

    session = await http_factory.get_session()
    url = "https://www.pathofexile.com/api/trade2/whisper"

    async with session.post(
        url,
        json={"token": hideout_token, "continue": "true"},
        headers=headers,
        timeout=10,
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(
                f"teleport failed: status={resp.status}, body={body[:200]}"
            )

        payload = await resp.json()
        if not payload or not payload.get("success"):
            raise RuntimeError(f"teleport failed: payload={payload}")


class PoeReconnectPolicy:
    """
    Реализация политики из массивов:
      _reconnectDelays / _reconnectDecayTimeouts

    Идея:
      - есть уровень [0..6];
      - при rate-limit (1013) берём delay[level], увеличиваем level,
      - со временем level сам по себе уменьшается (decay).
    """

    def __init__(self) -> None:
        self._level = 0
        self._last_error_at: Optional[float] = None

    def _apply_decay(self) -> None:
        if self._last_error_at is None:
            return

        now = time.monotonic()
        # можем за одно вычисление "просесть" на несколько уровней,
        # если прошло много времени
        while self._level > 0:
            timeout = RECONNECT_DECAY_TIMEOUTS[self._level]
            if now - self._last_error_at >= timeout:
                self._level -= 1
                self._last_error_at += timeout
            else:
                break

    def next_delay(self) -> int:
        """
        Вызывается, когда получили rate-limit (1013).
        Возвращает задержку в секундах и повышает уровень.
        """
        self._apply_decay()

        delay = RECONNECT_DELAYS[self._level]

        self._last_error_at = time.monotonic()
        if self._level < len(RECONNECT_DELAYS) - 1:
            self._level += 1

        return delay

    def reset(self) -> None:
        """Вызываем при успешном стабильном коннекте."""
        self._level = 0
        self._last_error_at = None


@dataclass
class PoeLiveSearchClient:
    """
    Один live search пресет <-> один экземпляр этого клиента.

    Он:
      - коннектится к PoE WS,
      - читает сообщения,
      - пушит Event-ы и обновляет статус пресета в BotContext,
      - делает reconnect/backoff по их правилам.
    """

    preset: LiveSearchPreset
    ctx: BotContext
    reconnect_policy: PoeReconnectPolicy = field(default_factory=PoeReconnectPolicy)
    item_fetch_manager: ItemFetchManager | None = None
    on_status_change: Callable[[LiveSearchPreset], None] | None = None

    async def run(self) -> None:
        preset_id = self.preset.id
        ws_url = build_live_ws_url(str(self.preset.url))
        if not self.ctx.poe_sessid:
            # нет авторизационных cookies — дальше 401, выходим сразу
            self.ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"[{self.preset.name}] POESESSID не задан. Укажите cookies в UI.",
                )
            )
            await self._set_status(LiveSearchStatus.ERROR)
            return

        headers = build_ws_headers(
            search_url=str(self.preset.url),
            poe_sessid=self.ctx.poe_sessid,
            cf_clearance=self.ctx.cf_clearance,
        )

        attempts = 0  # "обычные" попытки (по аналогии с trade.js)

        logger.info("LiveSearch[%s]: start loop, url=%s", preset_id, ws_url)

        try:
            while True:
                await self._set_status(LiveSearchStatus.CONNECTING)

                try:
                    async with websockets.connect(
                        ws_url,
                        additional_headers=headers,
                        ping_interval=None,
                        ping_timeout=20,
                        max_queue=32,
                    ) as ws:
                        # успешное подключение -> сбрасываем попытки / rate-limit уровень
                        attempts = 0
                        self.reconnect_policy.reset()
                        await self._ws_loop(ws)

                        # если _ws_loop вышел без исключений — просто завершаемся
                        logger.info("LiveSearch[%s]: ws_loop finished normally", preset_id)
                        return

                except asyncio.CancelledError:
                    logger.info("LiveSearch[%s]: cancelled", preset_id)
                    await self._on_stopped()
                    raise


                except ConnectionClosedError as exc:
                    # сюда попадаем при нормальном закрытии с кодом
                    code = exc.code
                    reason = exc.reason
                    logger.warning("LiveSearch[%s]: closed code=%s, reason=%r", preset_id, code, reason)

                    if code == 1013:
                        # rate-limit: используем policy-массивы
                        delay = self.reconnect_policy.next_delay()
                        await self._on_error(
                            f"Rate limit (1013). Повтор через {delay} c."
                        )
                        await asyncio.sleep(delay)
                        continue

                    if code == 1012:
                        # сервер рестартится — фиксированные 30 сек как на сайте
                        delay = 30
                        await self._on_error(
                            f"Server restarting (1012). Повтор через {delay} c."
                        )
                        await asyncio.sleep(delay)
                        continue

                    # остальные коды — как у оригинального JS: 10 + 5 * attempts, до 60
                    attempts += 1
                    delay = min(10 + 5 * attempts, 60)
                    await self._on_error(
                        f"WS закрыт (code={code}). Повтор через {delay} c."
                    )
                    await asyncio.sleep(delay)

                except InvalidStatus as exc:
                    # HTTP 401/403 при handshake
                    await self._on_error(
                        f"WS отклонён сервером: HTTP {exc.status_code}. Проверь cookies POESESSID/CF_CLEARANCE."
                    )
                    await asyncio.sleep(10)
                    return

                except Exception as exc:  # noqa: BLE001
                    attempts += 1
                    delay = min(10 + 5 * attempts, 60)
                    logger.exception("LiveSearch[%s]: unexpected error", preset_id)
                    await self._on_error(
                        f"Ошибка WS: {exc!r}. Повтор через {delay} c."
                    )
                    await asyncio.sleep(delay)

        finally:
            # safety net: чтобы статус не завис в CONNECTING/ERROR
            await self._set_status(LiveSearchStatus.STOPPED)

    async def _ws_loop(self, ws) -> None:
        """
        Основной цикл чтения сообщений из PoE.
        """
        preset_id = self.preset.id

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("LiveSearch[%s]: invalid JSON: %r", preset_id, raw)
                continue

            # первое сообщение от сервера: {"auth": true}
            if msg.get("auth"):
                await self._set_status(LiveSearchStatus.SEARCHING)
                self.ctx.push_event(
                    Event(
                        level=EventLevel.INFO,
                        message=f"[{self.preset.name}] live search authenticated",
                    )
                )
                continue

            # обычная пачка результатов
            if "count" in msg:
                total = msg.get("count")

                raw_result = msg.get("result")

                # PoE live WS шлёт либо строку-токен, либо массив токенов
                if isinstance(raw_result, list):
                    result_ids = raw_result
                elif isinstance(raw_result, str):
                    result_ids = [raw_result]
                else:
                    result_ids = []

                n = len(result_ids)

                # для отладки покажем первые несколько токенов обрезанными
                sample = [rid[:6] for rid in result_ids[:3]]
                self.ctx.push_event(
                    Event(
                        level=EventLevel.INFO,
                        message=(
                            f"[{self.preset.name}] {n} новых результатов, "
                            f"total={total}, sample_ids={sample}"
                        ),
                    )
                )

                # МГНОВЕННО отдаём в ItemFetchManager (без await)
                if self.item_fetch_manager and result_ids:
                    self.item_fetch_manager.submit_from_ws(self.preset, result_ids)

                continue

            # всё остальное сейчас просто логируем
            self.ctx.push_event(
                Event(
                    level=EventLevel.INFO,
                    message=f"[{self.preset.name}] WS message: {msg}",
                )
            )

    async def _set_status(self, status: LiveSearchStatus) -> None:
        preset = self.ctx.live_search_presets.get(self.preset.id)
        if not preset:
            return
        if preset.status == status:
            return
        preset.status = status
        self.ctx.live_search_presets[preset.id] = preset
        if self.on_status_change:
            try:
                self.on_status_change(preset)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LiveSearch on_status_change failed: %r", exc)

    async def _on_error(self, msg: str) -> None:
        await self._set_status(LiveSearchStatus.ERROR)
        self.ctx.push_event(
            Event(
                level=EventLevel.WARNING,
                message=f"[{self.preset.name}] {msg}",
            )
        )

    async def _on_stopped(self) -> None:
        await self._set_status(LiveSearchStatus.STOPPED)
        self.ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=f"[{self.preset.name}] live search stopped",
            )
        )
