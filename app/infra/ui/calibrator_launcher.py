from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from app.domain.models import Event, EventLevel
from app.domain.state import BotContext
from app.storage import load_state


class CalibratorHotkey:
    """Запуск PySide6-калибратора сеток по F8 и горячая перезагрузка состояния."""

    def __init__(self, state_path: Path, ctx: BotContext) -> None:
        self._state_path = state_path
        self._ctx = ctx
        self._process: Optional[subprocess.Popen] = None
        self._wait_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        try:
            import keyboard  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Отсутствует 'keyboard' для F8") from exc
        self._keyboard = keyboard

    def start(self) -> None:
        self._keyboard.add_hotkey("F8", self._launch)
        self._ctx.push_event(
            Event(level=EventLevel.INFO, message="Горячая клавиша F8 активна (калибратор)"),
        )

    def _launch(self) -> None:
        with self._lock:
            if self._wait_thread and not self._wait_thread.is_alive():
                self._wait_thread = None
            if self._process and self._process.poll() is None:
                # принудительно гасим старый процесс, чтобы не блокировать новый запуск
                try:
                    self._process.terminate()
                except Exception:
                    pass
                self._ctx.push_event(
                    Event(level=EventLevel.WARNING, message="Предыдущий калибратор завершён принудительно перед новым F8"),
                )
                self._process = None
                self._wait_thread = None
            else:
                self._process = None
            cmd = [
                sys.executable,
                "-m",
                "app.ui.grid_calibrator",
                "--state-path",
                str(self._state_path),
            ]
            try:
                self._process = subprocess.Popen(
                    cmd,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                self._ctx.push_event(
                    Event(level=EventLevel.INFO, message="Запущен калибратор сетки (F8)"),
                )
                self._wait_thread = threading.Thread(
                    target=self._wait_and_reload_state,
                    args=(self._process,),
                    daemon=True,
                )
                self._wait_thread.start()
            except Exception as exc:  # noqa: BLE001
                self._process = None
                self._wait_thread = None
                self._ctx.push_event(
                    Event(level=EventLevel.ERROR, message=f"Не удалось запустить калибратор: {exc!r}"),
                )

    def _wait_and_reload_state(self, proc: subprocess.Popen) -> None:
        try:
            proc.wait()
            load_state(self._state_path, self._ctx)
            self._ctx.push_event(
                Event(
                    level=EventLevel.INFO,
                    message="Состояние обновлено из state.json после калибратора",
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._ctx.push_event(
                Event(level=EventLevel.ERROR, message=f"Ошибка при обновлении состояния после калибратора: {exc!r}"),
            )
        finally:
            with self._lock:
                self._process = None
                self._wait_thread = None
