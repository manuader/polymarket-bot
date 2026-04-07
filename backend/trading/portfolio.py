"""
Portfolio manager: tracks balance, invested capital, circuit breaker, and concentration.
"""

from datetime import datetime, date, timezone

import structlog
from sqlalchemy import select, func, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import get_settings
from db.database import async_session
from db.models import Portfolio, PaperTrade, CircuitBreakerState, Market

log = structlog.get_logger()
settings = get_settings()


async def get_current_portfolio() -> dict:
    """Get current portfolio state. Creates initial if not exists."""
    async with async_session() as session:
        result = await session.execute(
            select(Portfolio).order_by(Portfolio.timestamp.desc()).limit(1)
        )
        portfolio = result.scalar_one_or_none()

        if not portfolio:
            # Initialize portfolio
            portfolio = Portfolio(
                balance=settings.initial_balance,
                invested=0,
                total_value=settings.initial_balance,
                total_pnl=0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
            )
            session.add(portfolio)
            await session.commit()
            await session.refresh(portfolio)

    return {
        "balance": portfolio.balance,
        "invested": portfolio.invested,
        "total_value": portfolio.total_value,
        "total_pnl": portfolio.total_pnl or 0,
        "total_trades": portfolio.total_trades or 0,
        "winning_trades": portfolio.winning_trades or 0,
        "losing_trades": portfolio.losing_trades or 0,
    }


async def save_portfolio_snapshot(data: dict):
    """Save a portfolio state snapshot."""
    async with async_session() as session:
        snapshot = Portfolio(**data)
        session.add(snapshot)
        await session.commit()


async def get_open_positions_count() -> int:
    """Get number of currently open paper trades."""
    async with async_session() as session:
        result = await session.execute(
            select(func.count(PaperTrade.id)).where(PaperTrade.status == "open")
        )
        return result.scalar_one()


async def get_open_positions() -> list[PaperTrade]:
    """Get all open paper trades."""
    async with async_session() as session:
        result = await session.execute(
            select(PaperTrade).where(PaperTrade.status == "open")
        )
        return list(result.scalars().all())


async def get_category_exposure() -> dict[str, float]:
    """Get total USD invested per market category."""
    async with async_session() as session:
        result = await session.execute(
            select(PaperTrade.category, func.sum(PaperTrade.usd_invested))
            .where(PaperTrade.status == "open")
            .group_by(PaperTrade.category)
        )
        return {cat or "unknown": float(vol) for cat, vol in result}


async def check_category_concentration(category: str, new_investment: float) -> bool:
    """Check if adding new_investment would exceed category concentration limit."""
    portfolio = await get_current_portfolio()
    total_value = portfolio["total_value"]

    if total_value <= 0:
        return False

    exposure = await get_category_exposure()
    current_in_category = exposure.get(category or "unknown", 0)
    projected = (current_in_category + new_investment) / total_value * 100

    return projected <= settings.category_concentration_max_pct


async def check_circuit_breaker() -> bool:
    """
    Check if the daily circuit breaker has been tripped.
    Returns True if trading is allowed, False if circuit breaker is active.
    """
    today = datetime.now(timezone.utc).date()
    today_dt = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

    async with async_session() as session:
        result = await session.execute(
            select(CircuitBreakerState).where(
                CircuitBreakerState.date == today_dt
            )
        )
        state = result.scalar_one_or_none()

        if state and state.is_tripped:
            return False

        # Get current portfolio value
        portfolio = await get_current_portfolio()
        current_value = portfolio["total_value"]

        if not state:
            # First check today — record starting value
            state = CircuitBreakerState(
                date=today_dt,
                starting_value=current_value,
                current_value=current_value,
                daily_pnl_pct=0,
                is_tripped=False,
            )
            session.add(state)
            await session.commit()
            return True

        # Update current value and check
        state.current_value = current_value
        if state.starting_value > 0:
            state.daily_pnl_pct = (
                (current_value - state.starting_value) / state.starting_value * 100
            )
        else:
            state.daily_pnl_pct = 0

        if state.daily_pnl_pct <= -settings.circuit_breaker_pct:
            state.is_tripped = True
            state.tripped_at = datetime.now(timezone.utc)
            log.warning(
                "circuit_breaker_tripped",
                daily_pnl_pct=round(state.daily_pnl_pct, 2),
                starting=state.starting_value,
                current=current_value,
            )
            await session.commit()
            return False

        await session.commit()
        return True


async def update_portfolio_after_trade(pnl: float, won: bool):
    """Update portfolio after closing a trade."""
    async with async_session() as session:
        # Get latest portfolio
        result = await session.execute(
            select(Portfolio).order_by(Portfolio.timestamp.desc()).limit(1)
        )
        latest = result.scalar_one_or_none()

        if not latest:
            return

        # Recalculate invested from open positions
        invested_result = await session.execute(
            select(func.coalesce(func.sum(PaperTrade.usd_invested), 0)).where(
                PaperTrade.status == "open"
            )
        )
        invested = float(invested_result.scalar_one())

        new_portfolio = Portfolio(
            balance=latest.balance + pnl,
            invested=invested,
            total_value=latest.balance + pnl + invested,
            total_pnl=(latest.total_pnl or 0) + pnl,
            total_trades=(latest.total_trades or 0) + 1,
            winning_trades=(latest.winning_trades or 0) + (1 if won else 0),
            losing_trades=(latest.losing_trades or 0) + (0 if won else 1),
        )
        session.add(new_portfolio)
        await session.commit()
