"""
Position sizing using Half-Kelly Criterion with Bayesian calibration.

Instead of the SPEC's linear score/10 approach, we use Kelly to compute
mathematically optimal bet sizes, then halve it for safety (half-Kelly).

Score brackets have different base win probabilities that update as we
accumulate real performance data.
"""

from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import get_settings
from db.database import async_session
from db.models import ScoreBracketStats

log = structlog.get_logger()
settings = get_settings()

# Initial Bayesian priors: estimated win probability per score bracket
# These get updated as we accumulate real trade results
BRACKET_PRIORS = {
    "5-6": {"win_prob": 0.55, "avg_payoff_ratio": 2.0, "max_pct": 3.0},
    "7": {"win_prob": 0.65, "avg_payoff_ratio": 2.5, "max_pct": 7.0},
    "8-10": {"win_prob": 0.80, "avg_payoff_ratio": 3.0, "max_pct": 20.0},
}

# Minimum trades before we trust the observed win rate over the prior
MIN_TRADES_FOR_CALIBRATION = 10


def score_to_bracket(score: int) -> str:
    if score <= 6:
        return "5-6"
    elif score == 7:
        return "7"
    else:
        return "8-10"


async def get_bracket_stats(bracket: str) -> dict:
    """Get current stats for a score bracket, with Bayesian blending."""
    prior = BRACKET_PRIORS.get(bracket, BRACKET_PRIORS["5-6"])

    async with async_session() as session:
        result = await session.execute(
            select(ScoreBracketStats).where(ScoreBracketStats.bracket == bracket)
        )
        stats = result.scalar_one_or_none()

    if not stats or stats.total_trades < MIN_TRADES_FOR_CALIBRATION:
        return prior

    # Bayesian blend: weight observed data more as sample grows
    obs_weight = min(stats.total_trades / (MIN_TRADES_FOR_CALIBRATION * 3), 1.0)
    prior_weight = 1.0 - obs_weight

    blended_win_prob = (prior["win_prob"] * prior_weight) + (
        (stats.win_rate or prior["win_prob"]) * obs_weight
    )

    # Update payoff ratio from observed data
    avg_payoff = prior["avg_payoff_ratio"]
    if stats.avg_pnl_win and stats.avg_pnl_loss and stats.avg_pnl_loss != 0:
        observed_payoff = abs(stats.avg_pnl_win / stats.avg_pnl_loss)
        avg_payoff = (prior["avg_payoff_ratio"] * prior_weight) + (observed_payoff * obs_weight)

    return {
        "win_prob": blended_win_prob,
        "avg_payoff_ratio": avg_payoff,
        "max_pct": prior["max_pct"],
    }


def kelly_fraction(win_prob: float, payoff_ratio: float) -> float:
    """
    Full Kelly: f* = (p * b - q) / b
    where p = win probability, q = 1-p, b = payoff ratio (win/loss)

    We use HALF Kelly for safety.
    """
    if payoff_ratio <= 0:
        return 0

    q = 1 - win_prob
    full_kelly = (win_prob * payoff_ratio - q) / payoff_ratio

    # Half Kelly for safety
    half_kelly = full_kelly / 2

    return max(0, half_kelly)


async def calculate_position_size(
    score: int,
    confidence: float,
    portfolio_value: float,
    entry_price: float,
) -> float:
    """
    Calculate the USD amount to invest based on Kelly Criterion.

    Args:
        score: Signal score (1-10)
        confidence: AI confidence (0.0-1.0)
        portfolio_value: Current total portfolio value in USD
        entry_price: Expected entry price (0.01-0.99)

    Returns:
        USD amount to invest
    """
    if score < settings.min_score_to_trade:
        return 0

    bracket = score_to_bracket(score)
    stats = await get_bracket_stats(bracket)

    # Kelly fraction
    kf = kelly_fraction(stats["win_prob"], stats["avg_payoff_ratio"])

    # Apply confidence multiplier: lower confidence = smaller bet
    kf *= max(0.3, confidence)  # Floor at 30% of Kelly

    # Convert to portfolio percentage
    pct = kf * 100

    # Cap at bracket maximum
    pct = min(pct, stats["max_pct"])

    # Cap at global maximum
    pct = min(pct, settings.max_position_pct)

    # Calculate USD amount
    usd_amount = portfolio_value * (pct / 100)

    # Minimum trade size
    min_trade = 50
    if usd_amount < min_trade:
        return 0

    log.info(
        "position_sized",
        score=score,
        bracket=bracket,
        confidence=confidence,
        kelly_fraction=round(kf, 4),
        pct=round(pct, 2),
        usd=round(usd_amount, 2),
    )

    return round(usd_amount, 2)


async def update_bracket_stats(
    score: int,
    pnl: float,
    pnl_pct: float,
    won: bool,
):
    """Update score bracket statistics after a trade closes."""
    bracket = score_to_bracket(score)

    async with async_session() as session:
        result = await session.execute(
            select(ScoreBracketStats).where(ScoreBracketStats.bracket == bracket)
        )
        stats = result.scalar_one_or_none()

        if not stats:
            stats = ScoreBracketStats(
                bracket=bracket,
                total_trades=0,
                wins=0,
                losses=0,
            )
            session.add(stats)

        stats.total_trades += 1
        if won:
            stats.wins += 1
        else:
            stats.losses += 1

        if stats.total_trades > 0:
            stats.win_rate = stats.wins / stats.total_trades

        # Update average P&L
        if won:
            if stats.avg_pnl_win:
                stats.avg_pnl_win = (stats.avg_pnl_win * (stats.wins - 1) + pnl_pct) / stats.wins
            else:
                stats.avg_pnl_win = pnl_pct
        else:
            if stats.avg_pnl_loss:
                stats.avg_pnl_loss = (stats.avg_pnl_loss * (stats.losses - 1) + pnl_pct) / stats.losses
            else:
                stats.avg_pnl_loss = pnl_pct

        # Profit factor
        if stats.avg_pnl_loss and stats.avg_pnl_loss != 0 and stats.losses > 0:
            total_wins = (stats.avg_pnl_win or 0) * stats.wins
            total_losses = abs(stats.avg_pnl_loss) * stats.losses
            stats.profit_factor = total_wins / total_losses if total_losses > 0 else None

        stats.updated_at = datetime.now(timezone.utc)
        await session.commit()

        log.info(
            "bracket_stats_updated",
            bracket=bracket,
            total=stats.total_trades,
            win_rate=round(stats.win_rate, 3) if stats.win_rate else None,
            profit_factor=round(stats.profit_factor, 2) if stats.profit_factor else None,
        )
