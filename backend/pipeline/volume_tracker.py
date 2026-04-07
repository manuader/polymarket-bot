"""
Tracks volume aggregates per market using sliding time windows (1h, 4h, 24h).
Stores periodic snapshots in market_volume_snapshots for trend detection.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select, func, and_

from db.database import async_session
from db.models import Trade, Market, MarketVolumeSnapshot

log = structlog.get_logger()

SNAPSHOT_INTERVAL = 60  # seconds between snapshot calculations


async def compute_volume_snapshot(market_id: str) -> dict | None:
    """Compute volume metrics for a single market across time windows."""
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        # Volume and trade stats for 1h, 4h, 24h windows
        windows = {
            "1h": now - timedelta(hours=1),
            "4h": now - timedelta(hours=4),
            "24h": now - timedelta(hours=24),
        }

        volumes = {}
        for label, since in windows.items():
            result = await session.execute(
                select(
                    func.coalesce(func.sum(Trade.usd_value), 0),
                    func.count(Trade.id),
                    func.coalesce(func.avg(Trade.usd_value), 0),
                ).where(
                    and_(
                        Trade.market_id == market_id,
                        Trade.timestamp >= since,
                    )
                )
            )
            row = result.one()
            volumes[label] = {
                "volume": float(row[0]),
                "count": int(row[1]),
                "avg_size": float(row[2]),
            }

        # Price change in last hour: latest price - earliest price in window
        price_change_1h = 0.0
        prices_result = await session.execute(
            select(Trade.price, Trade.timestamp)
            .where(
                and_(
                    Trade.market_id == market_id,
                    Trade.timestamp >= windows["1h"],
                )
            )
            .order_by(Trade.timestamp.asc())
        )
        prices = prices_result.all()
        if len(prices) >= 2:
            price_change_1h = prices[-1][0] - prices[0][0]

        if volumes["1h"]["count"] == 0 and volumes["24h"]["count"] == 0:
            return None

        return {
            "market_id": market_id,
            "timestamp": now,
            "volume_1h": volumes["1h"]["volume"],
            "volume_4h": volumes["4h"]["volume"],
            "volume_24h": volumes["24h"]["volume"],
            "trade_count_1h": volumes["1h"]["count"],
            "avg_trade_size_1h": volumes["1h"]["avg_size"],
            "price_change_1h": price_change_1h,
        }


async def save_snapshot(snapshot_data: dict):
    """Save a volume snapshot to the database."""
    async with async_session() as session:
        snapshot = MarketVolumeSnapshot(**snapshot_data)
        session.add(snapshot)
        await session.commit()


async def get_active_market_ids() -> list[str]:
    """Get IDs of active markets that have recent trades."""
    async with async_session() as session:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = await session.execute(
            select(Trade.market_id)
            .where(Trade.timestamp >= cutoff)
            .group_by(Trade.market_id)
        )
        return [row[0] for row in result]


async def get_latest_snapshot(market_id: str) -> MarketVolumeSnapshot | None:
    """Get the most recent volume snapshot for a market."""
    async with async_session() as session:
        result = await session.execute(
            select(MarketVolumeSnapshot)
            .where(MarketVolumeSnapshot.market_id == market_id)
            .order_by(MarketVolumeSnapshot.timestamp.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def get_avg_24h_volume(market_id: str, lookback_days: int = 7) -> float:
    """Get the average 24h volume over the past N days for spike detection."""
    async with async_session() as session:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        result = await session.execute(
            select(func.avg(MarketVolumeSnapshot.volume_24h)).where(
                and_(
                    MarketVolumeSnapshot.market_id == market_id,
                    MarketVolumeSnapshot.timestamp >= cutoff,
                )
            )
        )
        avg = result.scalar_one_or_none()
        return float(avg) if avg else 0.0


async def snapshot_once():
    """Run a single snapshot cycle for all active markets."""
    market_ids = await get_active_market_ids()
    saved = 0
    for mid in market_ids:
        try:
            snapshot = await compute_volume_snapshot(mid)
            if snapshot:
                await save_snapshot(snapshot)
                saved += 1
        except Exception as e:
            log.error("volume_snapshot_error", market=mid, error=str(e))

    if saved > 0:
        log.info("volume_snapshots_saved", count=saved, markets=len(market_ids))
    return saved


async def run_volume_tracker(interval_seconds: int = SNAPSHOT_INTERVAL):
    """Run volume tracking loop."""
    log.info("volume_tracker_starting", interval=interval_seconds)
    while True:
        try:
            await snapshot_once()
        except Exception as e:
            log.error("volume_tracker_error", error=str(e))
        await asyncio.sleep(interval_seconds)
