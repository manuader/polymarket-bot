"""
Outcome tracker: monitors resolved markets and records whether
the bot's signals/trades were correct or wrong.

This data feeds back into:
1. ScoreBracketStats (Kelly calibration)
2. Activity feed (visible learning)
3. Future AI prompts (examples of past successes/failures)

Runs every 5 minutes.
"""

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, and_

from db.database import async_session
from db.models import Signal, PaperTrade, Market
from activity import log_activity

log = structlog.get_logger()

OUTCOME_CHECK_INTERVAL = 300  # 5 minutes


async def check_signal_outcomes():
    """Check active signals against resolved markets and record outcomes."""
    async with async_session() as session:
        # Find active signals whose markets have been resolved
        result = await session.execute(
            select(Signal, Market)
            .join(Market, Signal.market_id == Market.condition_id)
            .where(
                Signal.status == "active",
                Market.active == False,  # market resolved
            )
        )
        resolved = result.all()

    outcomes_recorded = 0
    for signal, market in resolved:
        # Determine if the signal was correct
        was_correct = False
        if market.outcome_prices and len(market.outcome_prices) >= 2:
            yes_price = market.outcome_prices[0]
            # YES won if price ~1.0, NO won if price ~0.0
            if yes_price > 0.90 and signal.direction == "YES":
                was_correct = True
            elif yes_price < 0.10 and signal.direction == "NO":
                was_correct = True

        new_status = "resolved_win" if was_correct else "resolved_loss"

        async with async_session() as session:
            result = await session.execute(
                select(Signal).where(Signal.id == signal.id)
            )
            sig = result.scalar_one_or_none()
            if sig and sig.status == "active":
                sig.status = new_status
                await session.commit()
                outcomes_recorded += 1

                await log_activity(
                    event_type="signal_resolved",
                    severity="alert" if was_correct else "warning",
                    title=f"Signal {'CORRECT' if was_correct else 'WRONG'}: {market.question[:60] if market.question else ''}",
                    detail=(
                        f"Score was {signal.score}, direction {signal.direction}. "
                        f"Rules: {signal.signal_type}. "
                        f"{'The bot correctly predicted this outcome.' if was_correct else 'The bot was wrong on this one — this feeds into calibration.'}"
                    ),
                    market_id=market.condition_id,
                    signal_id=signal.id,
                    metadata={
                        "score": signal.score,
                        "direction": signal.direction,
                        "was_correct": was_correct,
                        "confidence": signal.confidence,
                        "rules": signal.signal_type,
                        "recommendation": signal.recommendation,
                    },
                )

    if outcomes_recorded > 0:
        log.info("signal_outcomes_recorded", count=outcomes_recorded)

    return outcomes_recorded


async def get_learning_summary() -> dict:
    """Get a summary of what the bot has learned from past signals.
    Used to inject into future AI prompts for self-improvement."""
    async with async_session() as session:
        # Count wins/losses by rule type
        result = await session.execute(
            select(Signal.signal_type, Signal.status, Signal.score, Signal.confidence)
            .where(Signal.status.in_(["resolved_win", "resolved_loss"]))
        )
        signals = result.all()

    if not signals:
        return {"total_resolved": 0}

    wins = [s for s in signals if s[1] == "resolved_win"]
    losses = [s for s in signals if s[1] == "resolved_loss"]

    # Track which rules lead to wins vs losses
    rule_stats = {}
    for sig_type, status, score, conf in signals:
        for rule in sig_type.split("+"):
            if rule not in rule_stats:
                rule_stats[rule] = {"wins": 0, "losses": 0}
            if status == "resolved_win":
                rule_stats[rule]["wins"] += 1
            else:
                rule_stats[rule]["losses"] += 1

    # Best and worst performing rules
    for rule, stats in rule_stats.items():
        total = stats["wins"] + stats["losses"]
        stats["win_rate"] = stats["wins"] / total if total > 0 else 0
        stats["total"] = total

    return {
        "total_resolved": len(signals),
        "total_wins": len(wins),
        "total_losses": len(losses),
        "overall_win_rate": len(wins) / len(signals) if signals else 0,
        "avg_winning_score": sum(s[2] for s in wins) / len(wins) if wins else 0,
        "avg_losing_score": sum(s[2] for s in losses) / len(losses) if losses else 0,
        "rule_performance": rule_stats,
    }


async def run_outcome_tracker(interval_seconds: int = OUTCOME_CHECK_INTERVAL):
    """Run outcome tracking loop."""
    log.info("outcome_tracker_starting", interval=interval_seconds)
    while True:
        try:
            await check_signal_outcomes()
        except Exception as e:
            log.error("outcome_tracker_error", error=str(e))
        await asyncio.sleep(interval_seconds)
