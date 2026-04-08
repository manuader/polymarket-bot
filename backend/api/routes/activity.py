"""Bot activity feed and AI cost tracking endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session
from db.models import BotActivity, Trade
from activity import get_ai_stats

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("/feed")
async def activity_feed(
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Recent bot activity events."""
    query = select(BotActivity).order_by(BotActivity.timestamp.desc())

    if event_type:
        query = query.where(BotActivity.event_type == event_type)
    if severity:
        query = query.where(BotActivity.severity == severity)

    query = query.limit(limit)
    result = await session.execute(query)
    events = result.scalars().all()

    import json
    out = []
    for e in events:
        metadata = None
        if e.metadata_json:
            try:
                metadata = json.loads(e.metadata_json)
            except (json.JSONDecodeError, TypeError):
                pass

        out.append({
            "id": e.id,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "event_type": e.event_type,
            "severity": e.severity,
            "title": e.title,
            "detail": e.detail,
            "market_id": e.market_id,
            "signal_id": e.signal_id,
            "trade_id": e.trade_id,
            "metadata": metadata,
        })

    return out


@router.get("/stats")
async def bot_stats(session: AsyncSession = Depends(get_session)):
    """Bot operational stats: AI costs, trade counts, activity counts."""
    ai = get_ai_stats()

    # Count trades in DB
    trade_count = (await session.execute(select(func.count(Trade.id)))).scalar_one()

    # Count activities by type
    activity_counts = {}
    result = await session.execute(
        select(BotActivity.event_type, func.count(BotActivity.id))
        .group_by(BotActivity.event_type)
    )
    for event_type, count in result:
        activity_counts[event_type] = count

    return {
        "ai": {
            "total_calls": ai["total_calls"],
            "total_input_tokens": ai["total_input_tokens"],
            "total_output_tokens": ai["total_output_tokens"],
            "estimated_cost_usd": round(ai["estimated_cost_usd"], 4),
        },
        "trades_in_db": trade_count,
        "activity_counts": activity_counts,
    }
