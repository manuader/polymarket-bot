"""
Performance metrics tracker for the paper trading engine.
Computes: P&L, win rate, Sharpe, max drawdown, profit factor, per-category/score stats.
"""

from datetime import datetime, timedelta, timezone
from math import sqrt

import structlog
from sqlalchemy import select, func, and_

from db.database import async_session
from db.models import PaperTrade, Portfolio, ScoreBracketStats

log = structlog.get_logger()


async def get_performance_summary() -> dict:
    """Get comprehensive performance metrics."""
    async with async_session() as session:
        # Closed trades
        closed = await session.execute(
            select(PaperTrade).where(PaperTrade.status == "closed")
        )
        trades = list(closed.scalars().all())

    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_pnl_per_trade": 0,
            "profit_factor": 0,
            "sharpe_ratio": 0,
            "max_drawdown": 0,
            "best_trade": None,
            "worst_trade": None,
            "avg_duration_hours": 0,
        }

    wins = [t for t in trades if t.pnl and t.pnl > 0]
    losses = [t for t in trades if t.pnl and t.pnl <= 0]

    total_pnl = sum(t.pnl or 0 for t in trades)
    win_rate = len(wins) / len(trades) if trades else 0

    # Profit factor
    total_win_pnl = sum(t.pnl for t in wins) if wins else 0
    total_loss_pnl = abs(sum(t.pnl for t in losses)) if losses else 0
    profit_factor = total_win_pnl / total_loss_pnl if total_loss_pnl > 0 else float("inf")

    # Sharpe ratio (annualized, using daily returns)
    pnl_pcts = [t.pnl_pct or 0 for t in trades]
    if len(pnl_pcts) > 1:
        mean_return = sum(pnl_pcts) / len(pnl_pcts)
        variance = sum((r - mean_return) ** 2 for r in pnl_pcts) / (len(pnl_pcts) - 1)
        std_dev = sqrt(variance) if variance > 0 else 0
        sharpe = (mean_return / std_dev * sqrt(365)) if std_dev > 0 else 0
    else:
        sharpe = 0

    # Max drawdown from portfolio history
    max_drawdown = await compute_max_drawdown()

    # Best and worst trades
    best = max(trades, key=lambda t: t.pnl or 0)
    worst = min(trades, key=lambda t: t.pnl or 0)

    # Average duration
    durations = []
    for t in trades:
        if t.opened_at and t.closed_at:
            opened = t.opened_at
            closed = t.closed_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            if closed.tzinfo is None:
                closed = closed.replace(tzinfo=timezone.utc)
            durations.append((closed - opened).total_seconds() / 3600)
    avg_duration = sum(durations) / len(durations) if durations else 0

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(total_pnl / len(trades), 2),
        "avg_win": round(total_win_pnl / len(wins), 2) if wins else 0,
        "avg_loss": round(-total_loss_pnl / len(losses), 2) if losses else 0,
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_drawdown, 2),
        "best_trade": {
            "id": best.id,
            "market_id": best.market_id,
            "pnl": round(best.pnl, 2) if best.pnl else 0,
            "pnl_pct": round(best.pnl_pct, 2) if best.pnl_pct else 0,
        },
        "worst_trade": {
            "id": worst.id,
            "market_id": worst.market_id,
            "pnl": round(worst.pnl, 2) if worst.pnl else 0,
            "pnl_pct": round(worst.pnl_pct, 2) if worst.pnl_pct else 0,
        },
        "avg_duration_hours": round(avg_duration, 1),
    }


async def compute_max_drawdown() -> float:
    """Compute maximum drawdown from portfolio history."""
    async with async_session() as session:
        result = await session.execute(
            select(Portfolio.total_value, Portfolio.timestamp)
            .order_by(Portfolio.timestamp.asc())
        )
        values = [(float(v), t) for v, t in result]

    if not values:
        return 0

    peak = values[0][0]
    max_dd = 0

    for value, _ in values:
        if value > peak:
            peak = value
        dd = (peak - value) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    return max_dd


async def get_pnl_by_category() -> dict[str, dict]:
    """Get P&L breakdown by market category."""
    async with async_session() as session:
        result = await session.execute(
            select(
                PaperTrade.category,
                func.count(PaperTrade.id),
                func.sum(PaperTrade.pnl),
                func.avg(PaperTrade.pnl_pct),
            )
            .where(PaperTrade.status == "closed")
            .group_by(PaperTrade.category)
        )
        categories = {}
        for cat, count, total_pnl, avg_pnl_pct in result:
            categories[cat or "unknown"] = {
                "trades": int(count),
                "total_pnl": round(float(total_pnl or 0), 2),
                "avg_pnl_pct": round(float(avg_pnl_pct or 0), 2),
            }
        return categories


async def get_pnl_by_score() -> dict[str, dict]:
    """Get P&L breakdown by signal score at entry."""
    async with async_session() as session:
        result = await session.execute(
            select(ScoreBracketStats)
        )
        brackets = {}
        for stats in result.scalars():
            brackets[stats.bracket] = {
                "total_trades": stats.total_trades,
                "wins": stats.wins,
                "losses": stats.losses,
                "win_rate": round(stats.win_rate, 4) if stats.win_rate else None,
                "avg_pnl_win": round(stats.avg_pnl_win, 2) if stats.avg_pnl_win else None,
                "avg_pnl_loss": round(stats.avg_pnl_loss, 2) if stats.avg_pnl_loss else None,
                "profit_factor": round(stats.profit_factor, 2) if stats.profit_factor else None,
            }
        return brackets


async def get_equity_curve() -> list[dict]:
    """Get portfolio value over time for equity curve chart."""
    async with async_session() as session:
        result = await session.execute(
            select(Portfolio.timestamp, Portfolio.total_value, Portfolio.total_pnl)
            .order_by(Portfolio.timestamp.asc())
        )
        return [
            {
                "timestamp": ts.isoformat() if ts else None,
                "total_value": round(float(tv), 2),
                "total_pnl": round(float(pnl or 0), 2),
            }
            for ts, tv, pnl in result
        ]


async def get_return_distribution() -> list[float]:
    """Get list of all trade P&L percentages for histogram."""
    async with async_session() as session:
        result = await session.execute(
            select(PaperTrade.pnl_pct)
            .where(
                and_(
                    PaperTrade.status == "closed",
                    PaperTrade.pnl_pct.isnot(None),
                )
            )
        )
        return [round(float(pct), 2) for (pct,) in result]
