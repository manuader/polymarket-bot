"""
Signal Manager: orchestrates the full detection pipeline.
  Trade → Heuristic Filter → AI Analyzer → Signal creation/deduplication.
Computes composite scores and manages signal lifecycle.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select, and_, func

from config import get_settings
from db.database import async_session
from db.models import Trade, Signal, Market, Wallet
from detection.heuristic_filter import evaluate_trade, RuleHit
from detection.ai_analyzer import analyze_with_ai
from activity import log_activity

log = structlog.get_logger()
settings = get_settings()

# Queue for trades to evaluate
trade_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)


def compute_composite_score(
    hits: list[RuleHit],
    ai_result: dict | None,
) -> tuple[int, float, str]:
    """
    Compute final score combining heuristic hits and AI analysis.
    Returns (score, confidence, recommendation).

    Scoring logic:
    - Base: highest heuristic priority
    - +1 if multiple rules triggered
    - +1 if AI score > heuristic score
    - -2 if AI says SKIP
    - Confidence: AI confidence if available, else 0.5
    """
    if not hits:
        return 1, 0.0, "SKIP"

    # Base score from heuristics
    base_score = max(h.priority for h in hits)

    # Bonus for multiple rules
    if len(hits) >= 2:
        base_score += 1
    if len(hits) >= 4:
        base_score += 1

    confidence = 0.5
    recommendation = "HOLD"

    if ai_result:
        ai_score = ai_result.get("insider_score", 5)
        ai_confidence = ai_result.get("confidence", 0.5)
        ai_rec = ai_result.get("recommendation", "HOLD")

        # Blend heuristic and AI scores
        if ai_score > base_score:
            base_score += 1
        elif ai_score < base_score - 2:
            base_score -= 1

        # AI recommendation modifiers
        if ai_rec == "STRONG_BUY":
            base_score += 1
        elif ai_rec == "SKIP":
            base_score -= 2

        confidence = ai_confidence
        recommendation = ai_rec

        # If news justifies the movement, reduce score
        if ai_result.get("news_justification"):
            base_score -= 2

    # Clamp
    final_score = max(1, min(10, base_score))

    # Derive recommendation from score if AI didn't provide one
    if not ai_result:
        if final_score >= 8:
            recommendation = "STRONG_BUY"
        elif final_score >= 6:
            recommendation = "BUY"
        elif final_score >= 4:
            recommendation = "HOLD"
        else:
            recommendation = "SKIP"

    return final_score, confidence, recommendation


async def is_duplicate_signal(market_id: str, signal_type: str, hours: int = 4) -> bool:
    """Check if a similar signal was already created recently."""
    async with async_session() as session:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = await session.execute(
            select(func.count(Signal.id)).where(
                and_(
                    Signal.market_id == market_id,
                    Signal.signal_type == signal_type,
                    Signal.detected_at >= cutoff,
                    Signal.status == "active",
                )
            )
        )
        count = result.scalar_one()
        return count > 0


async def create_signal(
    hits: list[RuleHit],
    ai_result: dict | None,
    market: Market,
) -> Signal | None:
    """Create a signal from rule hits and AI analysis."""
    if not hits:
        return None

    # Check for duplicates
    primary_type = hits[0].rule_name
    if await is_duplicate_signal(market.condition_id, primary_type):
        log.info("signal_deduplicated", market=market.condition_id[:12], type=primary_type)
        return None

    score, confidence, recommendation = compute_composite_score(hits, ai_result)

    # Aggregate data from hits
    all_wallets = list(set(w for h in hits for w in h.trigger_wallets if w))
    all_trade_ids = list(set(tid for h in hits for tid in h.trigger_trade_ids))
    total_volume = sum(h.total_suspicious_volume for h in hits)
    direction = hits[0].direction

    # Market price at detection
    market_price = None
    if market.outcome_prices:
        if direction == "YES" and len(market.outcome_prices) > 0:
            market_price = market.outcome_prices[0]
        elif direction == "NO" and len(market.outcome_prices) > 1:
            market_price = market.outcome_prices[1]

    # Time to resolution
    time_to_res = None
    if market.end_date:
        end = market.end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        time_to_res = end - datetime.now(timezone.utc)

    # AI-derived fields
    analysis = ai_result.get("reasoning", "") if ai_result else ""
    web_context = ""
    key_findings = []
    if ai_result:
        key_findings = ai_result.get("key_findings", [])
        event = ai_result.get("upcoming_event")
        event_date = ai_result.get("upcoming_event_date")
        if event:
            web_context = f"Upcoming event: {event}"
            if event_date:
                web_context += f" (date: {event_date})"

    signal_type = "+".join(h.rule_name for h in hits)

    async with async_session() as session:
        signal = Signal(
            market_id=market.condition_id,
            signal_type=signal_type,
            score=score,
            direction=direction,
            confidence=confidence,
            analysis=analysis,
            trigger_wallets=all_wallets,
            trigger_trade_ids=all_trade_ids,
            total_suspicious_volume=total_volume,
            market_price_at_detection=market_price,
            time_to_resolution=time_to_res,
            web_context=web_context,
            key_findings=key_findings,
            recommendation=recommendation,
            status="active",
        )
        session.add(signal)
        await session.commit()
        await session.refresh(signal)

        log.warning(
            "signal_created",
            signal_id=signal.id,
            market=market.condition_id[:12],
            question=market.question[:60] if market.question else "",
            score=score,
            confidence=confidence,
            direction=direction,
            rules=signal_type,
            recommendation=recommendation,
            volume=total_volume,
        )

        # Log to activity feed
        await log_activity(
            event_type="signal_detected",
            severity="alert" if score >= 7 else "warning",
            title=f"Signal score {score}: {market.question[:80] if market.question else market.condition_id[:12]}",
            detail=f"Rules: {signal_type}. Direction: {direction}. Confidence: {confidence:.0%}. Recommendation: {recommendation}. {analysis}",
            market_id=market.condition_id,
            signal_id=signal.id,
            metadata={
                "score": score,
                "confidence": confidence,
                "direction": direction,
                "recommendation": recommendation,
                "rules": signal_type.split("+"),
                "volume": total_volume,
                "wallets": all_wallets[:5],
                "key_findings": key_findings,
            },
        )

        return signal


async def process_trade(trade: Trade) -> Signal | None:
    """Full detection pipeline for a single trade."""
    # Step 1: Heuristic filter
    hits = await evaluate_trade(trade)
    if not hits:
        return None

    await log_activity(
        event_type="trade_flagged",
        severity="info",
        title=f"Trade ${trade.usd_value:,.0f} flagged by {len(hits)} rule(s)",
        detail=f"Market: {trade.market_id[:16]}. Rules: {', '.join(h.rule_name for h in hits)}. Wallet: {(trade.taker_address or 'unknown')[:12]}...",
        market_id=trade.market_id,
        metadata={"usd_value": trade.usd_value, "rules": [h.rule_name for h in hits]},
    )

    # Step 2: Load market and wallet data for AI analysis
    async with async_session() as session:
        market_result = await session.execute(
            select(Market).where(Market.condition_id == trade.market_id)
        )
        market = market_result.scalar_one_or_none()
        if not market:
            return None

        # Load wallet profiles for triggered wallets
        all_wallet_addrs = list(set(w for h in hits for w in h.trigger_wallets if w))
        wallets = []
        for addr in all_wallet_addrs:
            w_result = await session.execute(
                select(Wallet).where(Wallet.address == addr)
            )
            w = w_result.scalar_one_or_none()
            if w:
                wallets.append(w)

    # Step 3: AI analysis (only for high-priority hits)
    max_priority = max(h.priority for h in hits)
    ai_result = None
    if max_priority >= 6:  # Only invoke AI for medium+ priority
        ai_result = await analyze_with_ai(hits, market, wallets)

    # Step 4: Create signal
    signal = await create_signal(hits, ai_result, market)
    return signal


async def run_detection_engine():
    """Main detection loop: processes trades from the queue."""
    log.info("detection_engine_starting")

    while True:
        try:
            trade = await trade_queue.get()
            signal = await process_trade(trade)
            trade_queue.task_done()
        except Exception as e:
            log.error("detection_engine_error", error=str(e))
            await asyncio.sleep(1)


async def scan_recent_trades():
    """Scan recent large trades for signals (catch-up on startup)."""
    async with async_session() as session:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        result = await session.execute(
            select(Trade)
            .where(
                and_(
                    Trade.timestamp >= cutoff,
                    Trade.usd_value >= settings.min_trade_usd,
                )
            )
            .order_by(Trade.timestamp.desc())
            .limit(100)
        )
        trades = result.scalars().all()

    log.info("scanning_recent_trades", count=len(trades))
    for trade in trades:
        try:
            await process_trade(trade)
        except Exception as e:
            log.error("scan_trade_error", trade_id=trade.id, error=str(e))
