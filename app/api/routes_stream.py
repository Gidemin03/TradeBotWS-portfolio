import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.sse_manager import SSEManager

router = APIRouter(tags=["stream"])


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    manager: SSEManager = request.app.state.sse_manager
    queue = manager.subscribe()

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(
                        queue.get(),
                        timeout=manager.heartbeat_interval,
                    )
                    yield msg
                except asyncio.TimeoutError:
                    yield manager.heartbeat()
        except asyncio.CancelledError:
            # клиент отвалился
            raise
        finally:
            manager.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
