"""Tests for detection/rules_config.py — threshold values and consistency"""

from detection.rules_config import thresholds, RuleThresholds


def test_thresholds_loaded():
    assert thresholds is not None
    assert isinstance(thresholds, RuleThresholds)


def test_min_trade_usd_from_settings():
    # Should match config.py settings (loaded from .env)
    assert thresholds.min_trade_usd > 0


def test_all_rule_thresholds_positive():
    assert thresholds.whale_new_account_min_usd > 0
    assert thresholds.whale_new_account_age_days > 0
    assert thresholds.whale_new_account_max_trades > 0
    assert thresholds.pre_announcement_min_usd > 0
    assert thresholds.pre_announcement_hours > 0
    assert thresholds.improbable_bet_min_usd > 0
    assert thresholds.coordinated_min_combined_usd > 0
    assert thresholds.high_wr_min_usd > 0
    assert thresholds.consensus_min_usd > 0


def test_priorities_in_valid_range():
    for attr in dir(thresholds):
        if attr.endswith("_priority"):
            val = getattr(thresholds, attr)
            assert 1 <= val <= 10, f"{attr}={val} out of range"


def test_improbable_bet_price_in_range():
    assert 0 < thresholds.improbable_bet_max_price < 1.0


def test_consensus_threshold_in_range():
    assert 0.5 < thresholds.consensus_threshold < 1.0


def test_win_rate_threshold_in_range():
    assert 0.5 < thresholds.high_wr_min_rate <= 1.0


def test_volume_spike_multiplier_reasonable():
    assert 1.0 < thresholds.volume_spike_multiplier < 10.0


def test_rule_usd_thresholds_derived_from_base():
    # All rule thresholds should be proportional to min_trade_usd
    base = thresholds.min_trade_usd
    assert thresholds.whale_new_account_min_usd == base
    assert thresholds.improbable_bet_min_usd == base
    assert thresholds.consensus_min_usd == base
    assert thresholds.high_wr_min_usd == base
    assert thresholds.pre_announcement_min_usd == base / 2
    assert thresholds.coordinated_min_combined_usd == base * 4
