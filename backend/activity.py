"""
Bot activity logger — writes structured events to the bot_activity table.
These events power the frontend activity feed showing the bot's reasoning.
"""

import json
from datetime import datetime, timezone

import structlog
from db.database import async_session
from db.models import BotActivity

log = structlog.get_logger()

# Track cumulative AI costs
_ai_stats = {
    "total_calls": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "estimated_cost_usd": 0.0,
}


def get_ai_stats() -> dict:
    return dict(_ai_stats)


def record_ai_usage(input_tokens: int = 0, output_tokens: int = 0):
    """Track AI token usage and estimate cost (Sonnet pricing)."""
    _ai_stats["total_calls"] += 1
    _ai_stats["total_input_tokens"] += input_tokens
    _ai_stats["total_output_tokens"] += output_tokens
    # Sonnet pricing: $3/M input, $15/M output
    cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
    _ai_stats["estimated_cost_usd"] += cost


async def log_activity(
    event_type: str,
    title: str,
    detail: str = "",
    severity: str = "info",
    market_id: str = None,
    signal_id: int = None,
    trade_id: int = None,
    metadata: dict = None,
):
    """Log a bot activity event to the database."""
    try:
        async with async_session() as session:
            entry = BotActivity(
                event_type=event_type,
                severity=severity,
                title=title,
                detail=detail,
                market_id=market_id,
                signal_id=signal_id,
                trade_id=trade_id,
                metadata_json=json.dumps(metadata) if metadata else None,
            )
            session.add(entry)
            await session.commit()
    except Exception as e:
        log.error("activity_log_error", error=str(e))
