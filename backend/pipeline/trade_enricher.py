"""
Polls the Polymarket Data API for trades with wallet addresses.
Fetches ALL recent trades (not just large ones) and stores them.
The detection engine then analyzes the large ones.
"""

import asyncio
from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import select
from db.database import async_session
from db.models import Trade, Market
from config import get_settings

log = structlog.get_logger()
settings = get_settings()

DATA_API_TRADES_URL = f"{settings.data_api_url}/trades"
POLL_INTERVAL = 30  # seconds — poll more frequently for faster detection


async def fetch_recent_trades(client: httpx.AsyncClient, limit: int = 500) -> list[dict]:
    """Fetch recent trades from the public Data API."""
    try:
        resp = await client.get(
            DATA_API_TRADES_URL,
            params={"limit": limit},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return []
    except httpx.HTTPStatusError as e:
        log.warning("data_api_http_error", status=e.response.status_code)
    except Exception as e:
        log.error("data_api_fetch_error", error=str(e))
    return []


def parse_trade(raw: dict) -> dict | None:
    """Parse a Data API trade into our DB schema.

    Real format from https://data-api.polymarket.com/trades:
    {
        "proxyWallet": "0x...",
        "side": "BUY"/"SELL",
        "asset": "8332...",          # token ID
        "conditionId": "0x7338...",  # market ID
        "size": 101.4,
        "price": 0.995,
        "timestamp": 1775688073,     # unix seconds
        "title": "Will ...",
        "outcome": "Yes"/"No",
        "transactionHash": "0x..."
    }
    """
    try:
        price = float(raw.get("price", 0))
        size = float(raw.get("size", 0))
    except (TypeError, ValueError):
        return None

    if price <= 0 or size <= 0:
        return None

    usd_value = price * size
    condition_id = raw.get("conditionId", "")
    if not condition_id:
        return None

    # Parse unix timestamp
    ts_raw = raw.get("timestamp")
    timestamp = datetime.now(timezone.utc)
    if ts_raw:
        try:
            timestamp = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass

    outcome = raw.get("outcome", "Yes")
    side = raw.get("side", "BUY")
    wallet = raw.get("proxyWallet", "")
    asset = raw.get("asset", "")
    tx_hash = raw.get("transactionHash", "")

    return {
        "market_id": condition_id,
        "token_id": asset,
        "timestamp": timestamp,
        "price": price,
        "size": size,
        "side": side.upper(),
        "outcome": outcome.upper() if outcome else "YES",
        "maker_address": "",  # Data API doesn't distinguish maker/taker
        "taker_address": wallet,
        "usd_value": usd_value,
    }


async def ingest_trades(trades_data: list[dict]) -> tuple[int, list]:
    """Insert parsed trades into the database. Returns (count, list of Trade objects)."""
    if not trades_data:
        return 0, []

    inserted = 0
    new_trades = []

    async with async_session() as session:
        # Get set of known market IDs for quick lookup
        market_result = await session.execute(
            select(Market.condition_id)
        )
        known_markets = {row[0] for row in market_result}

        for data in trades_data:
            if data["market_id"] not in known_markets:
                continue

            # Dedup by market + timestamp + price + size + wallet
            existing = await session.execute(
                select(Trade.id).where(
                    Trade.market_id == data["market_id"],
                    Trade.timestamp == data["timestamp"],
                    Trade.price == data["price"],
                    Trade.size == data["size"],
                    Trade.taker_address == data["taker_address"],
                ).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue

            trade = Trade(**data)
            session.add(trade)
            new_trades.append(trade)
            inserted += 1

        if inserted > 0:
            await session.commit()
            # Refresh to get IDs
            for t in new_trades:
                await session.refresh(t)

    return inserted, new_trades


# Callback to notify detection engine of new trades
_on_new_trades_callback = None


def set_on_new_trades_callback(cb):
    global _on_new_trades_callback
    _on_new_trades_callback = cb


async def enrich_once():
    """Run a single enrichment cycle."""
    async with httpx.AsyncClient(timeout=30) as client:
        raw_trades = await fetch_recent_trades(client, limit=500)

    parsed = [parse_trade(t) for t in raw_trades]
    parsed = [t for t in parsed if t is not None]
    inserted, new_trades = await ingest_trades(parsed)

    if inserted > 0:
        log.info(
            "trades_ingested",
            fetched=len(raw_trades),
            parsed=len(parsed),
            inserted=inserted,
        )

        # Log large trades to activity feed
        from activity import log_activity
        large_trades = [t for t in new_trades if t.usd_value >= settings.min_trade_usd]
        if large_trades:
            await log_activity(
                event_type="trades_ingested",
                severity="info",
                title=f"Ingested {inserted} trades ({len(large_trades)} large >= ${settings.min_trade_usd:,.0f})",
                detail=f"Largest: ${max(t.usd_value for t in large_trades):,.0f}",
                metadata={
                    "total_inserted": inserted,
                    "large_count": len(large_trades),
                    "largest_usd": max(t.usd_value for t in large_trades),
                },
            )

        # Feed large trades to detection engine
        if _on_new_trades_callback:
            for trade in large_trades:
                await _on_new_trades_callback(trade)

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
