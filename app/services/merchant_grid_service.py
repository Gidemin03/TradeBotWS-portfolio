from __future__ import annotations

from typing import Optional, Tuple

from app.domain.models import MerchantGrid, InventoryGrid
from app.domain.state import BotContext


class MerchantGridService:
    """
    Управление конфигами сеток (merchant и inventory) и быстрый перевод слота в экранные координаты.
    """

    def __init__(self, ctx: BotContext) -> None:
        self._ctx = ctx

    def get(self) -> Optional[MerchantGrid]:
        return self._ctx.merchant_grid

    def is_configured(self) -> bool:
        return self._ctx.merchant_grid is not None

    def set_grid(self, grid: MerchantGrid) -> MerchantGrid:
        self._ctx.merchant_grid = grid
        return grid

    def slot_to_screen(
        self,
        x: int,
        y: int,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Tuple[int, int]:
        grid = self._ctx.merchant_grid
        if not grid:
            raise RuntimeError("Merchant grid is not calibrated yet")
        return grid.slot_to_screen(x=x, y=y, width=width, height=height)

    # --- inventory ---

    def get_inventory(self) -> Optional[InventoryGrid]:
        return self._ctx.inventory_grid

    def set_inventory(self, grid: InventoryGrid) -> InventoryGrid:
        self._ctx.inventory_grid = grid
        return grid

    def slot_to_screen_inventory(
        self,
        x: int,
        y: int,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Tuple[int, int]:
        grid = self._ctx.inventory_grid
        if not grid:
            raise RuntimeError("Inventory grid is not calibrated yet")
        return grid.slot_to_screen(x=x, y=y, width=width, height=height)
