"""Paper trade history endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session
from db.models import PaperTrade, Market

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("/")
async def list_trades(
    status: Optional[str] = None,
    category: Optional[str] = None,
    won: Optional[bool] = None,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """List paper trades with optional filters."""
    query = select(PaperTrade).order_by(PaperTrade.opened_at.desc())

    if status:
        query = query.where(PaperTrade.status == status)
    if category:
        query = query.where(PaperTrade.category == category)
    if won is True:
        query = query.where(PaperTrade.pnl > 0)
    elif won is False:
        query = query.where(PaperTrade.pnl <= 0)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await session.execute(count_query)).scalar_one()

    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    trades = result.scalars().all()

    out = []
    for t in trades:
        market_result = await session.execute(
            select(Market.question).where(Market.condition_id == t.market_id)
        )
        question = market_result.scalar_one_or_none() or ""

        out.append({
            "id": t.id,
            "signal_id": t.signal_id,
            "market_id": t.market_id,
            "question": question,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "size": t.size,
            "usd_invested": t.usd_invested,
            "usd_returned": t.usd_returned,
            "pnl": round(t.pnl, 2) if t.pnl else None,
            "pnl_pct": round(t.pnl_pct, 2) if t.pnl_pct else None,
            "exit_reason": t.exit_reason,
            "status": t.status,
            "score_at_entry": t.score_at_entry,
            "category": t.category,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        })

    return {"total": total, "trades": out}
