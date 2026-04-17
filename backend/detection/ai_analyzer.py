"""
Filter 2: AI analysis using Claude Sonnet with web search.
Only invoked for trades that passed the heuristic filter.
Produces an investigation report explaining the AI's research and reasoning.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import anthropic
import structlog
from sqlalchemy import select, and_

from config import get_settings
from db.database import async_session
from db.models import AICache, Market, Wallet, Signal
from detection.heuristic_filter import RuleHit
from activity import record_ai_usage, log_activity

log = structlog.get_logger()
settings = get_settings()

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096
CACHE_TTL_HOURS = 6

_daily_calls = {"date": "", "count": 0}


def _check_daily_limit() -> bool:
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
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        result = await session.execute(
            select(AICache.response_json).where(
                and_(AICache.market_id == market_id, AICache.expires_at > now)
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
    async with async_session() as session:
        cache_entry = AICache(
            market_id=market_id,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=CACHE_TTL_HOURS),
            response_json=json.dumps(response),
        )
        session.add(cache_entry)
        await session.commit()


def build_prompt(hits: list[RuleHit], market: Market, wallets: list[Wallet]) -> str:
    all_wallets = []
    total_volume = 0
    rule_names = []
    for hit in hits:
        all_wallets.extend(hit.trigger_wallets)
        total_volume += hit.total_suspicious_volume
        rule_names.append(hit.rule_name)
    all_wallets = list(set(w for w in all_wallets if w))

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
                f"markets={w.markets_traded}"
            )
    wallet_section = "\n".join(wallet_info) if wallet_info else "  No wallet profiles available"

    time_to_res = "unknown"
    if market.end_date:
        end = market.end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        delta = end - datetime.now(timezone.utc)
        if delta.total_seconds() > 0:
            hours = delta.total_seconds() / 3600
            time_to_res = f"{hours:.1f} hours" if hours < 24 else f"{hours/24:.1f} days"
        else:
            time_to_res = "EXPIRED"

    direction = hits[0].direction if hits else "unknown"

    metadata_parts = []
    for hit in hits:
        if hit.metadata:
            metadata_parts.append(f"  {hit.rule_name}: {json.dumps(hit.metadata)}")
    metadata_section = "\n".join(metadata_parts) if metadata_parts else "  None"

    return f"""You are an investigator specialized in detecting insider trading on Polymarket prediction markets.

## Signal Detected
- Rules triggered: {', '.join(rule_names)}
- Market: {market.question}
- Category: {market.category or 'unknown'}
- Resolution date: {market.end_date}
- Time to resolution: {time_to_res}

## Suspicious Trade Data
- Wallet(s): {', '.join(w[:10] + '...' for w in all_wallets) if all_wallets else 'unknown'}
- Total suspicious volume: ${total_volume:,.0f}
- Direction: {direction}
- Current market price: YES={market.outcome_prices[0] if market.outcome_prices else 'N/A'}, NO={market.outcome_prices[1] if market.outcome_prices and len(market.outcome_prices) > 1 else 'N/A'}

## Wallet Profiles
{wallet_section}

## Rule Metadata
{metadata_section}

## Your Task

You must conduct a thorough investigation to determine whether this trade is likely insider trading. Follow these steps:

1. **Search the web** for recent news and information about this market's event:
   - Is there an upcoming announcement, decision, or scheduled event?
   - Have there been any leaks, rumors, or insider information reported?
   - Are there recent news articles that would explain this trade?
   - Who would have privileged information about this outcome?

2. **Analyze the trade pattern** in context:
   - Does the wallet behavior match known insider trading patterns (new account, large concentrated bet, timing)?
   - Is the bet size unusual relative to the market's liquidity?
   - Does public information justify this trade, or does it suggest non-public knowledge?
   - Is this just a normal whale/sophisticated trader making a rational bet?

3. **Write an investigation report** and respond in this exact JSON format (no markdown, no backticks):

{{
  "insider_score": <1-10>,
  "confidence": <0.0-1.0>,
  "likely_direction": "YES" | "NO",
  "investigation_report": "<DETAILED multi-paragraph report. Explain: (1) What you searched for and what you found. (2) Whether news/events justify the trade. (3) Analysis of the wallet behavior. (4) Your conclusion on whether this is insider trading and why.>",
  "reasoning": "<1-2 sentence summary of your conclusion>",
  "key_findings": ["<finding 1>", "<finding 2>", "<finding 3>"],
  "upcoming_event": "<description of relevant event if found>" | null,
  "upcoming_event_date": "<date if found>" | null,
  "news_justification": true | false,
  "recommendation": "STRONG_BUY" | "BUY" | "HOLD" | "SKIP"
}}

IMPORTANT scoring criteria:
- 1-3: Normal whale/sophisticated trader. Public info justifies the trade. NOT insider trading.
- 4-5: Somewhat suspicious but insufficient evidence. Could be a smart trader.
- 6-7: Highly suspicious. Multiple indicators align. Likely insider trading.
- 8-10: Near-certain insider trading. New account + large amount + pre-announcement timing + NO justifying public news.

IMPORTANT recommendation criteria:
- STRONG_BUY: Score 8-10. Very high confidence this is insider trading. Worth following.
- BUY: Score 6-7. Likely insider trading. Moderate confidence.
- HOLD: Score 4-5. Suspicious but not enough to trade on.
- SKIP: Score 1-3. Not insider trading. Normal market activity.

Be skeptical. Most large trades are NOT insider trading. Only recommend BUY/STRONG_BUY when you have strong evidence. 
We are only interested in trades that will finish in a short period of time (30 days tops). If the trade finishes after 30 days DO NOT conduct the investigation. It is important to save tokens and ai costs.
The shorter the time frame, the more suspicius (most insider trading happens in short time frames [1 minute, 15 minutes, 1 hour, 1 day])"""


async def analyze_with_ai(
    hits: list[RuleHit],
    market: Market,
    wallets: list[Wallet],
) -> dict | None:
    """Run AI analysis. Returns parsed response or None (with error logged to activity feed)."""
    key = settings.anthropic_api_key or ""

    # Check API key is valid (real keys are 90+ chars)
    if len(key) < 50:
        log.warning("ai_analyzer_no_api_key", key_length=len(key))
        await log_activity(
            event_type="ai_error",
            severity="error",
            title="AI analysis failed: API key not configured",
            detail=f"The ANTHROPIC_API_KEY in .env is missing or invalid (length: {len(key)}). AI cannot analyze trades.",
            market_id=market.condition_id,
        )
        return None

    # Check cache
    cached = await get_cached_analysis(market.condition_id)
    if cached:
        log.info("ai_cache_hit", market=market.condition_id[:12])
        return cached

    # Check daily limit
    if not _check_daily_limit():
        log.warning("ai_daily_limit_reached", count=_daily_calls["count"])
        await log_activity(
            event_type="ai_error",
            severity="warning",
            title=f"AI analysis skipped: daily limit reached ({_daily_calls['count']}/{settings.max_ai_calls_per_day})",
            detail=f"Market: {market.question[:60] if market.question else market.condition_id[:12]}",
            market_id=market.condition_id,
        )
        return None

    prompt = build_prompt(hits, market, wallets)

    try:
        client = anthropic.AsyncAnthropic(api_key=key)
        response = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content": prompt}],
        )

        _increment_daily_count()

        input_tokens = getattr(response.usage, "input_tokens", 0)
        output_tokens = getattr(response.usage, "output_tokens", 0)
        record_ai_usage(input_tokens, output_tokens)

        # Extract text (some blocks from web_search have text=None)
        text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text is not None:
                text += block.text

        text = text.strip()
        stop_reason = getattr(response, "stop_reason", "unknown")

        # Handle empty response (model spent all tokens on web searches)
        if not text:
            log.error("ai_empty_response", stop_reason=stop_reason, blocks=len(response.content))
            await log_activity(
                event_type="ai_error", severity="error",
                title=f"AI returned no text (stop_reason={stop_reason}, {len(response.content)} blocks)",
                detail="The model used all tokens on web searches without producing a final answer. Will retry next time.",
                market_id=market.condition_id,
            )
            return None

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        # Extract JSON object even if wrapped in prose
        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            text = json_match.group(0)

        result = json.loads(text)

        # Validate
        required = ["insider_score", "confidence", "likely_direction", "reasoning", "recommendation"]
        for field in required:
            if field not in result:
                log.error("ai_response_missing_field", field=field)
                return None

        result["insider_score"] = max(1, min(10, int(result["insider_score"])))
        result["confidence"] = max(0.0, min(1.0, float(result["confidence"])))

        # Cache
        await save_cache(market.condition_id, result)

        # Log the investigation report to activity feed
        report = result.get("investigation_report", result.get("reasoning", ""))
        cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000

        await log_activity(
            event_type="ai_analysis",
            severity="alert" if result["insider_score"] >= 7 else "info",
            title=f"AI investigation: {market.question[:60] if market.question else ''} — Score {result['insider_score']}/10",
            detail=report,
            market_id=market.condition_id,
            metadata={
                "model": MODEL,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 4),
                "daily_calls": _daily_calls["count"],
                "insider_score": result["insider_score"],
                "confidence": result["confidence"],
                "recommendation": result["recommendation"],
                "key_findings": result.get("key_findings", []),
                "news_justification": result.get("news_justification"),
                "upcoming_event": result.get("upcoming_event"),
            },
        )

        log.info(
            "ai_analysis_complete",
            market=market.condition_id[:12],
            score=result["insider_score"],
            confidence=result["confidence"],
            recommendation=result["recommendation"],
            cost=f"${cost:.4f}",
        )

        return result

    except json.JSONDecodeError as e:
        log.error("ai_response_parse_error", error=str(e))
        await log_activity(
            event_type="ai_error", severity="error",
            title=f"AI response parse error: {str(e)[:60]}",
            market_id=market.condition_id,
        )
        return None
    except anthropic.APIError as e:
        log.error("ai_api_error", error=str(e))
        await log_activity(
            event_type="ai_error", severity="error",
            title=f"AI API error: {str(e)[:80]}",
            market_id=market.condition_id,
        )
        return None
    except Exception as e:
        log.error("ai_unexpected_error", error=str(e))
        await log_activity(
            event_type="ai_error", severity="error",
            title=f"AI error: {str(e)[:80]}",
            market_id=market.condition_id,
        )
        return None
