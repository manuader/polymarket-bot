"""Analytics endpoints: performance metrics, breakdowns, distributions."""

from fastapi import APIRouter

from trading.stats_tracker import (
    get_performance_summary,
    get_pnl_by_category,
    get_pnl_by_score,
    get_equity_curve,
    get_return_distribution,
    compute_max_drawdown,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/performance")
async def performance():
    """Comprehensive performance metrics."""
    return await get_performance_summary()


@router.get("/by-category")
async def by_category():
    """P&L breakdown by market category."""
    return await get_pnl_by_category()


@router.get("/by-score")
async def by_score():
    """P&L breakdown by signal score bracket."""
    return await get_pnl_by_score()


@router.get("/return-distribution")
async def return_distribution():
    """All trade P&L percentages for histogram."""
    return await get_return_distribution()


@router.get("/drawdown")
async def drawdown():
    """Current max drawdown percentage."""
    dd = await compute_max_drawdown()
    return {"max_drawdown_pct": round(dd, 2)}
