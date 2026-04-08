"""
Builds wallet profiles from Polymarket's Data API (real on-chain data).
NOT from our local DB (which is incomplete and biased).

Uses:
- /activity?user=X — full trade history (all trades, not just large ones)
- /positions?user=X — current/past positions with P&L

Profiles are fetched on-demand when a trade triggers heuristic rules,
then cached in the wallets table for reuse.
"""

import asyncio
from datetime import datetime, timezone
from collections import Counter

import httpx
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import get_settings
from db.database import async_session
from db.models import Wallet

log = structlog.get_logger()
settings = get_settings()

DATA_API_URL = settings.data_api_url

# In-memory cache to avoid re-fetching recently profiled wallets
_profile_cache: dict[str, float] = {}  # address -> timestamp of last fetch
CACHE_TTL = 3600  # re-fetch profile after 1 hour


async def fetch_wallet_activity(
    client: httpx.AsyncClient,
    address: str,
    limit: int = 5000,
) -> list[dict]:
    """Fetch full activity history for a wallet from Data API."""
    try:
        resp = await client.get(
            f"{DATA_API_URL}/activity",
            params={"user": address, "limit": limit},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning("wallet_activity_fetch_error", address=address[:12], error=str(e))
        return []


async def fetch_wallet_positions(
    client: httpx.AsyncClient,
    address: str,
    limit: int = 1000,
) -> list[dict]:
    """Fetch positions (with P&L) for a wallet from Data API."""
    try:
        resp = await client.get(
            f"{DATA_API_URL}/positions",
            params={"user": address, "limit": limit},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning("wallet_positions_fetch_error", address=address[:12], error=str(e))
        return []


def build_profile(
    address: str,
    activities: list[dict],
    positions: list[dict],
) -> dict:
    """Build a comprehensive wallet profile from real API data."""
    # --- Activity analysis ---
    trades = [a for a in activities if a.get("type") == "TRADE"]
    total_trades = len(trades)

    # First seen = earliest activity timestamp
    first_seen = datetime.now(timezone.utc)
    if activities:
        timestamps = [a.get("timestamp", 0) for a in activities if a.get("timestamp")]
        if timestamps:
            earliest = min(timestamps)
            try:
                first_seen = datetime.fromtimestamp(int(earliest), tz=timezone.utc)
            except (ValueError, OSError):
                pass

    # Total volume (USDC)
    total_volume = sum(float(a.get("usdcSize", 0) or 0) for a in trades)

    # Average trade size
    avg_trade_size = total_volume / total_trades if total_trades > 0 else 0

    # Unique markets traded
    market_ids = set(a.get("conditionId", "") for a in trades if a.get("conditionId"))
    markets_traded = len(market_ids)

    # --- Position/P&L analysis ---
    wins = 0
    losses = 0
    for pos in positions:
        pnl = float(pos.get("cashPnl", 0) or 0)
        # Only count resolved positions (redeemable or with clear P&L)
        size = float(pos.get("size", 0) or 0)
        if size == 0 and pnl != 0:
            # Position is closed
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
        elif pos.get("redeemable"):
            # Market resolved
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

    win_rate = None
    if wins + losses > 0:
        win_rate = wins / (wins + losses)

    # --- Category/topic analysis (stored in metadata) ---
    titles = [a.get("title", "") for a in trades if a.get("title")]
    # Find common market slugs to identify betting patterns
    event_slugs = [a.get("eventSlug", "") for a in trades if a.get("eventSlug")]
    slug_counts = Counter(event_slugs)
    top_topics = [slug for slug, _ in slug_counts.most_common(5)]

    return {
        "address": address,
        "first_seen": first_seen,
        "total_trades": total_trades,
        "total_volume": total_volume,
        "markets_traded": markets_traded,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_trade_size": avg_trade_size,
        # These will be useful for detection rules and AI analysis
        "_top_topics": top_topics,
        "_total_positions": len(positions),
        "_open_positions": len([p for p in positions if float(p.get("size", 0) or 0) > 0]),
    }


async def profile_wallet_from_api(address: str, force: bool = False) -> dict | None:
    """
    Fetch and build a full wallet profile from Polymarket's Data API.
    Results are cached in the wallets DB table.

    This is called ON-DEMAND when a trade triggers heuristic rules,
    not on a periodic loop scanning all addresses.
    """
    if not address:
        return None

    # Check in-memory cache
    import time
    now = time.monotonic()
    if not force and address in _profile_cache:
        if now - _profile_cache[address] < CACHE_TTL:
            # Return from DB
            return await get_wallet(address)

    async with httpx.AsyncClient(timeout=20) as client:
        activities, positions = await asyncio.gather(
            fetch_wallet_activity(client, address),
            fetch_wallet_positions(client, address),
        )

    if not activities and not positions:
        # No data found — wallet might be brand new or API issue
        profile = {
            "address": address,
            "first_seen": datetime.now(timezone.utc),
            "total_trades": 0,
            "total_volume": 0,
            "markets_traded": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "avg_trade_size": 0,
        }
    else:
        profile = build_profile(address, activities, positions)

    # Extra metadata for detection (not stored in DB but returned)
    extra = {
        "top_topics": profile.pop("_top_topics", []),
        "total_positions": profile.pop("_total_positions", 0),
        "open_positions": profile.pop("_open_positions", 0),
    }

    # Save to DB
    await upsert_wallet(profile)
    _profile_cache[address] = now

    log.info(
        "wallet_profiled",
        address=address[:12],
        trades=profile["total_trades"],
        volume=f"${profile['total_volume']:,.0f}",
        win_rate=f"{profile['win_rate']:.0%}" if profile["win_rate"] is not None else "N/A",
        age_days=(datetime.now(timezone.utc) - profile["first_seen"]).days,
    )

    # Return profile + extra metadata
    return {**profile, **extra}


async def upsert_wallet(data: dict):
    """Insert or update a wallet profile in the DB."""
    async with async_session() as session:
        stmt = pg_insert(Wallet).values(
            address=data["address"],
            first_seen=data["first_seen"],
            total_trades=data["total_trades"],
            total_volume=data["total_volume"],
            markets_traded=data["markets_traded"],
            wins=data["wins"],
            losses=data["losses"],
            win_rate=data["win_rate"],
            avg_trade_size=data["avg_trade_size"],
            updated_at=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["address"],
            set_={
                "total_trades": stmt.excluded.total_trades,
                "total_volume": stmt.excluded.total_volume,
                "markets_traded": stmt.excluded.markets_traded,
                "wins": stmt.excluded.wins,
                "losses": stmt.excluded.losses,
                "win_rate": stmt.excluded.win_rate,
                "avg_trade_size": stmt.excluded.avg_trade_size,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        await session.execute(stmt)
        await session.commit()


async def get_wallet(address: str) -> dict | None:
    """Get a wallet profile from the database."""
    from sqlalchemy import select
    async with async_session() as session:
        result = await session.execute(
            select(Wallet).where(Wallet.address == address)
        )
        w = result.scalar_one_or_none()
        if not w:
            return None
        return {
            "address": w.address,
            "first_seen": w.first_seen,
            "total_trades": w.total_trades,
            "total_volume": w.total_volume,
            "markets_traded": w.markets_traded,
            "wins": w.wins,
            "losses": w.losses,
            "win_rate": w.win_rate,
            "avg_trade_size": w.avg_trade_size,
            "is_flagged_hashdive": w.is_flagged_hashdive,
        }


# No more periodic background loop — profiling is on-demand
async def run_wallet_profiler(interval_seconds: int = 120):
    """Kept for compatibility but now does nothing.
    Wallet profiling is on-demand via profile_wallet_from_api()."""
    log.info("wallet_profiler_mode", mode="on-demand (API-based)")
    while True:
        await asyncio.sleep(interval_seconds)
