"""Tests for activity.py — record_ai_usage(), get_ai_stats()"""

from activity import record_ai_usage, get_ai_stats, _ai_stats


def setup_function():
    """Reset stats before each test."""
    _ai_stats["total_calls"] = 0
    _ai_stats["total_input_tokens"] = 0
    _ai_stats["total_output_tokens"] = 0
    _ai_stats["estimated_cost_usd"] = 0.0


def test_record_ai_usage_basic():
    record_ai_usage(input_tokens=2000, output_tokens=1500)
    stats = get_ai_stats()
    assert stats["total_calls"] == 1
    assert stats["total_input_tokens"] == 2000
    assert stats["total_output_tokens"] == 1500


def test_record_ai_usage_cost_calculation():
    # Sonnet: $3/M input, $15/M output
    record_ai_usage(input_tokens=1_000_000, output_tokens=0)
    stats = get_ai_stats()
    assert abs(stats["estimated_cost_usd"] - 3.0) < 0.001

    setup_function()
    record_ai_usage(input_tokens=0, output_tokens=1_000_000)
    stats = get_ai_stats()
    assert abs(stats["estimated_cost_usd"] - 15.0) < 0.001


def test_record_ai_usage_accumulates():
    record_ai_usage(input_tokens=1000, output_tokens=500)
    record_ai_usage(input_tokens=2000, output_tokens=1000)
    stats = get_ai_stats()
    assert stats["total_calls"] == 2
    assert stats["total_input_tokens"] == 3000
    assert stats["total_output_tokens"] == 1500


def test_record_ai_usage_zero_tokens():
    record_ai_usage(input_tokens=0, output_tokens=0)
    stats = get_ai_stats()
    assert stats["total_calls"] == 1
    assert stats["estimated_cost_usd"] == 0.0


def test_get_ai_stats_returns_copy():
    record_ai_usage(input_tokens=100, output_tokens=50)
    stats = get_ai_stats()
    stats["total_calls"] = 999  # mutate the copy
    assert get_ai_stats()["total_calls"] == 1  # original unchanged
