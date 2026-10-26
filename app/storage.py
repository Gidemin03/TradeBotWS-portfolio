import json
from pathlib import Path
from typing import Any, Dict, List

from app.domain.models import Event, EventLevel, InventoryGrid, InventoryPatches, LiveSearchPreset, MerchantGrid
from app.domain.state import BotContext
from fastapi.encoders import jsonable_encoder


def load_state(path: Path, ctx: BotContext) -> None:
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        ctx.push_event(
            Event(
                level=EventLevel.ERROR,
                message=f"Не удалось прочитать state.json: {exc!r}",
            )
        )
        return

    ctx.poe_sessid = raw.get("poe_sessid") or None
    ctx.cf_clearance = raw.get("cf_clearance") or None

    presets: List[Dict[str, Any]] = raw.get("presets") or []
    for p in presets:
        try:
            preset = LiveSearchPreset.parse_obj(p)
            ctx.add_preset(preset)
        except Exception:
            continue

    m_grid_raw = raw.get("merchant_grid")
    if m_grid_raw:
        try:
            ctx.merchant_grid = MerchantGrid.parse_obj(m_grid_raw)
        except Exception:
            ctx.merchant_grid = None

    inv_grid_raw = raw.get("inventory_grid")
    if inv_grid_raw:
        try:
            ctx.inventory_grid = InventoryGrid.parse_obj(inv_grid_raw)
        except Exception:
            ctx.inventory_grid = None

    inv_patches_raw = raw.get("inventory_patches")
    if inv_patches_raw:
        try:
            ctx.inventory_patches = InventoryPatches.parse_obj(inv_patches_raw)
        except Exception:
            ctx.inventory_patches = None


def save_state(path: Path, ctx: BotContext) -> None:
    data = {
        "poe_sessid": ctx.poe_sessid,
        "cf_clearance": ctx.cf_clearance,
        "presets": [p.dict() for p in ctx.live_search_presets.values()],
        "merchant_grid": ctx.merchant_grid.dict() if ctx.merchant_grid else None,
        "inventory_grid": ctx.inventory_grid.dict() if ctx.inventory_grid else None,
        "inventory_patches": ctx.inventory_patches.dict() if ctx.inventory_patches else None,
    }
    payload = jsonable_encoder(data)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)
    except Exception as exc:
        ctx.push_event(
            Event(
                level=EventLevel.ERROR,
                message=f"Не удалось сохранить state.json: {exc!r}",
            )
        )
