from __future__ import annotations

import asyncio
from collections import deque
from typing import Deque, Optional, Tuple

from app import config
from app.domain.models import (
    BotStatus,
    Event,
    EventLevel,
    FetchedItem,
    LiveSearchPreset,
    LiveSearchMode,
)
from app.domain.state import BotContext
from app.infra.poe.game_client import GameClient
from app.infra.http_session import HttpSessionFactory
from app.infra.poe.web_client import teleport_to_hideout
from app.services.game_window_manager import GameWindowManager
from app.services.open_inventory_detector import OpenInventoryDetector
from app.services.sse_manager import SSEManager


class BotManager:
    """
    LIFO-очередь предметов с одиночным воркером.
    Телепорты выполняются через веб-запрос к PoE (/trade2/whisper),
    покупка (autobuy) пока остаётся заглушкой в GameClient.
    """

    def __init__(
        self,
        ctx: BotContext,
        game_client: GameClient,
        http_factory: HttpSessionFactory,
        ready_detector: OpenInventoryDetector,
        window_manager: GameWindowManager,
        sse_manager: SSEManager | None = None,
    ) -> None:
        self._ctx = ctx
        self._game_client = game_client
        self._http_factory = http_factory
        self._ready_detector = ready_detector
        self._window_manager = window_manager
        self._queue: Deque[Tuple[LiveSearchPreset, FetchedItem]] = deque()
        self._wake_event = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None
        self._sse = sse_manager

    def _set_status(self, status: BotStatus) -> None:
        if self._ctx.bot_status == status:
            return
        prev = self._ctx.bot_status
        self._ctx.bot_status = status
        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=f"EVENT_BOT_STATUS_CHANGED: {prev.value} -> {status.value}",
            )
        )
        if self._sse:
            self._sse.publish_status(
                bot_status=self._ctx.bot_status,
                presets_count=len(self._ctx.live_search_presets),
                events_count=len(self._ctx.events),
            )

    # --- публичный API ---

    def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._worker = asyncio.create_task(self._worker_loop(), name="bot-manager")

    async def stop(self) -> None:
        if not self._worker:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    def submit_item(self, preset: LiveSearchPreset, item: FetchedItem) -> None:
        """
        Кладём в голову очереди, чтобы свежие офферы обрабатывались первыми.
        """
        self._queue.appendleft((preset, item))
        self._wake_event.set()

    def clear_queue(self) -> None:
        """
        Очистить очередь покупок и вернуть статус IDLE.
        """
        if self._queue:
            self._queue.clear()
            self._ctx.push_event(
                Event(
                    level=EventLevel.INFO,
                    message="Purchase queue cleared.",
                )
            )
        self._set_status(BotStatus.IDLE)
        self._wake_event.clear()

    # --- внутренности ---

    async def _worker_loop(self) -> None:
        while True:
            if not self._queue:
                self._set_status(BotStatus.IDLE)
                self._wake_event.clear()
                await self._wake_event.wait()
                continue

            preset, item = self._queue.popleft()

            try:
                await self._handle_item(preset, item)
            except Exception as exc:  # noqa: BLE001
                self._set_status(BotStatus.IDLE)
                self._ctx.push_event(
                    Event(
                        level=EventLevel.ERROR,
                        message=(
                            f"[{preset.name}] item {item.item_id[:6]}.. "
                            f"failed: {exc!r}"
                        ),
                    )
                )

            if not self._queue:
                try:
                    await self._game_client.return_to_hideout()
                except Exception as exc:  # noqa: BLE001
                    self._ctx.push_event(
                        Event(
                            level=EventLevel.ERROR,
                            message=f"Возврат в hideout завершился с ошибкой: {exc!r}",
                        )
                    )
                self._set_status(BotStatus.IDLE)

    async def _handle_item(self, preset: LiveSearchPreset, item: FetchedItem) -> None:
        if not self._ctx.poe_sessid:
            raise RuntimeError("POESESSID not set, save cookies in UI")

        if preset.mode == LiveSearchMode.ONLY_TP:
            await self._handle_only_tp(preset, item)
            self._set_status(BotStatus.IDLE)
            return

        if preset.mode == LiveSearchMode.AUTOBUY:
            await self._handle_autobuy(preset, item)
            return

        self._ctx.push_event(
            Event(
                level=EventLevel.WARNING,
                message=(
                    f"[{preset.name}] unknown mode {preset.mode}, "
                    f"skipping item {item.item_id[:6]}.."
                ),
            )
        )
        self._set_status(BotStatus.IDLE)

    async def _handle_only_tp(self, preset: LiveSearchPreset, item: FetchedItem) -> None:
        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=(
                    f"[{preset.name}] TP_ONLY: teleport to item {item.item_id[:6]}.. "
                ),
            )
        )
        try:
            await teleport_to_hideout(
                hideout_token=item.hideout_token,
                search_url=str(preset.url),
                http_factory=self._http_factory,
                poe_sessid=self._ctx.poe_sessid,
                cf_clearance=self._ctx.cf_clearance,
            )
        except Exception as exc:  # noqa: BLE001
            self._ctx.push_event(
                Event(
                    level=EventLevel.WARNING,
                    message=f"[{preset.name}] TP_ONLY teleport failed: {exc!r}",
                )
            )
            return

        self._set_status(BotStatus.IN_TELEPORTING)
        window_ready = True
        if config.ACTIVATE_GAME_WINDOW:
            window_ready = await self._window_manager.ensure_game_active()
            if not window_ready:
                self._ctx.push_event(
                    Event(
                        level=EventLevel.WARNING,
                        message=f"[{preset.name}] Game window not ready after teleport.",
                    )
                )
        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=(
                    f"[{preset.name}] TP_ONLY: holding for {config.TP_ONLY_HOLD_SECONDS:.1f}s before next actions"
                ),
            )
        )
        await asyncio.sleep(config.TP_ONLY_HOLD_SECONDS)
        if window_ready:
            self._ctx.push_event(
                Event(
                    level=EventLevel.INFO,
                    message=f"[{preset.name}] TP_ONLY: hold finished",
                )
            )

    async def _handle_autobuy(self, preset: LiveSearchPreset, item: FetchedItem) -> None:
        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=(
                    f"[{preset.name}] AUTOBUY item {item.item_id[:6]}.. "
                ),
            )
        )
        try:
            await teleport_to_hideout(
                hideout_token=item.hideout_token,
                search_url=str(preset.url),
                http_factory=self._http_factory,
                poe_sessid=self._ctx.poe_sessid,
                cf_clearance=self._ctx.cf_clearance,
            )
        except Exception as exc:  # noqa: BLE001
            self._ctx.push_event(
                Event(
                    level=EventLevel.WARNING,
                    message=f"[{preset.name}] teleport failed: {exc!r}, skip item {item.item_id[:6]}..",
                )
            )
            self._set_status(BotStatus.IDLE)
            return

        self._set_status(BotStatus.IN_TELEPORTING)
        if config.ACTIVATE_GAME_WINDOW:
            window_ready = await self._window_manager.ensure_game_active()
            if not window_ready:
                self._ctx.push_event(
                    Event(
                        level=EventLevel.WARNING,
                        message=(
                            f"[{preset.name}] Game window not ready, skip AUTOBUY {item.item_id[:6]}.."
                        ),
                    )
                )
                self._set_status(BotStatus.IDLE)
                return
        ready = await self._ready_detector.detect(timeout=config.READY_WAIT_TIMEOUT)
        if not ready:
            self._ctx.push_event(
                Event(
                    level=EventLevel.WARNING,
                    message=f"[{preset.name}] READY_TO_BUY not detected, skip item {item.item_id[:6]}..",
                )
            )
            self._set_status(BotStatus.IDLE)
            return

        self._set_status(BotStatus.BUSY)
        success = await self._game_client.buy_item_with_retry(preset, item)
        self._ctx.push_event(
            Event(
                level=EventLevel.INFO if success else EventLevel.WARNING,
                message=(
                    f"[{preset.name}] AUTOBUY finished for item {item.item_id[:6]}.. "
                    f"{'slot changed (assume bought)' if success else 'slot unchanged or errored'}"
                ),
            )
        )
        self._set_status(BotStatus.IDLE)

