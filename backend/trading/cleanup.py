"""
Periodic cleanup of old data to prevent unbounded DB growth.
- Trades: > 24h and not linked to signals
- Volume snapshots: > 48h
- Bot activity: > 7 days
Runs every hour.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select, delete, func, and_

from db.database import async_session
from db.models import Trade, Signal, MarketVolumeSnapshot, BotActivity
from activity import log_activity

log = structlog.get_logger()

CLEANUP_INTERVAL = 3600
TRADE_RETENTION_HOURS = 24
SNAPSHOT_RETENTION_HOURS = 48
ACTIVITY_RETENTION_DAYS = 7


async def get_signal_trade_ids() -> set[int]:
    async with async_session() as session:
        result = await session.execute(select(Signal.trigger_trade_ids))
        ids = set()
        for (trade_ids,) in result:
            if trade_ids:
                ids.update(trade_ids)
        return ids


async def cleanup_old_trades():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=TRADE_RETENTION_HOURS)
    protected_ids = await get_signal_trade_ids()

    async with async_session() as session:
        if protected_ids:
            stmt = delete(Trade).where(and_(Trade.timestamp < cutoff, Trade.id.notin_(protected_ids)))
        else:
            stmt = delete(Trade).where(Trade.timestamp < cutoff)
        result = await session.execute(stmt)
        deleted = result.rowcount
        await session.commit()
    return deleted


async def cleanup_old_snapshots():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SNAPSHOT_RETENTION_HOURS)
    async with async_session() as session:
        result = await session.execute(
            delete(MarketVolumeSnapshot).where(MarketVolumeSnapshot.timestamp < cutoff)
        )
        deleted = result.rowcount
        await session.commit()
    return deleted


async def cleanup_old_activity():
    cutoff = datetime.now(timezone.utc) - timedelta(days=ACTIVITY_RETENTION_DAYS)
    async with async_session() as session:
        result = await session.execute(
            delete(BotActivity).where(BotActivity.timestamp < cutoff)
        )
        deleted = result.rowcount
        await session.commit()
    return deleted


async def run_cleanup(interval_seconds: int = CLEANUP_INTERVAL):
    log.info("cleanup_starting", interval=interval_seconds)
    await asyncio.sleep(300)
    while True:
        try:
            trades_del = await cleanup_old_trades()
            snaps_del = await cleanup_old_snapshots()
            activity_del = await cleanup_old_activity()
            total = trades_del + snaps_del + activity_del
            if total > 0:
                log.info("cleanup_done", trades=trades_del, snapshots=snaps_del, activity=activity_del)
                await log_activity(
                    event_type="cleanup",
                    severity="info",
                    title=f"Cleanup: {trades_del} trades, {snaps_del} snapshots, {activity_del} activity logs",
                    metadata={"trades": trades_del, "snapshots": snaps_del, "activity": activity_del},
                )
        except Exception as e:
            log.error("cleanup_error", error=str(e))
        await asyncio.sleep(interval_seconds)
