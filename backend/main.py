import asyncio

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from pipeline.market_sync import run_market_sync, sync_once, get_all_token_ids
from pipeline.trade_enricher import run_trade_enricher
from pipeline.volume_tracker import run_volume_tracker
from pipeline.wallet_profiler import run_wallet_profiler
from pipeline.websocket_client import WebSocketManager, parse_ws_trade
from pipeline.orderbook_cache import run_orderbook_cache

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
            pass  # Drop oldest if queue is full


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting_polymarket_bot", min_score=settings.min_score_to_trade)

    # Initial market sync before starting other tasks
    try:
        count = await sync_once()
        log.info("initial_market_sync", markets=count)
    except Exception as e:
        log.error("initial_market_sync_failed", error=str(e))

    # Start all pipeline background tasks
    ws_manager = WebSocketManager(on_message_callback=on_ws_message)
    tasks = [
        asyncio.create_task(run_market_sync(300), name="market-sync"),
        asyncio.create_task(run_trade_enricher(60), name="trade-enricher"),
        asyncio.create_task(run_volume_tracker(60), name="volume-tracker"),
        asyncio.create_task(run_wallet_profiler(120), name="wallet-profiler"),
        asyncio.create_task(run_orderbook_cache(get_all_token_ids, 60), name="orderbook-cache"),
    ]

    # Start WebSocket connections
    try:
        await ws_manager.start()
    except Exception as e:
        log.error("ws_start_failed", error=str(e))

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
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}
