"""
Periodic cleanup of old trades that aren't linked to any signal.
Keeps trades referenced by signals for learning/analysis.
Runs every hour.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select, delete, func, and_

from db.database import async_session
from db.models import Trade, Signal
from activity import log_activity

log = structlog.get_logger()

CLEANUP_INTERVAL = 3600  # 1 hour
RETENTION_HOURS = 24     # keep non-signal trades for 24h


async def get_signal_trade_ids() -> set[int]:
    """Get trade IDs that are referenced by signals (must be kept)."""
    async with async_session() as session:
        result = await session.execute(select(Signal.trigger_trade_ids))
        ids = set()
        for (trade_ids,) in result:
            if trade_ids:
                ids.update(trade_ids)
        return ids


async def cleanup_old_trades():
    """Delete trades older than retention period that aren't linked to signals."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=RETENTION_HOURS)

    # Get IDs we must keep
    protected_ids = await get_signal_trade_ids()

    async with async_session() as session:
        # Count before
        total_before = (await session.execute(select(func.count(Trade.id)))).scalar_one()

        # Delete old trades not in protected set
        if protected_ids:
            stmt = delete(Trade).where(
                and_(
                    Trade.timestamp < cutoff,
                    Trade.id.notin_(protected_ids),
                )
            )
        else:
            stmt = delete(Trade).where(Trade.timestamp < cutoff)

        result = await session.execute(stmt)
        deleted = result.rowcount
        await session.commit()

        total_after = (await session.execute(select(func.count(Trade.id)))).scalar_one()

    if deleted > 0:
        log.info("trade_cleanup", deleted=deleted, remaining=total_after, protected=len(protected_ids))
        await log_activity(
            event_type="cleanup",
            severity="info",
            title=f"Cleaned {deleted} old trades (kept {total_after})",
            detail=f"Deleted trades older than {RETENTION_HOURS}h. Protected {len(protected_ids)} signal-linked trades.",
            metadata={"deleted": deleted, "remaining": total_after, "protected": len(protected_ids)},
        )

    return deleted


async def run_cleanup(interval_seconds: int = CLEANUP_INTERVAL):
    """Run cleanup loop."""
    log.info("cleanup_starting", interval=interval_seconds)
    # Wait a bit before first cleanup
    await asyncio.sleep(300)
    while True:
        try:
            await cleanup_old_trades()
        except Exception as e:
            log.error("cleanup_error", error=str(e))
        await asyncio.sleep(interval_seconds)
