"""Tests for pipeline/orderbook_cache.py — parse_book(), estimate_slippage()"""

from pipeline.orderbook_cache import parse_book, estimate_slippage, _book_cache
import time


def test_parse_book_sorts_correctly():
    raw = {
        "bids": [
            {"price": 0.48, "size": 2000},
            {"price": 0.50, "size": 500},
            {"price": 0.49, "size": 1000},
        ],
        "asks": [
            {"price": 0.53, "size": 2000},
            {"price": 0.51, "size": 500},
            {"price": 0.52, "size": 1000},
        ],
    }
    result = parse_book(raw)
    # Bids: descending by price
    assert result["bids"][0]["price"] == 0.50
    assert result["bids"][1]["price"] == 0.49
    assert result["bids"][2]["price"] == 0.48
    # Asks: ascending by price
    assert result["asks"][0]["price"] == 0.51
    assert result["asks"][1]["price"] == 0.52
    assert result["asks"][2]["price"] == 0.53
    assert "updated_at" in result


def test_parse_book_filters_invalid_entries():
    raw = {
        "bids": [
            {"price": 0.50, "size": 500},
            {"price": 0, "size": 100},      # invalid
            {"price": 0.49, "size": -10},    # invalid
        ],
        "asks": [{"price": 0.51, "size": 500}],
    }
    result = parse_book(raw)
    assert len(result["bids"]) == 1
    assert result["bids"][0]["price"] == 0.50


def test_parse_book_empty():
    result = parse_book({"bids": [], "asks": []})
    assert result["bids"] == []
    assert result["asks"] == []


def test_estimate_slippage_no_cache_fallback():
    # No cache entry → uses fallback table
    slippage = estimate_slippage("unknown_token", 500, "BUY")
    assert slippage == 0.015  # < $1000

    slippage = estimate_slippage("unknown_token", 3000, "BUY")
    assert slippage == 0.02  # $1k-$5k

    slippage = estimate_slippage("unknown_token", 10000, "BUY")
    assert slippage == 0.03  # $5k-$20k

    slippage = estimate_slippage("unknown_token", 50000, "BUY")
    assert slippage == 0.05  # > $20k


def test_estimate_slippage_with_cached_book():
    # Inject a cached book
    token = "test_cached_token"
    _book_cache[token] = {
        "bids": [
            {"price": 0.50, "size": 1000},
            {"price": 0.49, "size": 2000},
        ],
        "asks": [
            {"price": 0.51, "size": 1000},  # $510 available
            {"price": 0.52, "size": 2000},  # $1040 available
            {"price": 0.53, "size": 5000},  # $2650 available
        ],
        "updated_at": time.monotonic(),  # fresh
    }

    # BUY $400 — should fill at best ask 0.51
    slippage = estimate_slippage(token, 400, "BUY")
    assert slippage >= 0.005  # minimum slippage

    # BUY $2000 — should walk multiple levels
    slippage = estimate_slippage(token, 2000, "BUY")
    assert slippage > 0.005  # more slippage from walking book

    # Cleanup
    del _book_cache[token]


def test_estimate_slippage_insufficient_liquidity():
    token = "test_thin_token"
    _book_cache[token] = {
        "bids": [],
        "asks": [{"price": 0.51, "size": 10}],  # only $5.10 available
        "updated_at": time.monotonic(),
    }

    slippage = estimate_slippage(token, 10000, "BUY")
    assert slippage == 0.05  # max slippage when insufficient

    del _book_cache[token]
