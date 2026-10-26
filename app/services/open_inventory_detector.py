from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Optional, Tuple

from app import config
from app.domain.models import Event, EventLevel, InventoryGrid, InventoryPatches
from app.domain.state import BotContext
from app.infra.vision.patch_matcher import PatchMatcher


@dataclass
class _Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


def _clip_rect(rect: _Rect, screen_w: int, screen_h: int) -> Optional[_Rect]:
    x = max(0, rect.x)
    y = max(0, rect.y)
    w = min(rect.w, screen_w - x)
    h = min(rect.h, screen_h - y)
    if w <= 0 or h <= 0:
        return None
    return _Rect(x=x, y=y, w=w, h=h)


class OpenInventoryDetector:
    """
    Детектор открытия окна inventory по патчам над верхними ячейками.
    Запускается на время телепорта, затем выключается.
    """

    def __init__(self, ctx: BotContext) -> None:
        self._ctx = ctx
        self._matcher: Optional[PatchMatcher] = None
        self._matcher_signature: Optional[str] = None
        self._deps_available = self._check_dependencies()
        self._debug_counter = 0

    def _check_dependencies(self) -> bool:
        try:
            import mss  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as exc:
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"Open inv detector не активен: {exc}",
                )
            )
            return False
        return True

    async def detect(self, timeout: float | None) -> bool:
        import mss
        import numpy as np
        
        if not self._deps_available:
            return False
        grid = self._ctx.inventory_grid
        patches = self._ctx.inventory_patches
        if not grid or not patches:
            return False
        signature = PatchMatcher.signature(patches)
        if signature != self._matcher_signature:
            self._matcher_signature = signature
            self._matcher = PatchMatcher(
                patches=patches,
                mse_threshold=config.READY_MSE_THRESHOLD,
                corr_threshold=config.READY_CORR_THRESHOLD,
            )
        matcher = self._matcher
        if not matcher:
            return False

        consecutive = 0
        deadline = None if timeout is None else asyncio.get_event_loop().time() + timeout
        with mss.mss() as sct:
            while True:
                try:
                    if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                        return False
                    rects = self._build_patch_rects(grid, patches)
                    if rects is None:
                        continue
                    left_rect, right_rect = rects

                    capture_box = self._build_capture_box(left_rect, right_rect)
                    if capture_box is None:
                        continue

                    shot = sct.grab(capture_box)
                    frame = np.array(shot)  # BGRA

                    left_region = self._extract_region(frame, capture_box, left_rect)
                    right_region = self._extract_region(frame, capture_box, right_rect)
                    if left_region is None or right_region is None:
                        continue

                    (left_match, left_mse, left_corr, left_norm_mse), (right_match, right_mse, right_corr, right_norm_mse) = matcher.match_regions(left_region, right_region)
                    matched = left_match or right_match
                    if matched:
                        consecutive += 1
                        if consecutive >= config.READY_CONSECUTIVE_MATCHES:
                            return True
                    else:
                        consecutive = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self._ctx.push_event(
                        Event(
                            level=EventLevel.ERROR,
                            message=f"READY detector exception: {exc!r}",
                        )
                
                    )
                await asyncio.sleep(0.01)

    def _build_patch_rects(
        self,
        grid: InventoryGrid,
        patches: InventoryPatches,
    ) -> Optional[Tuple[_Rect, _Rect]]:
        left, top, right, bottom = grid.to_pixels()
        cell_w = (right - left) / grid.cols
        cell_h = (bottom - top) / grid.rows
        y_top = top - cell_h
        screen_w = grid.screen_width
        screen_h = grid.screen_height

        left_rect = _clip_rect(
            _Rect(
                x=int(math.floor(left)),
                y=int(math.floor(y_top)),
                w=patches.left.width,
                h=patches.left.height,
            ),
            screen_w,
            screen_h,
        )
        right_rect = _clip_rect(
            _Rect(
                x=int(math.floor(left + (grid.cols - 1) * cell_w)),
                y=int(math.floor(y_top)),
                w=patches.right.width,
                h=patches.right.height,
            ),
            screen_w,
            screen_h,
        )
        if left_rect is None or right_rect is None:
            return None
        return left_rect, right_rect


    @staticmethod
    def _build_capture_box(left_rect: _Rect, right_rect: _Rect) -> Optional[dict]:
        min_x = min(left_rect.x, right_rect.x)
        min_y = min(left_rect.y, right_rect.y)
        max_x = max(left_rect.right, right_rect.right)
        max_y = max(left_rect.bottom, right_rect.bottom)
        width = max_x - min_x
        height = max_y - min_y
        if width <= 0 or height <= 0:
            return None
        return {
            "left": min_x,
            "top": min_y,
            "width": width,
            "height": height,
        }

    @staticmethod
    def _extract_region(frame, capture_box: dict, rect: _Rect):
        x0 = rect.x - capture_box["left"]
        y0 = rect.y - capture_box["top"]
        x1 = x0 + rect.w
        y1 = y0 + rect.h
        if x0 < 0 or y0 < 0 or x1 > frame.shape[1] or y1 > frame.shape[0]:
            return None
        return frame[y0:y1, x0:x1, :]
