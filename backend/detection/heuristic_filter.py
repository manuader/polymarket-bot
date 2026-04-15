"""
Filter 1: Heuristic rules that flag suspicious trades without AI.
Runs on every large trade. Only ~5% should pass to the AI analyzer.

8 rules, each returns a list of RuleHit with type, priority, and metadata.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select, func, and_, distinct

from db.database import async_session
from db.models import Trade, Wallet, Market, MarketVolumeSnapshot
from detection.rules_config import thresholds
from pipeline.volume_tracker import get_avg_24h_volume, get_latest_snapshot
from pipeline.wallet_profiler import profile_wallet_from_api

log = structlog.get_logger()


@dataclass
class RuleHit:
    rule_name: str
    priority: int  # 1-10, higher = more suspicious
    market_id: str
    direction: str  # YES or NO
    trigger_wallets: list[str] = field(default_factory=list)
    trigger_trade_ids: list[int] = field(default_factory=list)
    total_suspicious_volume: float = 0
    metadata: dict = field(default_factory=dict)


async def rule_whale_new_account(trade: Trade) -> RuleHit | None:
    """Rule 1: Large trade from a new wallet with few trades.
    Fetches REAL wallet profile from Polymarket Data API."""
    if trade.usd_value < thresholds.whale_new_account_min_usd:
        return None

    address = trade.taker_address or trade.maker_address
    if not address:
        return None

    # Fetch real profile from Polymarket API
    profile = await profile_wallet_from_api(address)

    if not profile or profile.get("total_trades", 0) == 0:
        # Brand new wallet with no history at all
        return RuleHit(
            rule_name="WHALE_NEW_ACCOUNT",
            priority=thresholds.whale_new_account_priority,
            market_id=trade.market_id,
            direction=trade.outcome,
            trigger_wallets=[address],
            trigger_trade_ids=[trade.id] if trade.id else [],
            total_suspicious_volume=trade.usd_value,
            metadata={"wallet_age_days": 0, "wallet_trades": 0, "wallet_volume": 0},
        )

    first_seen = profile.get("first_seen", datetime.now(timezone.utc))
    if isinstance(first_seen, str):
        first_seen = datetime.fromisoformat(first_seen)
    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)

    age = datetime.now(timezone.utc) - first_seen
    total_trades = profile.get("total_trades", 0)

    if age.days < thresholds.whale_new_account_age_days and total_trades < thresholds.whale_new_account_max_trades:
        return RuleHit(
            rule_name="WHALE_NEW_ACCOUNT",
            priority=thresholds.whale_new_account_priority,
            market_id=trade.market_id,
            direction=trade.outcome,
            trigger_wallets=[address],
            trigger_trade_ids=[trade.id] if trade.id else [],
            total_suspicious_volume=trade.usd_value,
            metadata={
                "wallet_age_days": age.days,
                "wallet_trades": total_trades,
                "wallet_volume": profile.get("total_volume", 0),
                "wallet_markets": profile.get("markets_traded", 0),
                "wallet_win_rate": profile.get("win_rate"),
                "wallet_topics": profile.get("top_topics", []),
            },
        )

    return None


async def rule_volume_spike(trade: Trade) -> RuleHit | None:
    """Rule 2: Volume in last hour is 3x+ the 24h average."""
    snapshot = await get_latest_snapshot(trade.market_id)
    if not snapshot or not snapshot.volume_1h:
        return None

    avg_24h = await get_avg_24h_volume(trade.market_id)
    if avg_24h <= 0:
        return None

    # Normalize avg_24h to hourly
    avg_hourly = avg_24h / 24
    if avg_hourly <= 0:
        return None

    ratio = snapshot.volume_1h / avg_hourly
    if ratio >= thresholds.volume_spike_multiplier:
        return RuleHit(
            rule_name="VOLUME_SPIKE",
            priority=thresholds.volume_spike_priority,
            market_id=trade.market_id,
            direction=trade.outcome,
            total_suspicious_volume=snapshot.volume_1h,
            metadata={"spike_ratio": round(ratio, 2), "volume_1h": snapshot.volume_1h, "avg_hourly": avg_hourly},
        )

    return None


async def rule_pre_announcement(trade: Trade) -> RuleHit | None:
    """Rule 3: Large trade close to market resolution date from new/small wallet."""
    if trade.usd_value < thresholds.pre_announcement_min_usd:
        return None

    async with async_session() as session:
        result = await session.execute(
            select(Market.end_date).where(Market.condition_id == trade.market_id)
        )
        row = result.one_or_none()

    if not row or not row[0]:
        return None

    end_date = row[0]
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    time_remaining = end_date - datetime.now(timezone.utc)

    if time_remaining.total_seconds() > thresholds.pre_announcement_hours * 3600:
        return None

    # Check if wallet is new/small using real API data
    address = trade.taker_address or trade.maker_address
    if not address:
        return None

    profile = await profile_wallet_from_api(address)
    is_new = not profile or profile.get("total_trades", 0) < 10

    if is_new:
        return RuleHit(
            rule_name="PRE_ANNOUNCEMENT_ACTIVITY",
            priority=thresholds.pre_announcement_priority,
            market_id=trade.market_id,
            direction=trade.outcome,
            trigger_wallets=[address],
            trigger_trade_ids=[trade.id] if trade.id else [],
            total_suspicious_volume=trade.usd_value,
            metadata={"hours_to_resolution": round(time_remaining.total_seconds() / 3600, 1)},
        )

    return None


async def rule_improbable_bet(trade: Trade) -> RuleHit | None:
    """Rule 4: Large bet on an outcome the market considers very unlikely (<15%)."""
    if trade.usd_value < thresholds.improbable_bet_min_usd:
        return None

    if trade.price < thresholds.improbable_bet_max_price:
        return RuleHit(
            rule_name="IMPROBABLE_BET",
            priority=thresholds.improbable_bet_priority,
            market_id=trade.market_id,
            direction=trade.outcome,
            trigger_wallets=[trade.taker_address or trade.maker_address or ""],
            trigger_trade_ids=[trade.id] if trade.id else [],
            total_suspicious_volume=trade.usd_value,
            metadata={"entry_price": trade.price, "implied_probability": f"{trade.price*100:.1f}%"},
        )

    return None


async def rule_coordinated_wallets(trade: Trade) -> RuleHit | None:
    """Rule 5: Multiple new wallets buying the same outcome in the same hour."""
    async with async_session() as session:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=thresholds.coordinated_window_hours)

        # Find new wallets trading this market in the last hour
        result = await session.execute(
            select(
                Trade.taker_address,
                func.sum(Trade.usd_value),
                func.count(Trade.id),
            )
            .where(
                and_(
                    Trade.market_id == trade.market_id,
                    Trade.outcome == trade.outcome,
                    Trade.timestamp >= cutoff,
                    Trade.taker_address.isnot(None),
                    Trade.taker_address != "",
                )
            )
            .group_by(Trade.taker_address)
        )
        recent_wallets = result.all()

        if len(recent_wallets) < thresholds.coordinated_min_wallets:
            return None

        # Check how many are new using real API data (limit to 5 to avoid API spam)
        new_wallets = []
        total_volume = 0

        for addr, vol, count in recent_wallets[:5]:
            total_volume += float(vol)
            profile = await profile_wallet_from_api(addr)
            if not profile or profile.get("total_trades", 0) == 0:
                new_wallets.append(addr)
            elif profile.get("first_seen"):
                fs = profile["first_seen"]
                if isinstance(fs, str):
                    fs = datetime.fromisoformat(fs)
                if fs.tzinfo is None:
                    fs = fs.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - fs).days < 7:
                    new_wallets.append(addr)
        # Add remaining volume from unchecked wallets
        for addr, vol, count in recent_wallets[5:]:
            total_volume += float(vol)

        if len(new_wallets) >= thresholds.coordinated_min_wallets and total_volume >= thresholds.coordinated_min_combined_usd:
            return RuleHit(
                rule_name="COORDINATED_WALLETS",
                priority=thresholds.coordinated_priority,
                market_id=trade.market_id,
                direction=trade.outcome,
                trigger_wallets=new_wallets,
                total_suspicious_volume=total_volume,
                metadata={"new_wallet_count": len(new_wallets), "total_wallets": len(recent_wallets)},
            )

    return None


async def rule_high_win_rate_whale(trade: Trade) -> RuleHit | None:
    """Rule 6: High win rate wallet making a large trade.
    Uses real API data for accurate win rate."""
    if trade.usd_value < thresholds.high_wr_min_usd:
        return None

    address = trade.taker_address or trade.maker_address
    if not address:
        return None

    profile = await profile_wallet_from_api(address)
    if not profile or profile.get("win_rate") is None:
        return None

    win_rate = profile["win_rate"]
    total_trades = profile.get("total_trades", 0)

    if win_rate >= thresholds.high_wr_min_rate and total_trades >= thresholds.high_wr_min_trades:
        return RuleHit(
            rule_name="HIGH_WIN_RATE_WHALE",
            priority=thresholds.high_wr_priority,
            market_id=trade.market_id,
            direction=trade.outcome,
            trigger_wallets=[address],
            trigger_trade_ids=[trade.id] if trade.id else [],
            total_suspicious_volume=trade.usd_value,
            metadata={
                "win_rate": win_rate,
                "total_trades": total_trades,
                "total_volume": profile.get("total_volume", 0),
                "markets_traded": profile.get("markets_traded", 0),
                "top_topics": profile.get("top_topics", []),
            },
        )

    return None


async def rule_price_reversal_after_spike(trade: Trade) -> RuleHit | None:
    """Rule 7 (NEW): Large price spike followed by partial reversal.
    Suggests informed trader entered and market partially corrected."""
    async with async_session() as session:
        window = datetime.now(timezone.utc) - timedelta(minutes=thresholds.price_reversal_window_minutes)

        result = await session.execute(
            select(Trade.price, Trade.timestamp)
            .where(
                and_(
                    Trade.market_id == trade.market_id,
                    Trade.outcome == trade.outcome,
                    Trade.timestamp >= window,
                )
            )
            .order_by(Trade.timestamp.asc())
        )
        prices = result.all()

    if len(prices) < 5:
        return None

    price_values = [p[0] for p in prices]
    first_price = price_values[0]
    max_price = max(price_values)
    min_price = min(price_values)
    current_price = price_values[-1]

    # Check for spike up then reversal
    if first_price > 0:
        spike_up = (max_price - first_price) / first_price
        if spike_up >= thresholds.price_reversal_min_spike_pct:
            reversal = (max_price - current_price) / (max_price - first_price) if max_price > first_price else 0
            if reversal >= thresholds.price_reversal_revert_pct:
                return RuleHit(
                    rule_name="PRICE_REVERSAL_AFTER_SPIKE",
                    priority=thresholds.price_reversal_priority,
                    market_id=trade.market_id,
                    direction=trade.outcome,
                    total_suspicious_volume=trade.usd_value,
                    metadata={"spike_pct": round(spike_up * 100, 1), "reversal_pct": round(reversal * 100, 1)},
                )

    # Check for spike down then reversal
    if first_price > 0:
        spike_down = (first_price - min_price) / first_price
        if spike_down >= thresholds.price_reversal_min_spike_pct:
            reversal = (current_price - min_price) / (first_price - min_price) if first_price > min_price else 0
            if reversal >= thresholds.price_reversal_revert_pct:
                return RuleHit(
                    rule_name="PRICE_REVERSAL_AFTER_SPIKE",
                    priority=thresholds.price_reversal_priority,
                    market_id=trade.market_id,
                    direction=trade.outcome,
                    total_suspicious_volume=trade.usd_value,
                    metadata={"spike_pct": round(spike_down * 100, 1), "reversal_pct": round(reversal * 100, 1)},
                )

    return None


async def rule_bet_against_consensus(trade: Trade) -> RuleHit | None:
    """Rule 8 (NEW): Large bet against strong market consensus (>80% one way)."""
    if trade.usd_value < thresholds.consensus_min_usd:
        return None

    async with async_session() as session:
        result = await session.execute(
            select(Market.outcome_prices).where(Market.condition_id == trade.market_id)
        )
        row = result.one_or_none()

    if not row or not row[0] or len(row[0]) < 2:
        return None

    yes_price, no_price = row[0][0], row[0][1]

    # Determine if this trade is against consensus
    is_against = False
    if yes_price >= thresholds.consensus_threshold and trade.outcome == "NO":
        is_against = True
    elif no_price >= thresholds.consensus_threshold and trade.outcome == "YES":
        is_against = True

    if is_against:
        return RuleHit(
            rule_name="BET_AGAINST_CONSENSUS",
            priority=thresholds.consensus_priority,
            market_id=trade.market_id,
            direction=trade.outcome,
            trigger_wallets=[trade.taker_address or trade.maker_address or ""],
            trigger_trade_ids=[trade.id] if trade.id else [],
            total_suspicious_volume=trade.usd_value,
            metadata={"yes_price": yes_price, "no_price": no_price, "bet_direction": trade.outcome},
        )

    return None


async def rule_big_bet(trade: Trade) -> RuleHit | None:
    """Rule 9: Any trade >= $20k automatically flagged for AI analysis."""
    if trade.usd_value < thresholds.big_bet_min_usd:
        return None

    return RuleHit(
        rule_name="BIG_BET",
        priority=thresholds.big_bet_priority,
        market_id=trade.market_id,
        direction=trade.outcome,
        trigger_wallets=[trade.taker_address or trade.maker_address or ""],
        trigger_trade_ids=[trade.id] if trade.id else [],
        total_suspicious_volume=trade.usd_value,
        metadata={"usd_value": trade.usd_value},
    )


# All rules in order of evaluation
ALL_RULES = [
    rule_coordinated_wallets,      # Highest priority (9) — strongest signal
    rule_big_bet,                  # Priority 8 — any trade >= $20k
    rule_whale_new_account,        # Priority 8
    rule_volume_spike,             # Priority 8
    rule_pre_announcement,         # Priority 8
    rule_improbable_bet,           # Priority 7
    rule_price_reversal_after_spike,  # Priority 7
    rule_bet_against_consensus,    # Priority 7
    rule_high_win_rate_whale,      # Priority 6
]


async def evaluate_trade(trade: Trade) -> tuple[list[RuleHit], dict[str, str]]:
    """Run all heuristic rules against a single trade.
    Returns (list of hits, dict of rule_name -> reason for non-match)."""
    if trade.usd_value < thresholds.min_trade_usd:
        return [], {"ALL": f"usd_value {trade.usd_value:.0f} < {thresholds.min_trade_usd}"}

    hits = []
    rule_results = {}

    for rule_fn in ALL_RULES:
        rule_name = rule_fn.__name__.replace("rule_", "").upper()
        try:
            hit = await rule_fn(trade)
            if hit:
                hits.append(hit)
                rule_results[rule_name] = "MATCHED"
            else:
                rule_results[rule_name] = "no match"
        except Exception as e:
            rule_results[rule_name] = f"error: {str(e)[:60]}"
            log.error("rule_evaluation_error", rule=rule_fn.__name__, error=str(e))

    if hits:
        log.warning(
            "heuristic_hits",
            market=trade.market_id[:12],
            rules=[h.rule_name for h in hits],
            max_priority=max(h.priority for h in hits),
        )

    return hits, rule_results
