"""
Syncs market metadata from Polymarket Gamma API.
Polls every 5 minutes for active markets, updating the markets table.
"""

import asyncio
from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import get_settings
from db.database import async_session
from db.models import Market

log = structlog.get_logger()
settings = get_settings()

GAMMA_MARKETS_URL = f"{settings.gamma_api_url}/markets"
GAMMA_EVENTS_URL = f"{settings.gamma_api_url}/events"
PAGE_SIZE = 100


async def fetch_all_active_markets(client: httpx.AsyncClient) -> list[dict]:
    """Fetch all active markets from Gamma API with pagination."""
    all_markets = []
    offset = 0

    while True:
        params = {
            "active": "true",
            "closed": "false",
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        resp = await client.get(GAMMA_MARKETS_URL, params=params)
        resp.raise_for_status()
        markets = resp.json()

        if not markets:
            break

        all_markets.extend(markets)
        offset += PAGE_SIZE

        if len(markets) < PAGE_SIZE:
            break

    return all_markets


def parse_market(raw: dict) -> dict:
    """Parse a Gamma API market response into our DB schema."""
    # Extract clob token IDs from tokens array
    clob_token_ids = []
    outcome_prices = []
    tokens = raw.get("tokens", [])
    if isinstance(tokens, list):
        for t in tokens:
            if isinstance(t, dict):
                tid = t.get("token_id", "")
                if tid:
                    clob_token_ids.append(tid)
                price = t.get("price", 0)
                try:
                    outcome_prices.append(float(price))
                except (TypeError, ValueError):
                    outcome_prices.append(0.0)

    # If tokens not in expected format, try clobTokenIds field
    if not clob_token_ids:
        raw_ids = raw.get("clobTokenIds")
        if isinstance(raw_ids, str):
            clob_token_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
        elif isinstance(raw_ids, list):
            clob_token_ids = raw_ids

    # Parse outcome prices from outcomePrices string
    if not outcome_prices:
        raw_prices = raw.get("outcomePrices")
        if isinstance(raw_prices, str):
            try:
                outcome_prices = [float(x.strip()) for x in raw_prices.split(",") if x.strip()]
            except (TypeError, ValueError):
                outcome_prices = []
        elif isinstance(raw_prices, list):
            outcome_prices = [float(x) for x in raw_prices]

    end_date = None
    raw_end = raw.get("endDate") or raw.get("end_date_iso")
    if raw_end:
        try:
            end_date = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            pass

    tags = raw.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    volume = 0
    try:
        volume = float(raw.get("volume", 0) or 0)
    except (TypeError, ValueError):
        pass

    liquidity = 0
    try:
        liquidity = float(raw.get("liquidity", 0) or 0)
    except (TypeError, ValueError):
        pass

    return {
        "condition_id": raw.get("conditionId") or raw.get("condition_id", ""),
        "question": raw.get("question", ""),
        "description": raw.get("description", ""),
        "category": raw.get("category", ""),
        "end_date": end_date,
        "slug": raw.get("slug", ""),
        "tags": tags if tags else [],
        "volume": volume,
        "liquidity": liquidity,
        "active": raw.get("active", True),
        "neg_risk": raw.get("negRisk", False),
        "clob_token_ids": clob_token_ids,
        "outcome_prices": outcome_prices,
        "image": raw.get("image", ""),
    }


async def upsert_markets(markets_data: list[dict]):
    """Upsert markets into the database."""
    if not markets_data:
        return

    async with async_session() as session:
        for data in markets_data:
            if not data["condition_id"]:
                continue
            stmt = pg_insert(Market).values(**data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["condition_id"],
                set_={
                    "question": stmt.excluded.question,
                    "description": stmt.excluded.description,
                    "category": stmt.excluded.category,
                    "end_date": stmt.excluded.end_date,
                    "volume": stmt.excluded.volume,
                    "liquidity": stmt.excluded.liquidity,
                    "active": stmt.excluded.active,
                    "clob_token_ids": stmt.excluded.clob_token_ids,
                    "outcome_prices": stmt.excluded.outcome_prices,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            await session.execute(stmt)
        await session.commit()


async def get_all_token_ids() -> list[str]:
    """Return all clob_token_ids from active markets for WebSocket subscription."""
    async with async_session() as session:
        result = await session.execute(
            select(Market.clob_token_ids).where(Market.active == True)
        )
        token_ids = []
        for (ids,) in result:
            if ids:
                token_ids.extend(ids)
        return token_ids


async def sync_once():
    """Run a single sync cycle."""
    async with httpx.AsyncClient(timeout=30) as client:
        raw_markets = await fetch_all_active_markets(client)
        parsed = [parse_market(m) for m in raw_markets]
        parsed = [m for m in parsed if m["condition_id"]]
        await upsert_markets(parsed)
        log.info("market_sync_complete", count=len(parsed))
        return len(parsed)


async def run_market_sync(interval_seconds: int = 300):
    """Run market sync loop every `interval_seconds` (default 5 min)."""
    log.info("market_sync_starting", interval=interval_seconds)
    while True:
        try:
            count = await sync_once()
            log.info("market_sync_cycle", markets=count)
        except Exception as e:
            log.error("market_sync_error", error=str(e))
        await asyncio.sleep(interval_seconds)
