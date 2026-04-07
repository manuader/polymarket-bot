"""
Polls the Polymarket Data API for large trades with wallet addresses.
The CLOB WebSocket only provides price updates, not wallet info.
This module fills that gap by fetching recent trades from the public Data API.
"""

import asyncio
from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import get_settings
from db.database import async_session
from db.models import Trade, Market

log = structlog.get_logger()
settings = get_settings()

DATA_API_TRADES_URL = f"{settings.data_api_url}/trades"
CLOB_TRADES_URL = f"{settings.clob_api_url}/trades"
POLL_INTERVAL = 60  # seconds


async def fetch_recent_large_trades(
    client: httpx.AsyncClient,
    min_usd: float = 5000,
    limit: int = 100,
) -> list[dict]:
    """Fetch recent large trades from the Data API."""
    trades = []

    try:
        # Data API provides public trade history with wallet addresses
        params = {
            "limit": limit,
        }
        resp = await client.get(DATA_API_TRADES_URL, params=params, timeout=30)
        resp.raise_for_status()
        raw_trades = resp.json()

        if isinstance(raw_trades, list):
            for t in raw_trades:
                usd_value = 0
                try:
                    price = float(t.get("price", 0))
                    size = float(t.get("size", 0))
                    usd_value = price * size
                except (TypeError, ValueError):
                    continue

                if usd_value >= min_usd:
                    trades.append(t)
    except httpx.HTTPStatusError as e:
        log.warning("data_api_http_error", status=e.response.status_code)
    except Exception as e:
        log.error("data_api_fetch_error", error=str(e))

    return trades


def parse_data_api_trade(raw: dict) -> dict | None:
    """Parse a Data API trade into our DB schema."""
    try:
        price = float(raw.get("price", 0))
        size = float(raw.get("size", 0))
    except (TypeError, ValueError):
        return None

    if price <= 0 or size <= 0:
        return None

    usd_value = price * size

    # Parse timestamp
    ts_raw = raw.get("timestamp") or raw.get("matchTime") or raw.get("created_at")
    timestamp = datetime.now(timezone.utc)
    if ts_raw:
        try:
            if isinstance(ts_raw, (int, float)):
                timestamp = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            else:
                timestamp = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            pass

    # Determine outcome from token_id by checking market data
    outcome = raw.get("outcome", "")
    side = raw.get("side", "BUY")

    # Get asset/token and market IDs
    asset_id = raw.get("asset_id") or raw.get("tokenID") or raw.get("token_id", "")
    market_id = raw.get("market") or raw.get("conditionId") or raw.get("condition_id", "")

    # Wallet addresses
    maker = raw.get("maker_address") or raw.get("maker", "")
    taker = raw.get("taker_address") or raw.get("taker", "")

    # If we can't identify the market, try proxy wallet
    if not market_id:
        return None

    return {
        "market_id": market_id,
        "token_id": asset_id,
        "timestamp": timestamp,
        "price": price,
        "size": size,
        "side": side.upper() if side else "BUY",
        "outcome": outcome.upper() if outcome else "YES",
        "maker_address": maker,
        "taker_address": taker,
        "usd_value": usd_value,
    }


async def ingest_trades(trades_data: list[dict]) -> int:
    """Insert parsed trades into the database, skipping duplicates."""
    if not trades_data:
        return 0

    inserted = 0
    async with async_session() as session:
        for data in trades_data:
            # Check if this trade already exists (by market + timestamp + price + size)
            existing = await session.execute(
                select(Trade.id).where(
                    Trade.market_id == data["market_id"],
                    Trade.timestamp == data["timestamp"],
                    Trade.price == data["price"],
                    Trade.size == data["size"],
                    Trade.taker_address == data.get("taker_address", ""),
                ).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue

            # Verify market exists
            market = await session.execute(
                select(Market.condition_id).where(
                    Market.condition_id == data["market_id"]
                ).limit(1)
            )
            if market.scalar_one_or_none() is None:
                continue

            trade = Trade(**data)
            session.add(trade)
            inserted += 1

        if inserted > 0:
            await session.commit()

    return inserted


async def enrich_once(min_usd: float | None = None):
    """Run a single enrichment cycle."""
    threshold = min_usd or settings.min_trade_usd / 2  # Use half threshold for enrichment
    async with httpx.AsyncClient(timeout=30) as client:
        raw_trades = await fetch_recent_large_trades(client, min_usd=threshold)
        parsed = [parse_data_api_trade(t) for t in raw_trades]
        parsed = [t for t in parsed if t is not None]
        inserted = await ingest_trades(parsed)
        if inserted > 0:
            log.info("trade_enrichment_complete", fetched=len(raw_trades), inserted=inserted)
        return inserted


async def run_trade_enricher(interval_seconds: int = POLL_INTERVAL):
    """Run trade enrichment loop."""
    log.info("trade_enricher_starting", interval=interval_seconds)
    while True:
        try:
            await enrich_once()
        except Exception as e:
            log.error("trade_enricher_error", error=str(e))
        await asyncio.sleep(interval_seconds)
