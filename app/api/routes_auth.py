from fastapi import APIRouter, Request
from pydantic import BaseModel
from app.storage import save_state

router = APIRouter(tags=["auth"])


class CookiesBody(BaseModel):
    poe_sessid: str | None = None
    cf_clearance: str | None = None


@router.get("/auth/cookies")
async def get_cookies(request: Request) -> dict:
    ctx = request.app.state.context
    return {
        "poe_sessid": ctx.poe_sessid,
        "cf_clearance": ctx.cf_clearance,
    }


@router.post("/auth/cookies")
async def set_cookies(body: CookiesBody, request: Request) -> dict:
    ctx = request.app.state.context
    ctx.set_auth(body.poe_sessid, body.cf_clearance)
    save_state(request.app.state.state_path, ctx)
    return {"status": "ok"}
