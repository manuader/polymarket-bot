"""
Configurable thresholds for all heuristic detection rules.
Separating config from logic makes it easy to tune for maximum alpha.
"""

from dataclasses import dataclass, field


@dataclass
class RuleThresholds:
    # Rule 1: WHALE_NEW_ACCOUNT
    whale_new_account_age_days: int = 7
    whale_new_account_min_usd: float = 10_000
    whale_new_account_max_trades: int = 5
    whale_new_account_priority: int = 8

    # Rule 2: VOLUME_SPIKE
    volume_spike_multiplier: float = 3.0
    volume_spike_priority: int = 8

    # Rule 3: PRE_ANNOUNCEMENT_ACTIVITY
    pre_announcement_hours: int = 48
    pre_announcement_min_usd: float = 5_000
    pre_announcement_priority: int = 8

    # Rule 4: IMPROBABLE_BET
    improbable_bet_max_price: float = 0.15
    improbable_bet_min_usd: float = 10_000
    improbable_bet_priority: int = 7

    # Rule 5: COORDINATED_WALLETS
    coordinated_min_wallets: int = 3
    coordinated_window_hours: int = 1
    coordinated_min_combined_usd: float = 20_000
    coordinated_priority: int = 9

    # Rule 6: HIGH_WIN_RATE_WHALE
    high_wr_min_rate: float = 0.85
    high_wr_min_trades: int = 10
    high_wr_min_usd: float = 10_000
    high_wr_priority: int = 6

    # Rule 7: PRICE_REVERSAL_AFTER_SPIKE (new)
    price_reversal_min_spike_pct: float = 0.10  # 10% price move
    price_reversal_revert_pct: float = 0.50  # reverts 50%+ of spike
    price_reversal_window_minutes: int = 30
    price_reversal_priority: int = 7

    # Rule 8: BET_AGAINST_CONSENSUS (new)
    consensus_threshold: float = 0.80  # market probability > 80%
    consensus_min_usd: float = 10_000
    consensus_priority: int = 7

    # General
    min_trade_usd: float = 10_000


# Singleton
thresholds = RuleThresholds()
