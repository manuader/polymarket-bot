"""
Polls the Polymarket Data API for trades with wallet addresses.
Tracks the latest timestamp to only process NEW trades each cycle.
Feeds large trades to the detection engine in real-time.
"""

import asyncio
from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import select, func
from db.database import async_session
from db.models import Trade, Market
from config import get_settings

log = structlog.get_logger()
settings = get_settings()

DATA_API_TRADES_URL = f"{settings.data_api_url}/trades"
POLL_INTERVAL = 15  # poll every 15s for near-real-time detection

# Track latest trade timestamp to avoid re-processing
_last_seen_timestamp: int = 0


async def _init_last_seen():
    """Initialize last seen timestamp from DB on first run."""
    global _last_seen_timestamp
    if _last_seen_timestamp > 0:
        return
    async with async_session() as session:
        result = await session.execute(
            select(func.max(Trade.timestamp))
        )
        max_ts = result.scalar_one_or_none()
        if max_ts:
            _last_seen_timestamp = int(max_ts.timestamp())
            log.info("enricher_init_last_seen", timestamp=_last_seen_timestamp)


async def fetch_recent_trades(client: httpx.AsyncClient, limit: int = 200) -> list[dict]:
    """Fetch recent trades from the public Data API."""
    try:
        resp = await client.get(
            DATA_API_TRADES_URL,
            params={"limit": limit},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except httpx.HTTPStatusError as e:
        log.warning("data_api_http_error", status=e.response.status_code)
    except Exception as e:
        log.error("data_api_fetch_error", error=str(e))
    return []


def parse_trade(raw: dict) -> dict | None:
    """Parse a Data API trade into our DB schema."""
    try:
        price = float(raw.get("price", 0))
        size = float(raw.get("size", 0))
    except (TypeError, ValueError):
        return None

    if price <= 0 or size <= 0:
        return None

    condition_id = raw.get("conditionId", "")
    if not condition_id:
        return None

    ts_raw = raw.get("timestamp")
    timestamp = datetime.now(timezone.utc)
    ts_int = 0
    if ts_raw:
        try:
            ts_int = int(ts_raw)
            timestamp = datetime.fromtimestamp(ts_int, tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass

    return {
        "market_id": condition_id,
        "token_id": raw.get("asset", ""),
        "timestamp": timestamp,
        "ts_unix": ts_int,
        "price": price,
        "size": size,
        "side": (raw.get("side", "BUY")).upper(),
        "outcome": (raw.get("outcome", "Yes")).upper(),
        "maker_address": "",
        "taker_address": raw.get("proxyWallet", ""),
        "usd_value": price * size,
        "tx_hash": raw.get("transactionHash", ""),
    }


async def ingest_trades(trades_data: list[dict]) -> tuple[int, list]:
    """Insert only NEW trades (newer than _last_seen_timestamp)."""
    global _last_seen_timestamp
    if not trades_data:
        return 0, []

    # Filter to only trades newer than what we've seen
    new_candidates = [t for t in trades_data if t["ts_unix"] > _last_seen_timestamp]
    if not new_candidates:
        return 0, []

    inserted = 0
    new_trades = []

    async with async_session() as session:
        # Cache known markets
        market_result = await session.execute(select(Market.condition_id))
        known_markets = {row[0] for row in market_result}

        for data in new_candidates:
            if data["market_id"] not in known_markets:
                continue

            # Remove internal fields before DB insert
            ts_unix = data.pop("ts_unix", 0)
            tx_hash = data.pop("tx_hash", "")

            trade = Trade(**data)
            session.add(trade)
            new_trades.append(trade)
            inserted += 1

            # Track max timestamp
            if ts_unix > _last_seen_timestamp:
                _last_seen_timestamp = ts_unix

        if inserted > 0:
            await session.commit()
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
    await _init_last_seen()

    async with httpx.AsyncClient(timeout=20) as client:
        raw_trades = await fetch_recent_trades(client, limit=200)

    parsed = [parse_trade(t) for t in raw_trades]
    parsed = [t for t in parsed if t is not None]
    inserted, new_trades = await ingest_trades(parsed)

    if inserted > 0:
        log.info("trades_ingested", new=inserted, total_fetched=len(raw_trades))

        # Log and feed to detection
        from activity import log_activity
        large_trades = [t for t in new_trades if t.usd_value >= settings.min_trade_usd]

        if large_trades:
            await log_activity(
                event_type="trades_ingested",
                severity="info",
                title=f"{inserted} new trades ({len(large_trades)} large >= ${settings.min_trade_usd:,.0f})",
                detail=f"Largest: ${max(t.usd_value for t in large_trades):,.0f}",
                metadata={"inserted": inserted, "large": len(large_trades)},
            )

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
