from __future__ import annotations

import asyncio
import logging
from typing import Tuple

from app import config
from app.domain.models import Event, EventLevel, FetchedItem, LiveSearchPreset
from app.domain.state import BotContext
from app.services.game_window_manager import GameWindowManager
from app.services.item_patch_service import ItemPatchService
from app.services.merchant_grid_service import MerchantGridService


class GameClient:
    """
    Управление вводом/кликами для AUTOBUY: наведение на merchant ячейку, ctrl+клики и отвод курсора.
    """

    def __init__(
        self,
        ctx: BotContext,
        grid_service: MerchantGridService,
        window_manager: GameWindowManager,
    ) -> None:
        self._ctx = ctx
        self._grid_service = grid_service
        self._window_manager = window_manager
        self._logger = logging.getLogger(__name__)
        self._pyautogui = None
        self._keyboard = None
        self._item_patch_service = ItemPatchService(ctx, grid_service)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def _ensure_input(self) -> bool:
        if self._pyautogui and self._keyboard:
            return True
        try:
            import pyautogui
            import keyboard
        except ImportError as exc:  # pragma: no cover
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"Нет зависимостей ввода (pyautogui/keyboard): {exc!r}",
                )
            )
            return False

        pyautogui.FAILSAFE = False  # отключаем спасительный угол (0,0)
        pyautogui.PAUSE = 0
        self._pyautogui = pyautogui
        self._keyboard = keyboard
        return True

    def _move_mouse(self, x: int, y: int) -> None:
        if not self._pyautogui:
            raise RuntimeError("pyautogui не инициализирован")
        self._pyautogui.moveTo(x, y)

    def _click(self, x: int, y: int) -> None:
        if not self._pyautogui:
            raise RuntimeError("pyautogui не инициализирован")
        self._pyautogui.click(x=x, y=y, button="left")

    def _ctrl_down(self) -> None:
        if not self._keyboard:
            raise RuntimeError("keyboard не инициализирован")
        self._keyboard.press("ctrl")

    def _ctrl_up(self) -> None:
        if not self._keyboard:
            raise RuntimeError("keyboard не инициализирован")
        self._keyboard.release("ctrl")

    async def _click_series_with_ctrl(
        self,
        x: int,
        y: int,
        clicks: int,
        interval: float,
    ) -> None:
        self._ctrl_down()
        try:
            for i in range(clicks):
                self._click(x, y)
                if i < clicks - 1:
                    await self.sleep(interval)
        finally:
            self._ctrl_up()

    async def _perform_attempt(
        self,
        target_xy: Tuple[int, int],
        inventory_xy: Tuple[int, int],
    ) -> None:
        self._move_mouse(*target_xy)
        await self._click_series_with_ctrl(
            x=target_xy[0],
            y=target_xy[1],
            clicks=3,
            interval=config.AUTOBUY_CLICK_INTERVAL,
        )
        self._move_mouse(*inventory_xy)
        await self.sleep(config.AUTOBUY_MOVE_WAIT)

    async def buy_item_with_retry(self, preset: LiveSearchPreset, item: FetchedItem) -> bool:
        """
        Три попытки покупки с проверкой патчей merchant-grid между попытками.
        Возвращает True, если слот изменился (считаем, что предмет выкуплен).
        """
        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message=f"[{preset.name}] AUTOBUY start item {item.item_id[:6]}..",
            )
        )
        if not self._ensure_input():
            return False

        try:
            merchant_xy = self._grid_service.slot_to_screen(item.stash_x, item.stash_y)
            inventory_xy = self._grid_service.slot_to_screen_inventory(0, 0)
        except Exception as exc:  # noqa: BLE001
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"[{preset.name}] AUTOBUY coords error: {exc!r}",
                )
            )
            return False

        reference_patch = self._item_patch_service.capture_patch(item.stash_x, item.stash_y)
        if reference_patch is None:
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"[{preset.name}] AUTOBUY: не удалось снять референсный патч merchant-ячейки.",
                )
            )
            return False

        success = False
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            self._ctx.push_event(
                Event(
                    level=EventLevel.INFO,
                    message=(
                        f"[{preset.name}] AUTOBUY попытка {attempt}: "
                        f"merchant=({merchant_xy[0]}, {merchant_xy[1]}), "
                        f"inv=({inventory_xy[0]}, {inventory_xy[1]})"
                    ),
                )
            )
            try:
                await self._perform_attempt(merchant_xy, inventory_xy)
            except Exception as exc:  # noqa: BLE001
                self._ctx.push_event(
                    Event(
                        level=EventLevel.ERROR,
                        message=f"[{preset.name}] AUTOBUY попытка {attempt} упала: {exc!r}",
                    )
                )
                break

            current_patch = self._item_patch_service.capture_patch(item.stash_x, item.stash_y)
            if current_patch is None:
                self._ctx.push_event(
                    Event(
                        level=EventLevel.ERROR,
                        message=f"[{preset.name}] AUTOBUY: не удалось снять патч после попытки {attempt}.",
                    )
                )
                break

            try:
                still_present, mse, norm_mse, corr = self._item_patch_service.compare_patches(
                    reference_patch,
                    current_patch,
                )
            except Exception as exc:  # noqa: BLE001
                self._ctx.push_event(
                    Event(
                        level=EventLevel.ERROR,
                        message=f"[{preset.name}] AUTOBUY: сравнение патчей не удалось: {exc!r}",
                    )
                )
                break

            if not still_present:
                self._ctx.push_event(
                    Event(
                        level=EventLevel.INFO,
                        message=(
                            f"[{preset.name}] AUTOBUY: слот изменился на попытке {attempt} "
                            f"(mse={mse:.1f}, norm_mse={norm_mse:.1f}, corr={corr:.2f}); считаем, что предмет выкуплен."
                        ),
                    )
                )
                success = True
                break

            if attempt >= max_attempts:
                self._ctx.push_event(
                    Event(
                        level=EventLevel.WARNING,
                        message=(
                            f"[{preset.name}] AUTOBUY: патчи совпадают после {attempt} попыток "
                            f"(mse={mse:.1f}, norm_mse={norm_mse:.1f}, corr={corr:.2f}); прекращаем."
                        ),
                    )
                )
                break

            self._ctx.push_event(
                Event(
                    level=EventLevel.INFO,
                    message=(
                        f"[{preset.name}] AUTOBUY: патчи совпадают (mse={mse:.1f}, norm_mse={norm_mse:.1f}, corr={corr:.2f}), "
                        f"пробуем снова."
                    ),
                )
            )

        return success

    async def return_to_hideout(self) -> None:
        """
        Send /hideout in chat if the PoE window is ready.
        """
        if config.ACTIVATE_GAME_WINDOW:
            window_ready = await self._window_manager.ensure_game_active()
            if not window_ready:
                return

        if not self._ensure_input():
            return
        try:
            self._ctx.push_event(
                Event(level=EventLevel.INFO, message="Return to hideout: open chat")
            )
            self._keyboard.press_and_release("enter")
            await self.sleep(config.HIDEOUT_CHAT_DELAY)
            self._keyboard.write("/hideout")
            await self.sleep(config.HIDEOUT_CHAT_DELAY)
            self._keyboard.press_and_release("enter")
            self._ctx.push_event(
                Event(level=EventLevel.INFO, message="Return to hideout: command sent")
            )
        except Exception as exc:  # noqa: BLE001
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"Return to hideout failed: {exc!r}",
                )
            )
