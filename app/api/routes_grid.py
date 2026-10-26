from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.domain.models import MerchantGrid, InventoryGrid

router = APIRouter(tags=["grid"])


def _validate_grid(grid: MerchantGrid) -> None:
    if not (0 <= grid.left < grid.right <= 1 and 0 <= grid.top < grid.bottom <= 1):
        raise HTTPException(status_code=400, detail="Координаты должны быть в диапазоне [0, 1]")
    if grid.cols <= 0 or grid.rows <= 0:
        raise HTTPException(status_code=400, detail="Некорректное количество ячеек")
    min_px = 32
    if (grid.width * grid.screen_width) < min_px or (grid.height * grid.screen_height) < min_px:
        raise HTTPException(status_code=400, detail="Сетка слишком мала")


@router.get("/grid")
async def get_grid(request: Request) -> dict:
    ctx = request.app.state.context
    return {
        "merchant_grid": ctx.merchant_grid.dict() if ctx.merchant_grid else None,
        "inventory_grid": ctx.inventory_grid.dict() if ctx.inventory_grid else None,
        "inventory_patches": ctx.inventory_patches.dict() if ctx.inventory_patches else None,
    }


class GridPayload(BaseModel):
    merchant_grid: MerchantGrid | None = None
    inventory_grid: InventoryGrid | None = None


@router.put("/grid")
async def set_grid(request: Request, payload: GridPayload) -> dict:
    ctx = request.app.state.context
    if payload.merchant_grid:
        _validate_grid(payload.merchant_grid)
        ctx.merchant_grid = payload.merchant_grid
    if payload.inventory_grid:
        _validate_grid(payload.inventory_grid)
        ctx.inventory_grid = payload.inventory_grid
        ctx.inventory_patches = None

    return {
        "merchant_grid": ctx.merchant_grid.dict() if ctx.merchant_grid else None,
        "inventory_grid": ctx.inventory_grid.dict() if ctx.inventory_grid else None,
    }
