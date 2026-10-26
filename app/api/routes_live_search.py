# api/routes_live_search.py

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, HttpUrl

from app.domain.models import LiveSearchPreset, LiveSearchMode
from app.storage import save_state

router = APIRouter(tags=["live_search"])


class CreatePresetBody(BaseModel):
    url: HttpUrl
    name: str | None = None
    mode: LiveSearchMode | None = None

class UpdatePresetModeBody(BaseModel):
    mode: LiveSearchMode


@router.get("/live_search/presets", response_model=list[LiveSearchPreset])
async def list_presets(request: Request):
    manager = request.app.state.live_search_manager
    return manager.list_presets()


@router.post("/live_search/presets", response_model=LiveSearchPreset)
async def create_preset(body: CreatePresetBody, request: Request):
    manager = request.app.state.live_search_manager
    preset = await manager.create_preset(
        url=body.url,
        name=body.name,
        mode=body.mode,
    )
    save_state(request.app.state.state_path, request.app.state.context)
    return preset


@router.post("/live_search/presets/{preset_id}/start")
async def start_preset(preset_id: str, request: Request):
    manager = request.app.state.live_search_manager
    try:
        await manager.start_preset(preset_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    save_state(request.app.state.state_path, request.app.state.context)
    return {"status": "ok"}


@router.post("/live_search/presets/{preset_id}/stop")
async def stop_preset(preset_id: str, request: Request):
    manager = request.app.state.live_search_manager
    try:
        await manager.stop_preset(preset_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    save_state(request.app.state.state_path, request.app.state.context)
    return {"status": "ok"}

@router.post("/live_search/presets/start_all")
async def start_all_presets(request: Request):
    manager = request.app.state.live_search_manager
    await manager.start_all()
    save_state(request.app.state.state_path, request.app.state.context)
    return {"status": "ok"}

@router.post("/live_search/presets/stop_all")
async def stop_all_presets(request: Request):
    manager = request.app.state.live_search_manager
    bot_manager = request.app.state.bot_manager
    await manager.stop_all()
    bot_manager.clear_queue()
    save_state(request.app.state.state_path, request.app.state.context)
    return {"status": "ok"}

@router.patch("/live_search/presets/{preset_id}/mode", response_model=LiveSearchPreset)
async def change_preset_mode(
    preset_id: str,
    body: UpdatePresetModeBody,
    request: Request,
):
    manager = request.app.state.live_search_manager
    try:
        preset = await manager.set_preset_mode(preset_id, body.mode)
    except ValueError as e:
        # если пресет не найден
        raise HTTPException(status_code=404, detail=str(e))
    save_state(request.app.state.state_path, request.app.state.context)
    return preset

@router.delete("/live_search/presets/{preset_id}")
async def delete_preset(preset_id: str, request: Request):
    """
    Удаление пресета по id.
    """
    manager = request.app.state.live_search_manager
    try:
        await manager.delete_preset(preset_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    save_state(request.app.state.state_path, request.app.state.context)
    return {"status": "ok"}
