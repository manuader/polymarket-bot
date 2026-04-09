"""
Filter 2: AI analysis using Claude Sonnet with web search.
Only invoked for trades that passed the heuristic filter (~5% of total).
Produces insider_score (1-10), confidence (0-1), and recommendation.
Results are cached per market for 6 hours.
"""

import json
from datetime import datetime, timedelta, timezone

import anthropic
import structlog
from sqlalchemy import select, and_

from config import get_settings
from db.database import async_session
from db.models import AICache, Market, Wallet, Signal
from detection.heuristic_filter import RuleHit

log = structlog.get_logger()
settings = get_settings()

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1500
CACHE_TTL_HOURS = 6

# Track daily invocations
_daily_calls = {"date": "", "count": 0}


def _check_daily_limit() -> bool:
    """Check if we're within the daily AI call limit."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _daily_calls["date"] != today:
        _daily_calls["date"] = today
        _daily_calls["count"] = 0
    return _daily_calls["count"] < settings.max_ai_calls_per_day


def _increment_daily_count():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _daily_calls["date"] != today:
        _daily_calls["date"] = today
        _daily_calls["count"] = 0
    _daily_calls["count"] += 1


async def get_cached_analysis(market_id: str) -> dict | None:
    """Check for a cached AI analysis for this market."""
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        result = await session.execute(
            select(AICache.response_json).where(
                and_(
                    AICache.market_id == market_id,
                    AICache.expires_at > now,
                )
            ).order_by(AICache.created_at.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            try:
                return json.loads(row)
            except json.JSONDecodeError:
                pass
    return None


async def save_cache(market_id: str, response: dict):
    """Cache an AI analysis result."""
    async with async_session() as session:
        cache_entry = AICache(
            market_id=market_id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS),
            response_json=json.dumps(response),
        )
        session.add(cache_entry)
        await session.commit()


def build_prompt(hits: list[RuleHit], market: Market, wallets: list[Wallet]) -> str:
    """Build the analysis prompt from rule hits and market context."""
    # Aggregate info from all hits
    all_wallets = []
    total_volume = 0
    rule_names = []
    for hit in hits:
        all_wallets.extend(hit.trigger_wallets)
        total_volume += hit.total_suspicious_volume
        rule_names.append(hit.rule_name)

    all_wallets = list(set(w for w in all_wallets if w))

    # Build wallet profiles section
    wallet_info = []
    for w in wallets:
        if w:
            age = "unknown"
            if w.first_seen:
                age_days = (datetime.now(timezone.utc) - w.first_seen).days
                age = f"{age_days} days"
            wallet_info.append(
                f"  - {w.address[:10]}...: age={age}, trades={w.total_trades}, "
                f"volume=${w.total_volume:,.0f}, win_rate={w.win_rate or 'N/A'}, "
                f"markets={w.markets_traded}, flagged_hashdive={w.is_flagged_hashdive}"
            )

    wallet_section = "\n".join(wallet_info) if wallet_info else "  No wallet profiles available"

    # Time to resolution
    time_to_res = "unknown"
    if market.end_date:
        end = market.end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        delta = end - datetime.now(timezone.utc)
        if delta.total_seconds() > 0:
            hours = delta.total_seconds() / 3600
            if hours < 24:
                time_to_res = f"{hours:.1f} hours"
            else:
                time_to_res = f"{hours/24:.1f} days"
        else:
            time_to_res = "EXPIRED"

    direction = hits[0].direction if hits else "unknown"
    entry_price = 0
    if market.outcome_prices:
        if direction == "YES" and len(market.outcome_prices) > 0:
            entry_price = market.outcome_prices[0]
        elif direction == "NO" and len(market.outcome_prices) > 1:
            entry_price = market.outcome_prices[1]

    # Collect metadata from all hits
    metadata_parts = []
    for hit in hits:
        if hit.metadata:
            metadata_parts.append(f"  {hit.rule_name}: {json.dumps(hit.metadata)}")
    metadata_section = "\n".join(metadata_parts) if metadata_parts else "  None"

    return f"""You are an analyst specialized in detecting insider trading on Polymarket prediction markets.

## Signal Detected
- Rules triggered: {', '.join(rule_names)}
- Market: {market.question}
- Category: {market.category or 'unknown'}
- Resolution date: {market.end_date}
- Time to resolution: {time_to_res}

## Suspicious Trade Data
- Wallet(s): {', '.join(w[:10] + '...' for w in all_wallets) if all_wallets else 'unknown'}
- Total suspicious volume: ${total_volume:,.0f}
- Direction: {direction} (YES/NO)
- Current market price: YES={market.outcome_prices[0] if market.outcome_prices else 'N/A'}, NO={market.outcome_prices[1] if market.outcome_prices and len(market.outcome_prices) > 1 else 'N/A'}

## Wallet Profiles
{wallet_section}

## Rule Metadata
{metadata_section}

## Your Task

1. Search the web for information about this market's event:
   - Is there an upcoming announcement or scheduled decision?
   - Are there recent news that would justify this price movement?
   - Who would have privileged information about this outcome?

2. Assess the probability of insider trading considering:
   - Timing of the trade vs announcement/resolution date
   - Wallet profile (new, no history, concentrated in one market)
   - Bet size vs market probability
   - Whether public information justifies the bet
   - Known insider trading patterns on Polymarket

3. Respond EXCLUSIVELY in the following JSON format (no markdown, no backticks):

{{
  "insider_score": <1-10>,
  "confidence": <0.0-1.0>,
  "likely_direction": "YES" | "NO",
  "reasoning": "<2-3 sentence explanation>",
  "key_findings": ["<finding 1>", "<finding 2>"],
  "upcoming_event": "<description of relevant event if found>" | null,
  "upcoming_event_date": "<date if found>" | null,
  "news_justification": true | false,
  "recommendation": "STRONG_BUY" | "BUY" | "HOLD" | "SKIP"
}}

Scoring criteria:
- 1-3: Normal whale/sophisticated trader activity
- 4-5: Suspicious but insufficient evidence
- 6-7: Highly suspicious, multiple indicators align
- 8-10: Near-certain insider trading (new account + large amount + pre-announcement timing + no justifying news)"""


async def analyze_with_ai(
    hits: list[RuleHit],
    market: Market,
    wallets: list[Wallet],
) -> dict | None:
    """
    Run AI analysis on a set of rule hits.
    Returns parsed JSON response or None if unavailable.
    """
    if not settings.anthropic_api_key or settings.anthropic_api_key.startswith("sk-ant-..."):
        log.warning("ai_analyzer_no_api_key")
        # Return a synthetic score based on heuristic priority
        max_priority = max(h.priority for h in hits) if hits else 5
        return {
            "insider_score": max_priority,
            "confidence": 0.5,
            "likely_direction": hits[0].direction if hits else "YES",
            "reasoning": f"AI analysis unavailable. Heuristic score based on {len(hits)} rule(s): {', '.join(h.rule_name for h in hits)}",
            "key_findings": [h.rule_name for h in hits],
            "upcoming_event": None,
            "upcoming_event_date": None,
            "news_justification": False,
            "recommendation": "BUY" if max_priority >= 7 else "HOLD",
        }

    # Check cache first
    cached = await get_cached_analysis(market.condition_id)
    if cached:
        log.info("ai_cache_hit", market=market.condition_id[:12])
        return cached

    # Check daily limit
    if not _check_daily_limit():
        log.warning("ai_daily_limit_reached", count=_daily_calls["count"])
        return None

    prompt = build_prompt(hits, market, wallets)

    try:
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": prompt}],
        )

        _increment_daily_count()

        # Track token usage
        from activity import record_ai_usage, log_activity
        input_tokens = getattr(response.usage, "input_tokens", 0)
        output_tokens = getattr(response.usage, "output_tokens", 0)
        record_ai_usage(input_tokens, output_tokens)

        await log_activity(
            event_type="ai_analysis",
            severity="info",
            title=f"AI analyzed: {market.question[:60] if market.question else market.condition_id[:12]}",
            detail=f"Model: {MODEL}. Input tokens: {input_tokens}. Output tokens: {output_tokens}.",
            market_id=market.condition_id,
            metadata={
                "model": MODEL,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "daily_calls": _daily_calls["count"],
            },
        )

        # Extract text from response
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        # Parse JSON response
        text = text.strip()
        # Remove markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        result = json.loads(text)

        # Validate required fields
        required = ["insider_score", "confidence", "likely_direction", "reasoning", "recommendation"]
        for field in required:
            if field not in result:
                log.error("ai_response_missing_field", field=field)
                return None

        # Clamp values
        result["insider_score"] = max(1, min(10, int(result["insider_score"])))
        result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

        # Cache the result
        await save_cache(market.condition_id, result)

        log.info(
            "ai_analysis_complete",
            market=market.condition_id[:12],
            score=result["insider_score"],
            confidence=result["confidence"],
            recommendation=result["recommendation"],
        )

        return result

    except json.JSONDecodeError as e:
        log.error("ai_response_parse_error", error=str(e), raw=text[:200] if text else "")
        return None
    except anthropic.APIError as e:
        log.error("ai_api_error", error=str(e))
        return None
    except Exception as e:
        log.error("ai_unexpected_error", error=str(e))
        return None
