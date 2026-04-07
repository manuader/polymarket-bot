"""
Builds and maintains wallet profiles from ingested trades.
Aggregates: total trades, volume, markets traded, win rate.
Used by heuristic filter to identify suspicious wallets.
"""

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, func, and_, distinct

from db.database import async_session
from db.models import Trade, Wallet, Market

log = structlog.get_logger()

PROFILE_INTERVAL = 120  # seconds between full profile rebuilds


async def profile_wallet(address: str) -> dict:
    """Compute full profile for a single wallet address."""
    async with async_session() as session:
        # Basic aggregates
        result = await session.execute(
            select(
                func.count(Trade.id),
                func.coalesce(func.sum(Trade.usd_value), 0),
                func.count(distinct(Trade.market_id)),
                func.min(Trade.timestamp),
                func.coalesce(func.avg(Trade.usd_value), 0),
            ).where(
                (Trade.taker_address == address) | (Trade.maker_address == address)
            )
        )
        row = result.one()
        total_trades = int(row[0])
        total_volume = float(row[1])
        markets_traded = int(row[2])
        first_seen = row[3]
        avg_trade_size = float(row[4])

        # Win/loss tracking: check resolved markets where wallet had positions
        # A "win" = wallet bought YES on a market that resolved YES, or NO on NO
        # For now, count markets where wallet bet on the winning side
        wins = 0
        losses = 0

        resolved_markets = await session.execute(
            select(Trade.market_id, Trade.outcome)
            .where(
                (Trade.taker_address == address) | (Trade.maker_address == address)
            )
            .group_by(Trade.market_id, Trade.outcome)
        )

        for market_id, outcome in resolved_markets:
            market = await session.execute(
                select(Market.active, Market.outcome_prices)
                .where(Market.condition_id == market_id)
            )
            market_row = market.one_or_none()
            if not market_row:
                continue

            active, prices = market_row
            if active:
                continue  # Not resolved yet

            # If market is inactive and YES price is ~1.0, YES won
            if prices and len(prices) >= 1:
                yes_price = prices[0]
                if yes_price > 0.95:  # YES won
                    if outcome and outcome.upper() == "YES":
                        wins += 1
                    else:
                        losses += 1
                elif yes_price < 0.05:  # NO won
                    if outcome and outcome.upper() == "NO":
                        wins += 1
                    else:
                        losses += 1

        win_rate = None
        if wins + losses > 0:
            win_rate = wins / (wins + losses)

        return {
            "address": address,
            "first_seen": first_seen or datetime.now(timezone.utc),
            "total_trades": total_trades,
            "total_volume": total_volume,
            "markets_traded": markets_traded,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "avg_trade_size": avg_trade_size,
        }


async def upsert_wallet(data: dict):
    """Insert or update a wallet profile."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with async_session() as session:
        stmt = pg_insert(Wallet).values(**data)
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


async def get_wallet(address: str) -> Wallet | None:
    """Get a wallet profile from the database."""
    async with async_session() as session:
        result = await session.execute(
            select(Wallet).where(Wallet.address == address)
        )
        return result.scalar_one_or_none()


async def get_distinct_addresses() -> list[str]:
    """Get all unique wallet addresses from recent trades."""
    async with async_session() as session:
        takers = await session.execute(
            select(distinct(Trade.taker_address)).where(
                Trade.taker_address.isnot(None),
                Trade.taker_address != "",
            )
        )
        makers = await session.execute(
            select(distinct(Trade.maker_address)).where(
                Trade.maker_address.isnot(None),
                Trade.maker_address != "",
            )
        )
        addresses = set()
        for (addr,) in takers:
            if addr:
                addresses.add(addr)
        for (addr,) in makers:
            if addr:
                addresses.add(addr)
        return list(addresses)


async def profile_once():
    """Run a single profiling cycle for all known addresses."""
    addresses = await get_distinct_addresses()
    updated = 0
    for addr in addresses:
        try:
            data = await profile_wallet(addr)
            await upsert_wallet(data)
            updated += 1
        except Exception as e:
            log.error("wallet_profile_error", address=addr[:10], error=str(e))

    if updated > 0:
        log.info("wallet_profiles_updated", count=updated)
    return updated


async def run_wallet_profiler(interval_seconds: int = PROFILE_INTERVAL):
    """Run wallet profiling loop."""
    log.info("wallet_profiler_starting", interval=interval_seconds)
    while True:
        try:
            await profile_once()
        except Exception as e:
            log.error("wallet_profiler_error", error=str(e))
        await asyncio.sleep(interval_seconds)
