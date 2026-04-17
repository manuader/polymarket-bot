"""Signal endpoints: list, filter, detail, retry AI."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session
from db.models import Signal, Market, Wallet

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


class RetryAIRequest(BaseModel):
    signal_id: Optional[int] = None
    market_id: Optional[str] = None


@router.post("/retry-ai")
async def retry_ai_analysis(
    body: RetryAIRequest,
    session: AsyncSession = Depends(get_session),
):
    """Re-run AI analysis for a signal that failed or wasn't analyzed."""
    from detection.ai_analyzer import analyze_with_ai
    from detection.signal_manager import compute_composite_score
    from detection.heuristic_filter import RuleHit
    from config import get_settings
    from activity import log_activity

    settings = get_settings()

    # Find signal
    if body.signal_id:
        result = await session.execute(select(Signal).where(Signal.id == body.signal_id))
        signal = result.scalar_one_or_none()
    elif body.market_id:
        result = await session.execute(
            select(Signal)
            .where(Signal.market_id == body.market_id)
            .order_by(Signal.detected_at.desc())
            .limit(1)
        )
        signal = result.scalar_one_or_none()
    else:
        raise HTTPException(400, "Provide signal_id or market_id")

    if not signal:
        raise HTTPException(404, "Signal not found")

    # Load market
    market_result = await session.execute(
        select(Market).where(Market.condition_id == signal.market_id)
    )
    market = market_result.scalar_one_or_none()
    if not market:
        raise HTTPException(404, "Market not found")

    # Load wallets
    wallets = []
    if signal.trigger_wallets:
        for addr in signal.trigger_wallets:
            w_result = await session.execute(select(Wallet).where(Wallet.address == addr))
            w = w_result.scalar_one_or_none()
            if w:
                wallets.append(w)

    # Reconstruct RuleHit objects from signal data
    rule_names = signal.signal_type.split("+") if signal.signal_type else ["UNKNOWN"]
    hits = []
    for rn in rule_names:
        hits.append(RuleHit(
            rule_name=rn,
            priority=signal.score or 5,
            market_id=signal.market_id,
            direction=signal.direction or "YES",
            trigger_wallets=signal.trigger_wallets or [],
            trigger_trade_ids=signal.trigger_trade_ids or [],
            total_suspicious_volume=signal.total_suspicious_volume or 0,
        ))

    # Run AI analysis
    ai_result = await analyze_with_ai(hits, market, wallets)
    if not ai_result:
        raise HTTPException(502, "AI analysis failed — check logs for details")

    # Recompute composite score with AI result
    score, confidence, recommendation = compute_composite_score(hits, ai_result)

    # Update signal
    signal.score = score
    signal.confidence = confidence
    signal.recommendation = recommendation
    signal.analysis = ai_result.get("investigation_report", ai_result.get("reasoning", ""))
    signal.key_findings = ai_result.get("key_findings", [])
    event = ai_result.get("upcoming_event")
    event_date = ai_result.get("upcoming_event_date")
    if event:
        signal.web_context = f"Upcoming event: {event}"
        if event_date:
            signal.web_context += f" (date: {event_date})"

    await session.commit()

    # Open paper trade if score is high enough
    if signal.score >= settings.min_score_to_trade:
        from trading.paper_engine import process_signal
        await process_signal(signal)

    return {
        "signal_id": signal.id,
        "score": score,
        "confidence": confidence,
        "recommendation": recommendation,
    }
