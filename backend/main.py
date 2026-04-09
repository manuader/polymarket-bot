import asyncio

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from pipeline.market_sync import run_market_sync, sync_once, get_all_token_ids
from pipeline.trade_enricher import run_trade_enricher, set_on_new_trades_callback
from pipeline.volume_tracker import run_volume_tracker
from pipeline.wallet_profiler import run_wallet_profiler
from pipeline.websocket_client import WebSocketManager, parse_ws_trade
from pipeline.orderbook_cache import run_orderbook_cache
from detection.signal_manager import run_detection_engine, scan_recent_trades, trade_queue
from trading.paper_engine import run_paper_engine
from trading.cleanup import run_cleanup
from trading.outcome_tracker import run_outcome_tracker
from api.routes.dashboard import router as dashboard_router
from api.routes.signals import router as signals_router
from api.routes.trades import router as trades_router
from api.routes.analytics import router as analytics_router
from api.routes.activity import router as activity_router
from api.websocket import websocket_endpoint

settings = get_settings()

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
)
log = structlog.get_logger()

# Price updates queue — consumed by detection engine
price_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)


async def on_ws_message(msg: dict):
    """Callback for WebSocket messages — routes to price queue."""
    parsed = await parse_ws_trade(msg)
    if parsed and parsed.get("type") == "price_update":
        try:
            price_queue.put_nowait(parsed)
        except asyncio.QueueFull:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting_polymarket_bot", min_score=settings.min_score_to_trade)

    # Initial market sync (with timeout so it doesn't block startup)
    try:
        count = await asyncio.wait_for(sync_once(), timeout=120)
        log.info("initial_market_sync", markets=count)
    except asyncio.TimeoutError:
        log.warning("initial_market_sync_timeout", msg="Continuing startup, will retry in background")
    except Exception as e:
        log.error("initial_market_sync_failed", error=str(e))

    # Connect trade enricher → detection engine
    async def on_new_trade(trade):
        try:
            trade_queue.put_nowait(trade)
        except asyncio.QueueFull:
            pass
    set_on_new_trades_callback(on_new_trade)

    # Start all background tasks
    ws_manager = WebSocketManager(on_message_callback=on_ws_message)
    tasks = [
        # Data pipeline
        asyncio.create_task(run_market_sync(300), name="market-sync"),
        asyncio.create_task(run_trade_enricher(15), name="trade-enricher"),  # poll every 15s
        asyncio.create_task(run_volume_tracker(60), name="volume-tracker"),
        asyncio.create_task(run_wallet_profiler(120), name="wallet-profiler"),
        asyncio.create_task(run_orderbook_cache(get_all_token_ids, 60), name="orderbook-cache"),
        # Detection engine
        asyncio.create_task(run_detection_engine(), name="detection-engine"),
        # Paper trading engine
        asyncio.create_task(run_paper_engine(), name="paper-engine"),
        # Cleanup old trades & track outcomes for learning
        asyncio.create_task(run_cleanup(), name="trade-cleanup"),
        asyncio.create_task(run_outcome_tracker(), name="outcome-tracker"),
    ]

    # Start WebSocket connections to Polymarket
    try:
        await ws_manager.start()
    except Exception as e:
        log.error("ws_start_failed", error=str(e))

    # Scan recent trades for signals on startup
    try:
        await scan_recent_trades()
    except Exception as e:
        log.error("initial_scan_failed", error=str(e))

    app.state.ws_manager = ws_manager
    app.state.price_queue = price_queue
    app.state.bg_tasks = tasks

    yield

    # Shutdown
    log.info("shutting_down_polymarket_bot")
    await ws_manager.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(
    title="Polymarket Insider Detection Bot",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
app.include_router(dashboard_router)
app.include_router(signals_router)
app.include_router(trades_router)
app.include_router(analytics_router)
app.include_router(activity_router)


# WebSocket endpoint for frontend real-time updates
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket_endpoint(websocket)


# Serve static frontend files (production build)
import os
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA for any non-API route."""
        file_path = static_dir / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(static_dir / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}
