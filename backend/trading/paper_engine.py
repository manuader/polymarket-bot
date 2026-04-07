"""
Paper Trading Engine: simulates trades based on detection signals.

Entry: when signal score >= MIN_SCORE_TO_TRADE
Exit: 6 conditions (resolution, trailing stop, dynamic stop loss,
      take profit, near-resolution, time decay)
"""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from sqlalchemy import select

from config import get_settings
from db.database import async_session
from db.models import Signal, PaperTrade, Market
from trading.position_sizer import calculate_position_size, update_bracket_stats
from trading.portfolio import (
    get_current_portfolio,
    get_open_positions,
    get_open_positions_count,
    check_circuit_breaker,
    check_category_concentration,
    update_portfolio_after_trade,
)
from trading.slippage_model import compute_entry_price, compute_exit_price

log = structlog.get_logger()
settings = get_settings()

POSITION_CHECK_INTERVAL = 60  # seconds


async def open_position(signal: Signal, market: Market) -> PaperTrade | None:
    """Attempt to open a paper trade based on a signal."""
    # Pre-checks
    if signal.score < settings.min_score_to_trade:
        return None

    if not await check_circuit_breaker():
        log.warning("paper_trade_blocked_circuit_breaker")
        return None

    open_count = await get_open_positions_count()
    if open_count >= settings.max_positions:
        log.info("paper_trade_blocked_max_positions", open=open_count)
        return None

    portfolio = await get_current_portfolio()
    if portfolio["balance"] < 50:  # Minimum trade
        log.info("paper_trade_blocked_insufficient_balance", balance=portfolio["balance"])
        return None

    # Category concentration check
    category = market.category or "unknown"
    confidence = signal.confidence or 0.5
    usd_amount = await calculate_position_size(
        score=signal.score,
        confidence=confidence,
        portfolio_value=portfolio["total_value"],
        entry_price=signal.market_price_at_detection or 0.5,
    )

    if usd_amount <= 0:
        return None

    if not await check_category_concentration(category, usd_amount):
        log.info("paper_trade_blocked_concentration", category=category)
        return None

    # Cap to available balance
    usd_amount = min(usd_amount, portfolio["balance"])

    # Compute entry price with dynamic slippage
    market_price = signal.market_price_at_detection or 0.5
    token_id = ""
    if market.clob_token_ids:
        if signal.direction == "YES" and len(market.clob_token_ids) > 0:
            token_id = market.clob_token_ids[0]
        elif signal.direction == "NO" and len(market.clob_token_ids) > 1:
            token_id = market.clob_token_ids[1]

    entry_price = compute_entry_price(market_price, token_id, usd_amount)
    shares = usd_amount / entry_price if entry_price > 0 else 0

    if shares <= 0:
        return None

    # Create paper trade
    async with async_session() as session:
        trade = PaperTrade(
            signal_id=signal.id,
            market_id=market.condition_id,
            direction=signal.direction or "YES",
            entry_price=entry_price,
            size=shares,
            usd_invested=usd_amount,
            status="open",
            score_at_entry=signal.score,
            confidence_at_entry=confidence,
            category=category,
            highest_price_since_entry=entry_price,
            trailing_stop_price=None,
        )
        session.add(trade)

        # Update portfolio balance
        from db.models import Portfolio
        port_result = await session.execute(
            select(Portfolio).order_by(Portfolio.timestamp.desc()).limit(1)
        )
        latest_port = port_result.scalar_one_or_none()
        if latest_port:
            new_port = Portfolio(
                balance=latest_port.balance - usd_amount,
                invested=(latest_port.invested or 0) + usd_amount,
                total_value=latest_port.total_value,  # unchanged, just rebalanced
                total_pnl=latest_port.total_pnl,
                total_trades=latest_port.total_trades,
                winning_trades=latest_port.winning_trades,
                losing_trades=latest_port.losing_trades,
            )
            session.add(new_port)

        await session.commit()
        await session.refresh(trade)

    log.warning(
        "paper_trade_opened",
        trade_id=trade.id,
        market=market.condition_id[:12],
        direction=signal.direction,
        entry_price=round(entry_price, 4),
        usd=round(usd_amount, 2),
        shares=round(shares, 2),
        score=signal.score,
    )

    return trade


async def get_current_price(market: Market, direction: str) -> float | None:
    """Get current price for a market outcome."""
    if market.outcome_prices:
        if direction == "YES" and len(market.outcome_prices) > 0:
            return market.outcome_prices[0]
        elif direction == "NO" and len(market.outcome_prices) > 1:
            return market.outcome_prices[1]
    return None


async def close_position(trade: PaperTrade, exit_price: float, reason: str):
    """Close a paper trade and update portfolio."""
    usd_returned = trade.size * exit_price
    pnl = usd_returned - trade.usd_invested
    pnl_pct = (pnl / trade.usd_invested * 100) if trade.usd_invested > 0 else 0
    won = pnl > 0

    async with async_session() as session:
        result = await session.execute(
            select(PaperTrade).where(PaperTrade.id == trade.id)
        )
        db_trade = result.scalar_one_or_none()
        if not db_trade or db_trade.status == "closed":
            return

        db_trade.exit_price = exit_price
        db_trade.usd_returned = usd_returned
        db_trade.pnl = pnl
        db_trade.pnl_pct = pnl_pct
        db_trade.exit_reason = reason
        db_trade.status = "closed"
        db_trade.closed_at = datetime.now(timezone.utc)
        await session.commit()

    # Update portfolio
    await update_portfolio_after_trade(pnl, won)

    # Update bracket stats for Kelly calibration
    if trade.score_at_entry:
        await update_bracket_stats(trade.score_at_entry, pnl, pnl_pct, won)

    # Update signal status
    if trade.signal_id:
        async with async_session() as session:
            result = await session.execute(
                select(Signal).where(Signal.id == trade.signal_id)
            )
            signal = result.scalar_one_or_none()
            if signal:
                signal.status = "resolved_win" if won else "resolved_loss"
                await session.commit()

    log.warning(
        "paper_trade_closed",
        trade_id=trade.id,
        reason=reason,
        exit_price=round(exit_price, 4),
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 2),
        won=won,
    )


async def check_exit_conditions(trade: PaperTrade):
    """
    Check all 6 exit conditions for an open position.

    1. Market resolution → $1.00 or $0.00
    2. Trailing stop: profit > 40% → stop at breakeven
    3. Dynamic stop loss: -30% (score >= 8), -50% (score < 8)
    4. Take profit: 80% of max potential
    5. Near-resolution: < 30 min + in profit → close
    6. Time decay: > 14 days open → close
    """
    async with async_session() as session:
        result = await session.execute(
            select(Market).where(Market.condition_id == trade.market_id)
        )
        market = result.scalar_one_or_none()

    if not market:
        return

    current_price = await get_current_price(market, trade.direction)
    if current_price is None:
        return

    entry = trade.entry_price
    now = datetime.now(timezone.utc)

    # ── Condition 1: Market Resolution ──
    if not market.active:
        # Market resolved
        if current_price > 0.95:
            await close_position(trade, 1.0, "resolution_win")
        elif current_price < 0.05:
            await close_position(trade, 0.0, "resolution_loss")
        else:
            await close_position(trade, current_price, "resolution")
        return

    # ── Update trailing stop tracking ──
    if current_price > (trade.highest_price_since_entry or entry):
        async with async_session() as session:
            result = await session.execute(
                select(PaperTrade).where(PaperTrade.id == trade.id)
            )
            db_trade = result.scalar_one_or_none()
            if db_trade:
                db_trade.highest_price_since_entry = current_price

                # Condition 2: Activate trailing stop once profit > 40%
                profit_pct = (current_price - entry) / entry * 100 if entry > 0 else 0
                if profit_pct >= settings.trailing_stop_trigger_pct:
                    db_trade.trailing_stop_price = entry  # Stop at breakeven
                await session.commit()
                trade.highest_price_since_entry = current_price
                trade.trailing_stop_price = db_trade.trailing_stop_price

    # ── Condition 2: Trailing Stop ──
    if trade.trailing_stop_price and current_price <= trade.trailing_stop_price:
        await close_position(trade, current_price, "trailing_stop")
        return

    # ── Condition 3: Dynamic Stop Loss ──
    score = trade.score_at_entry or 5
    stop_loss_pct = settings.stop_loss_pct_high if score >= 8 else settings.stop_loss_pct_low
    stop_price = entry * (1 - stop_loss_pct / 100)
    if current_price <= stop_price:
        await close_position(trade, current_price, "stop_loss")
        return

    # ── Condition 4: Take Profit ──
    max_potential = 1.0 - entry  # Max profit per share
    take_profit_threshold = entry + max_potential * (settings.take_profit_pct / 100)
    if current_price >= take_profit_threshold:
        await close_position(trade, current_price, "take_profit")
        return

    # ── Condition 5: Near-Resolution Profit Capture ──
    if market.end_date:
        end = market.end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        time_remaining = end - now
        if time_remaining.total_seconds() < 1800 and current_price > entry:  # < 30 min
            await close_position(trade, current_price, "near_resolution")
            return

    # ── Condition 6: Time Decay ──
    if trade.opened_at:
        opened = trade.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        days_open = (now - opened).days
        if days_open > 14:
            await close_position(trade, current_price, "time_decay")
            return


async def process_signal(signal: Signal):
    """Attempt to open a paper trade from a new signal."""
    if signal.score < settings.min_score_to_trade:
        return

    async with async_session() as session:
        result = await session.execute(
            select(Market).where(Market.condition_id == signal.market_id)
        )
        market = result.scalar_one_or_none()

    if not market:
        return

    await open_position(signal, market)


async def check_all_positions():
    """Check exit conditions for all open positions."""
    positions = await get_open_positions()
    for pos in positions:
        try:
            await check_exit_conditions(pos)
        except Exception as e:
            log.error("position_check_error", trade_id=pos.id, error=str(e))


async def run_paper_engine():
    """Main paper trading loop: checks positions every minute."""
    log.info("paper_engine_starting")
    while True:
        try:
            await check_all_positions()
        except Exception as e:
            log.error("paper_engine_error", error=str(e))
        await asyncio.sleep(POSITION_CHECK_INTERVAL)
