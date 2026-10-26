from typing import Dict, Optional
import asyncio

from app.services.item_fetch_manager import ItemFetchManager
from app.infra.poe.web_client import PoeLiveSearchClient
from app.domain.state import BotContext
from app.domain.models import (
    LiveSearchPreset,
    LiveSearchStatus,
    LiveSearchMode,
    Event,
    EventLevel,
)
from app.services.sse_manager import SSEManager


class LiveSearchManager:
    """
    Manages live search presets: CRUD, start/stop, status updates.
    """

    def __init__(
        self,
        context: BotContext,
        item_fetch_manager: ItemFetchManager,
        sse_manager: SSEManager | None = None,
    ) -> None:
        self._ctx = context
        self._item_fetch_manager = item_fetch_manager
        self._tasks: Dict[str, asyncio.Task] = {}
        self._bulk_start_task: asyncio.Task | None = None
        self._bulk_cancel_event = asyncio.Event()
        self._sse = sse_manager

    # --- Public API ---

    async def create_preset(
        self,
        url: str,
        name: Optional[str] = None,
        mode: Optional[LiveSearchMode] = None,
    ) -> LiveSearchPreset:
        preset = LiveSearchPreset.from_url(url=url, name=name, mode=mode)
        self._ctx.add_preset(preset)

        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=f"Preset '{preset.name}' created (mode={preset.mode.value})",
            )
        )
        self._publish_presets()

        return preset

    def get_preset(self, preset_id: str) -> Optional[LiveSearchPreset]:
        return self._ctx.live_search_presets.get(preset_id)

    def list_presets(self) -> list[LiveSearchPreset]:
        return self._ctx.list_presets()

    async def start_all(self) -> None:
        if self._bulk_start_task and not self._bulk_start_task.done():
            self._bulk_cancel_event.set()
            try:
                await self._bulk_start_task
            except asyncio.CancelledError:
                pass

        self._bulk_cancel_event = asyncio.Event()

        async def _run():
            presets = self.list_presets()
            for idx, preset in enumerate(presets):
                if self._bulk_cancel_event.is_set():
                    break
                await self.start_preset(preset.id)
                if idx < len(presets) - 1 and not self._bulk_cancel_event.is_set():
                    await asyncio.sleep(1)

        self._bulk_start_task = asyncio.create_task(_run(), name="live-search-start-all")

    async def stop_all(self) -> None:
        if self._bulk_start_task and not self._bulk_start_task.done():
            self._bulk_cancel_event.set()
            try:
                await self._bulk_start_task
            except asyncio.CancelledError:
                pass

        tasks = list(self._tasks.keys())
        for preset_id in tasks:
            await self.stop_preset(preset_id)

    # --- Lifecycle of a single preset ---

    async def start_preset(self, preset_id: str) -> None:
        preset = self.get_preset(preset_id)
        if not preset:
            raise ValueError("Preset not found")

        task = self._tasks.get(preset_id)
        if task and not task.done():
            return

        preset.status = LiveSearchStatus.CONNECTING
        self._ctx.live_search_presets[preset_id] = preset
        self._publish_presets()

        client = PoeLiveSearchClient(
            preset=preset,
            ctx=self._ctx,
            item_fetch_manager=self._item_fetch_manager,
            on_status_change=self._on_preset_status_change,
        )

        async def ws_loop():
            try:
                await client.run()
            except asyncio.CancelledError:
                preset.status = LiveSearchStatus.STOPPED
                self._ctx.live_search_presets[preset_id] = preset
                self._publish_presets()
                raise

        task = asyncio.create_task(ws_loop(), name=f"live-search-{preset_id}")
        self._tasks[preset_id] = task

    async def stop_preset(self, preset_id: str) -> None:
        preset = self.get_preset(preset_id)
        if not preset:
            raise ValueError("Preset not found")

        task = self._tasks.get(preset_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.pop(preset_id, None)

        if preset_id in self._ctx.live_search_presets:
            preset.status = LiveSearchStatus.STOPPED
            self._ctx.live_search_presets[preset_id] = preset
            self._publish_presets()

    async def delete_preset(self, preset_id: str) -> None:
        preset = self.get_preset(preset_id)
        if not preset:
            raise ValueError("Preset not found")

        await self.stop_preset(preset_id)
        self._ctx.remove_preset(preset_id)
        self._tasks.pop(preset_id, None)

        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=f"Preset '{preset.name}' deleted",
            )
        )
        self._publish_presets()

    async def set_preset_mode(
        self,
        preset_id: str,
        mode: LiveSearchMode,
    ) -> LiveSearchPreset:
        preset = self.get_preset(preset_id)
        if not preset:
            raise ValueError("Preset not found")

        preset.mode = mode
        self._ctx.live_search_presets[preset_id] = preset

        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=f"Preset '{preset.name}' mode set to {mode.value}",
            )
        )
        self._publish_presets()
        return preset

    async def close(self) -> None:
        if not self._tasks:
            return

        tasks = list(self._tasks.values())

        for task in tasks:
            if task and not task.done():
                task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

        self._tasks.clear()

        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message="LiveSearchManager: tasks stopped (shutdown)",
            )
        )

    # --- SSE helpers ---

    def _publish_presets(self) -> None:
        if self._sse:
            self._sse.publish_presets(self.list_presets())

    def _on_preset_status_change(self, _: LiveSearchPreset) -> None:
        self._publish_presets()
