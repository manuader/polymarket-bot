"""Tests for trading/slippage_model.py"""

from unittest.mock import patch
from trading.slippage_model import compute_entry_price, compute_exit_price


@patch("trading.slippage_model.estimate_slippage", return_value=0.02)
def test_compute_entry_price_buy(mock_slip):
    price = compute_entry_price(0.50, "token1", 5000, "BUY")
    assert abs(price - 0.51) < 0.001  # 0.50 * 1.02 = 0.51


@patch("trading.slippage_model.estimate_slippage", return_value=0.02)
def test_compute_entry_price_sell(mock_slip):
    price = compute_entry_price(0.50, "token1", 5000, "SELL")
    assert abs(price - 0.49) < 0.001  # 0.50 * 0.98 = 0.49


@patch("trading.slippage_model.estimate_slippage", return_value=0.02)
def test_compute_entry_price_clamped_high(mock_slip):
    # Very high price + slippage should clamp to 0.99
    price = compute_entry_price(0.98, "token1", 5000, "BUY")
    assert price <= 0.99


@patch("trading.slippage_model.estimate_slippage", return_value=0.05)
def test_compute_entry_price_clamped_low(mock_slip):
    # Very low price - slippage should clamp to 0.01
    price = compute_entry_price(0.02, "token1", 5000, "SELL")
    assert price >= 0.01


@patch("trading.slippage_model.estimate_slippage", return_value=0.03)
def test_compute_exit_price(mock_slip):
    price = compute_exit_price(0.60, "token1", 5000)
    assert abs(price - 0.582) < 0.001  # 0.60 * 0.97
