import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent

# Позволяет запускать как `python app/main.py`, добавляя корень в sys.path
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from app.config import SERVER_PORT  # noqa: E402
from app.api import routes_status, routes_live_search, routes_auth, routes_grid, routes_stream  # noqa: E402
from app.domain.state import BotContext  # noqa: E402
from app.domain.models import Event, EventLevel  # noqa: E402
from app.services.live_search_manager import LiveSearchManager  # noqa: E402
from app.services.bot_manager import BotManager  # noqa: E402
from app.infra.http_session import HttpSessionFactory  # noqa: E402
from app.services.item_fetch_manager import ItemFetchManager  # noqa: E402
from app.infra.poe.game_client import GameClient  # noqa: E402
from app.services.game_window_manager import GameWindowManager  # noqa: E402
from app.infra.ui.calibrator_launcher import CalibratorHotkey  # noqa: E402
from app.services.merchant_grid_service import MerchantGridService  # noqa: E402
from app.services.open_inventory_detector import OpenInventoryDetector  # noqa: E402
from app.services.sse_manager import SSEManager  # noqa: E402
from app.storage import load_state, save_state  # noqa: E402

UI_DIR = BASE_DIR / "ui"
STATIC_DIR = UI_DIR / "static"
STATE_PATH = BASE_DIR / "state.json"
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="PoE Trade Bot")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(UI_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    context = BotContext()
    sse_manager = SSEManager()
    context.sse_manager = sse_manager
    http_session_factory = HttpSessionFactory()
    merchant_grid_service = MerchantGridService(context)
    game_window_manager = GameWindowManager(context)
    game_client = GameClient(context, merchant_grid_service, game_window_manager)
    ready_detector = OpenInventoryDetector(context)
    calibrator_hotkey = None
    try:
        calibrator_hotkey = CalibratorHotkey(STATE_PATH, context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось инициализировать хоткей F8: %r", exc)
        context.push_event(
            Event(
                level=EventLevel.WARNING,
                message="Не удалось включить хоткей F8 (требуется зависимость keyboard)",
            )
        )
    bot_manager = BotManager(
        ctx=context,
        game_client=game_client,
        http_factory=http_session_factory,
        ready_detector=ready_detector,
        window_manager=game_window_manager,
        sse_manager=sse_manager,
    )
    item_fetch_manager = ItemFetchManager(
        ctx=context,
        http_factory=http_session_factory,
        bot_manager=bot_manager,
    )
    live_search_manager = LiveSearchManager(
        context=context,
        item_fetch_manager=item_fetch_manager,
        sse_manager=sse_manager,
    )

    load_state(STATE_PATH, context)

    app.state.context = context
    app.state.http_session_factory = http_session_factory
    app.state.bot_manager = bot_manager
    app.state.game_client = game_client
    app.state.item_fetch_manager = item_fetch_manager
    app.state.live_search_manager = live_search_manager
    app.state.merchant_grid_service = merchant_grid_service
    app.state.game_window_manager = game_window_manager
    app.state.calibrator_hotkey = calibrator_hotkey
    app.state.ready_detector = ready_detector
    app.state.state_path = STATE_PATH
    app.state.sse_manager = sse_manager

    app.include_router(routes_status.router, prefix="/api")
    app.include_router(routes_live_search.router, prefix="/api")
    app.include_router(routes_auth.router, prefix="/api")
    app.include_router(routes_grid.router, prefix="/api")
    app.include_router(routes_stream.router, prefix="/api")

    @app.on_event("startup")
    async def _startup() -> None:
        bot_manager.start()
        if app.state.calibrator_hotkey:
            app.state.calibrator_hotkey.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        save_state(STATE_PATH, context)
        await bot_manager.stop()
        await live_search_manager.close()
        await http_session_factory.close()

    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=SERVER_PORT,
        log_level="info",
    )
