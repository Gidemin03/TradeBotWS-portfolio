from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from app import config
from app.domain.models import Event, EventLevel
from app.domain.state import BotContext
from app.services.merchant_grid_service import MerchantGridService


@dataclass(frozen=True)
class PatchRect:
    x: int
    y: int
    w: int
    h: int


@dataclass
class ItemPatch:
    rect: PatchRect
    gray: np.ndarray


def _normalize(arr: np.ndarray) -> np.ndarray:
    mean = float(arr.mean())
    std = float(arr.std())
    if std < 1e-6:
        return arr - mean
    return (arr - mean) / std


def _corrcoef(a: np.ndarray, b: np.ndarray) -> float:
    a_mean = float(a.mean())
    b_mean = float(b.mean())
    da = a - a_mean
    db = b - b_mean
    denom = float(np.sqrt(np.sum(da * da) * np.sum(db * db)))
    if denom == 0:
        return 0.0
    return float(np.sum(da * db) / denom)


def _to_gray_bgra(data: np.ndarray) -> np.ndarray:
    b = data[..., 0].astype(np.float32)
    g = data[..., 1].astype(np.float32)
    r = data[..., 2].astype(np.float32)
    return (0.1140 * b) + (0.5870 * g) + (0.2989 * r)


class ItemPatchService:
    """
    Съём и сравнение патчей ячейки merchant grid для проверки, остался ли предмет.
    """

    def __init__(self, ctx: BotContext, grid_service: MerchantGridService) -> None:
        self._ctx = ctx
        self._grid_service = grid_service
        self._deps_available = self._check_dependencies()

    def _check_dependencies(self) -> bool:
        try:
            import mss  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as exc:
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"Item patch deps missing (mss/numpy): {exc!r}",
                )
            )
            return False
        return True

    def _clip_rect(self, rect: PatchRect, screen_w: int, screen_h: int) -> Optional[PatchRect]:
        x = max(0, rect.x)
        y = max(0, rect.y)
        w = min(rect.w, screen_w - x)
        h = min(rect.h, screen_h - y)
        if w <= 0 or h <= 0:
            return None
        return PatchRect(x=x, y=y, w=w, h=h)

    def _build_rect(self, stash_x: int, stash_y: int) -> Optional[PatchRect]:
        grid = self._grid_service.get()
        if not grid:
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message="Merchant grid is not calibrated; cannot capture patch.",
                )
            )
            return None

        left, top, right, bottom = grid.to_pixels()
        cell_w = (right - left) / grid.cols
        cell_h = (bottom - top) / grid.rows
        cx = left + (stash_x + 0.5) * cell_w
        cy = top + (stash_y + 0.5) * cell_h

        patch_w = max(1, int(round(cell_w * config.MERCHANT_PATCH_SCALE)))
        patch_h = max(1, int(round(cell_h * config.MERCHANT_PATCH_SCALE)))

        rect = PatchRect(
            x=int(math.floor(cx - patch_w / 2)),
            y=int(math.floor(cy - patch_h / 2)),
            w=patch_w,
            h=patch_h,
        )
        return self._clip_rect(rect, grid.screen_width, grid.screen_height)

    def capture_patch(self, stash_x: int, stash_y: int) -> Optional[ItemPatch]:
        if not self._deps_available:
            return None
        rect = self._build_rect(stash_x, stash_y)
        if rect is None:
            return None

        import mss

        try:
            with mss.mss() as sct:
                shot = sct.grab(
                    {
                        "left": rect.x,
                        "top": rect.y,
                        "width": rect.w,
                        "height": rect.h,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            self._ctx.push_event(
                Event(
                    level=EventLevel.ERROR,
                    message=f"Item patch capture failed: {exc!r}",
                )
            )
            return None

        frame = np.array(shot)  # BGRA
        gray = _to_gray_bgra(frame)
        return ItemPatch(rect=rect, gray=gray)

    def compare_patches(self, reference: ItemPatch, current: ItemPatch) -> Tuple[bool, float, float, float]:
        """
        Возвращает (still_present, mse, norm_mse, corr).
        still_present=True, если патчи визуально совпадают (предмет лежит).
        """
        if reference.gray.shape != current.gray.shape:
            raise ValueError("Patch size mismatch")

        mse = float(np.mean((current.gray - reference.gray) ** 2))
        ref_norm = _normalize(reference.gray)
        cur_norm = _normalize(current.gray)
        norm_mse = float(np.mean((cur_norm - ref_norm) ** 2))
        corr = _corrcoef(cur_norm, ref_norm)

        still_present = (mse <= config.MERCHANT_PATCH_MSE_THRESHOLD) or (
            norm_mse <= config.MERCHANT_PATCH_MSE_THRESHOLD and corr >= config.MERCHANT_PATCH_CORR_THRESHOLD
        )
        return still_present, mse, norm_mse, corr
