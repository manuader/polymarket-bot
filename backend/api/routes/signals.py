"""Signal endpoints: list, filter, detail."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session
from db.models import Signal, Market

router = APIRouter(prefix="/api/signals", tags=["signals"])


@router.get("/")
async def list_signals(
    score_min: Optional[int] = Query(None, ge=1, le=10),
    score_max: Optional[int] = Query(None, ge=1, le=10),
    category: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """List signals with optional filters."""
    query = select(Signal).order_by(Signal.detected_at.desc())

    if score_min is not None:
        query = query.where(Signal.score >= score_min)
    if score_max is not None:
        query = query.where(Signal.score <= score_max)
    if status:
        query = query.where(Signal.status == status)

    # Category filter requires join with markets
    if category:
        query = query.join(Market, Signal.market_id == Market.condition_id).where(
            Market.category == category
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await session.execute(count_query)).scalar_one()

    # Paginate
    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    signals = result.scalars().all()

    out = []
    for s in signals:
        market_result = await session.execute(
            select(Market.question, Market.category, Market.slug).where(
                Market.condition_id == s.market_id
            )
        )
        m = market_result.one_or_none()

        out.append({
            "id": s.id,
            "market_id": s.market_id,
            "question": m[0] if m else "",
            "category": m[1] if m else "",
            "slug": m[2] if m else "",
            "signal_type": s.signal_type,
            "score": s.score,
            "direction": s.direction,
            "confidence": s.confidence,
            "recommendation": s.recommendation,
            "analysis": s.analysis,
            "total_suspicious_volume": s.total_suspicious_volume,
            "market_price_at_detection": s.market_price_at_detection,
            "detected_at": s.detected_at.isoformat() if s.detected_at else None,
            "status": s.status,
        })

    return {"total": total, "signals": out}


@router.get("/{signal_id}")
async def signal_detail(
    signal_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Detailed view of a single signal."""
    result = await session.execute(
        select(Signal).where(Signal.id == signal_id)
    )
    s = result.scalar_one_or_none()
    if not s:
        from fastapi import HTTPException
        raise HTTPException(404, "Signal not found")

    market_result = await session.execute(
        select(Market).where(Market.condition_id == s.market_id)
    )
    market = market_result.scalar_one_or_none()

    return {
        "id": s.id,
        "market_id": s.market_id,
        "market_question": market.question if market else "",
        "market_category": market.category if market else "",
        "market_end_date": market.end_date.isoformat() if market and market.end_date else None,
        "market_slug": market.slug if market else "",
        "signal_type": s.signal_type,
        "score": s.score,
        "direction": s.direction,
        "confidence": s.confidence,
        "recommendation": s.recommendation,
        "analysis": s.analysis,
        "trigger_wallets": s.trigger_wallets,
        "trigger_trade_ids": s.trigger_trade_ids,
        "total_suspicious_volume": s.total_suspicious_volume,
        "market_price_at_detection": s.market_price_at_detection,
        "time_to_resolution": str(s.time_to_resolution) if s.time_to_resolution else None,
        "web_context": s.web_context,
        "key_findings": s.key_findings,
        "detected_at": s.detected_at.isoformat() if s.detected_at else None,
        "status": s.status,
    }
