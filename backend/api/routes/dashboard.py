"""Dashboard endpoints: portfolio summary, equity curve, active signals, open positions."""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session
from db.models import Signal, PaperTrade, Market, Portfolio
from trading.portfolio import get_current_portfolio, get_open_positions
from trading.stats_tracker import get_equity_curve

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(session: AsyncSession = Depends(get_session)):
    """Portfolio summary + key metrics."""
    portfolio = await get_current_portfolio()

    # Active signals count
    result = await session.execute(
        select(func.count(Signal.id)).where(Signal.status == "active")
    )
    active_signals = result.scalar_one()

    # Open positions count
    result = await session.execute(
        select(func.count(PaperTrade.id)).where(PaperTrade.status == "open")
    )
    open_positions = result.scalar_one()

    # Today's P&L (closed trades today)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(func.coalesce(func.sum(PaperTrade.pnl), 0)).where(
            PaperTrade.status == "closed",
            PaperTrade.closed_at >= today,
        )
    )
    today_pnl = float(result.scalar_one())

    return {
        **portfolio,
        "active_signals": active_signals,
        "open_positions": open_positions,
        "today_pnl": round(today_pnl, 2),
    }


@router.get("/equity-curve")
async def equity_curve():
    """Portfolio value over time."""
    return await get_equity_curve()


@router.get("/active-signals")
async def active_signals(session: AsyncSession = Depends(get_session)):
    """Signals from the last 24 hours."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    result = await session.execute(
        select(Signal)
        .where(Signal.detected_at >= cutoff)
        .order_by(Signal.detected_at.desc())
        .limit(50)
    )
    signals = result.scalars().all()

    out = []
    for s in signals:
        # Get market question
        market_result = await session.execute(
            select(Market.question, Market.category).where(Market.condition_id == s.market_id)
        )
        market_row = market_result.one_or_none()

        out.append({
            "id": s.id,
            "market_id": s.market_id,
            "question": market_row[0] if market_row else "",
            "category": market_row[1] if market_row else "",
            "signal_type": s.signal_type,
            "score": s.score,
            "direction": s.direction,
            "confidence": s.confidence,
            "recommendation": s.recommendation,
            "total_suspicious_volume": s.total_suspicious_volume,
            "detected_at": s.detected_at.isoformat() if s.detected_at else None,
            "status": s.status,
        })

    return out


@router.get("/open-positions")
async def open_positions_endpoint(session: AsyncSession = Depends(get_session)):
    """All open paper trades with current P&L."""
    result = await session.execute(
        select(PaperTrade).where(PaperTrade.status == "open")
    )
    trades = result.scalars().all()

    out = []
    for t in trades:
        market_result = await session.execute(
            select(Market.question, Market.outcome_prices).where(Market.condition_id == t.market_id)
        )
        market_row = market_result.one_or_none()

        current_price = t.entry_price
        if market_row and market_row[1]:
            prices = market_row[1]
            if t.direction == "YES" and len(prices) > 0:
                current_price = prices[0]
            elif t.direction == "NO" and len(prices) > 1:
                current_price = prices[1]

        unrealized_pnl = t.size * (current_price - t.entry_price)
        unrealized_pnl_pct = (current_price - t.entry_price) / t.entry_price * 100 if t.entry_price > 0 else 0

        out.append({
            "id": t.id,
            "market_id": t.market_id,
            "question": market_row[0] if market_row else "",
            "direction": t.direction,
            "entry_price": t.entry_price,
            "current_price": current_price,
            "size": t.size,
            "usd_invested": t.usd_invested,
            "unrealized_pnl": round(unrealized_pnl, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
            "score_at_entry": t.score_at_entry,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        })

    return out
