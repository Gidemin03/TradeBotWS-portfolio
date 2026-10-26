# api/routes_status.py

from fastapi import APIRouter, Request

router = APIRouter(tags=["status"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/status")
async def get_status(request: Request) -> dict:
    ctx = request.app.state.context
    return {
        "bot_status": ctx.bot_status,
        "live_search_presets_count": len(ctx.live_search_presets),
        "events_count": len(ctx.events),
    }


@router.get("/events")
async def get_events(request: Request) -> list[dict]:
    ctx = request.app.state.context
    return [e.dict() for e in ctx.events]
