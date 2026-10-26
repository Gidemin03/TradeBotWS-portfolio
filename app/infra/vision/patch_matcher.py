from __future__ import annotations

import base64
import hashlib
from typing import Tuple

import numpy as np

from app.domain.models import InventoryPatch, InventoryPatches


def _to_gray_rgba(data: np.ndarray) -> np.ndarray:
    r = data[..., 0].astype(np.float32)
    g = data[..., 1].astype(np.float32)
    b = data[..., 2].astype(np.float32)
    return (0.2989 * r) + (0.5870 * g) + (0.1140 * b)


def _to_gray_bgra(data: np.ndarray) -> np.ndarray:
    b = data[..., 0].astype(np.float32)
    g = data[..., 1].astype(np.float32)
    r = data[..., 2].astype(np.float32)
    return (0.1140 * b) + (0.5870 * g) + (0.2989 * r)


def _normalize(arr: np.ndarray) -> np.ndarray:
    mean = float(arr.mean())
    std = float(arr.std())
    if std < 1e-6:
        return arr - mean
    return (arr - mean) / std


def _decode_patch(patch: InventoryPatch) -> np.ndarray:
    raw = base64.b64decode(patch.data_b64)
    expected = patch.width * patch.height * patch.channels
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < expected:
        raise ValueError("Patch payload too short")
    arr = arr[:expected]
    arr = arr.reshape((patch.height, patch.width, patch.channels))
    return _to_gray_rgba(arr)


class PatchMatcher:
    """
    Быстрое сравнение патчей по MSE/корреляции.
    """

    def __init__(
        self,
        patches: InventoryPatches,
        mse_threshold: float,
        corr_threshold: float,
    ) -> None:
        self._patches = patches
        self._mse_thr = mse_threshold
        self._corr_thr = corr_threshold

        self._left_gray = _decode_patch(patches.left)
        self._right_gray = _decode_patch(patches.right)

    @staticmethod
    def signature(patches: InventoryPatches) -> str:
        digest = hashlib.sha1(
            (patches.left.data_b64 + "|" + patches.right.data_b64).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        return digest

    @staticmethod
    def _corrcoef(a: np.ndarray, b: np.ndarray) -> float:
        a_mean = float(a.mean())
        b_mean = float(b.mean())
        da = a - a_mean
        db = b - b_mean
        denom = float(np.sqrt(np.sum(da * da) * np.sum(db * db)))
        if denom == 0:
            return 0.0
        return float(np.sum(da * db) / denom)

    def _is_match(self, region_bgra: np.ndarray, reference_gray: np.ndarray) -> tuple[bool, float, float]:
        if region_bgra.shape[0] != reference_gray.shape[0] or region_bgra.shape[1] != reference_gray.shape[1]:
            return False, float("inf"), 0.0
        candidate = _to_gray_bgra(region_bgra)
        mse = float(np.mean((candidate - reference_gray) ** 2))
        cand_norm = _normalize(candidate)
        ref_norm = _normalize(reference_gray)
        norm_mse = float(np.mean((cand_norm - ref_norm) ** 2))
        corr = self._corrcoef(cand_norm, ref_norm)

        match = (mse <= self._mse_thr) or (norm_mse <= self._mse_thr and corr >= self._corr_thr)
        return match, mse, corr, norm_mse

    def match_regions(
        self,
        left_region: np.ndarray,
        right_region: np.ndarray,
    ) -> Tuple[Tuple[bool, float, float, float], Tuple[bool, float, float, float]]:
        return (
            self._is_match(left_region, self._left_gray),
            self._is_match(right_region, self._right_gray),
        )
