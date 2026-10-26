from __future__ import annotations

import asyncio
import logging
import ctypes
from ctypes import wintypes
from typing import Optional

from app import config
from app.domain.models import Event, EventLevel
from app.domain.state import BotContext


user32 = ctypes.WinDLL("user32", use_last_error=True)
logger = logging.getLogger(__name__)

SW_RESTORE = 9


class GameWindowManager:
    """
    Minimal wrapper around WinAPI for activating the PoE window.
    """

    def __init__(
        self,
        ctx: BotContext,
        window_title: str = "Path of Exile",
        activation_delay: float = config.GAME_WINDOW_ACTIVATION_DELAY,
    ) -> None:
        self._ctx = ctx
        self._window_title = window_title
        self._window_title_lower = window_title.lower()
        self._activation_delay = activation_delay

    def _get_window_title(self, hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def find_game_window(self) -> Optional[int]:
        matches: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd: int, _: int) -> bool:
            title = self._get_window_title(hwnd)
            if self._window_title_lower in title.lower():
                matches.append(hwnd)
                return False  # stop enumeration
            return True

        user32.EnumWindows(_enum_proc, 0)
        return matches[0] if matches else None

    def _is_foreground(self, hwnd: int) -> bool:
        return hwnd == user32.GetForegroundWindow()

    def _is_minimized(self, hwnd: int) -> bool:
        return bool(user32.IsIconic(hwnd))

    async def ensure_game_active(self) -> bool:
        """
        Restore and focus PoE window if needed.
        Returns True when window is ready for input, False otherwise.
        """
        hwnd = self.find_game_window()
        if not hwnd:
            self._ctx.push_event(
                Event(
                    level=EventLevel.WARNING,
                    message="Game window not found (Path of Exile).",
                )
            )
            return False

        try:
            minimized = self._is_minimized(hwnd)
            foreground = self._is_foreground(hwnd)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to check window state: %r", exc)
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"Cannot inspect game window state: {exc!r}",
                )
            )
            return False

        if not minimized and foreground:
            logger.debug("Game window already active")
            return True

        changed = False
        try:
            if minimized:
                user32.ShowWindow(hwnd, SW_RESTORE)
                changed = True
            if not foreground:
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
                changed = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to activate game window: %r", exc)
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"Cannot activate game window: {exc!r}",
                )
            )
            return False

        self._ctx.push_event(
            Event(
                level=EventLevel.INFO,
                message="Game window activated.",
            )
        )

        if changed and self._activation_delay > 0:
            await asyncio.sleep(self._activation_delay)

        return True
