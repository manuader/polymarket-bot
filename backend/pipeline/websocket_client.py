"""
Connects to Polymarket CLOB WebSocket for real-time trade/price data.
Subscribes to market channel using token IDs from the database.
Sends heartbeats every 10s, uses a 120s inactivity watchdog for reconnection.
"""

import asyncio
import json
import time
from datetime import datetime, timezone

import structlog
import websockets

from config import get_settings
from pipeline.market_sync import get_all_token_ids

log = structlog.get_logger()
settings = get_settings()

HEARTBEAT_INTERVAL = 10  # seconds
INACTIVITY_TIMEOUT = 120  # seconds
MAX_TOKENS_PER_CONNECTION = 500
RECONNECT_BASE_DELAY = 1
RECONNECT_MAX_DELAY = 60


class WebSocketManager:
    """Manages one or more WebSocket connections to Polymarket CLOB."""

    def __init__(self, on_message_callback):
        self.on_message = on_message_callback
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        """Start WebSocket connections, sharding tokens across multiple connections."""
        self._running = True
        token_ids = await get_all_token_ids()
        if not token_ids:
            log.warning("ws_no_tokens", msg="No token IDs found. Waiting for market sync.")
            await asyncio.sleep(30)
            token_ids = await get_all_token_ids()

        # Shard tokens across connections
        chunks = [
            token_ids[i : i + MAX_TOKENS_PER_CONNECTION]
            for i in range(0, len(token_ids), MAX_TOKENS_PER_CONNECTION)
        ]

        if not chunks:
            chunks = [[]]  # At least one connection to listen for new subscriptions

        log.info(
            "ws_starting",
            total_tokens=len(token_ids),
            connections=len(chunks),
        )

        for i, chunk in enumerate(chunks):
            task = asyncio.create_task(
                self._run_connection(chunk, conn_id=i),
                name=f"ws-conn-{i}",
            )
            self._tasks.append(task)

    async def stop(self):
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def _run_connection(self, token_ids: list[str], conn_id: int):
        """Run a single WebSocket connection with reconnection logic."""
        delay = RECONNECT_BASE_DELAY

        while self._running:
            try:
                await self._connect_and_listen(token_ids, conn_id)
                delay = RECONNECT_BASE_DELAY  # reset on clean disconnect
            except (
                websockets.ConnectionClosed,
                websockets.InvalidURI,
                ConnectionRefusedError,
                OSError,
            ) as e:
                log.warning("ws_disconnected", conn=conn_id, error=str(e), reconnect_in=delay)
            except Exception as e:
                log.error("ws_unexpected_error", conn=conn_id, error=str(e))

            if self._running:
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _connect_and_listen(self, token_ids: list[str], conn_id: int):
        """Connect, subscribe, and listen for messages."""
        async with websockets.connect(
            settings.clob_ws_url,
            ping_interval=None,  # we handle heartbeats manually
            close_timeout=10,
        ) as ws:
            # Subscribe to market channel
            if token_ids:
                subscribe_msg = {
                    "type": "market",
                    "assets_ids": token_ids,
                }
                await ws.send(json.dumps(subscribe_msg))
                log.info("ws_subscribed", conn=conn_id, tokens=len(token_ids))

            last_data_time = time.monotonic()

            # Heartbeat task
            async def heartbeat():
                while self._running:
                    try:
                        await ws.send("PING")
                    except Exception:
                        return
                    await asyncio.sleep(HEARTBEAT_INTERVAL)

            hb_task = asyncio.create_task(heartbeat())

            try:
                while self._running:
                    # Watchdog: force reconnect if no data
                    elapsed = time.monotonic() - last_data_time
                    if elapsed > INACTIVITY_TIMEOUT:
                        log.warning("ws_inactivity_timeout", conn=conn_id, elapsed=elapsed)
                        break

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=INACTIVITY_TIMEOUT)
                    except asyncio.TimeoutError:
                        log.warning("ws_recv_timeout", conn=conn_id)
                        break

                    last_data_time = time.monotonic()

                    if raw == "PONG" or raw == "pong":
                        continue

                    try:
                        data = json.loads(raw)
                        await self.on_message(data)
                    except json.JSONDecodeError:
                        pass
            finally:
                hb_task.cancel()

    async def refresh_subscriptions(self):
        """Refresh token subscriptions (called after market sync)."""
        if self._tasks:
            await self.stop()
        await self.start()


async def parse_ws_trade(msg: dict) -> dict | None:
    """Parse a WebSocket trade message into a normalized dict.

    The WS market channel sends price/trade updates. The format varies
    but typically includes asset_id, price, and sometimes trade info.
    """
    # The WebSocket sends various event types
    event_type = msg.get("event_type") or msg.get("type", "")

    if event_type == "last_trade_price":
        # This gives us price updates per token but no wallet info
        return {
            "type": "price_update",
            "asset_id": msg.get("asset_id", ""),
            "price": float(msg.get("price", 0)),
            "timestamp": datetime.now(timezone.utc),
        }

    if event_type == "tick_size_change":
        return None  # Not relevant

    # For book/trade events, extract what we can
    if "market" in msg or "asset_id" in msg:
        return {
            "type": "market_event",
            "raw": msg,
            "timestamp": datetime.now(timezone.utc),
        }

    return None
