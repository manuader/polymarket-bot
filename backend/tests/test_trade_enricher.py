"""Tests for pipeline/trade_enricher.py — parse_trade()"""

from pipeline.trade_enricher import parse_trade


def test_parse_valid_trade():
    raw = {
        "conditionId": "0xabc123",
        "asset": "token_1",
        "timestamp": 1712500000,
        "price": 0.35,
        "size": 1000.0,
        "side": "BUY",
        "outcome": "Yes",
        "proxyWallet": "0xwallet1",
        "transactionHash": "0xtx1",
        "title": "Will X happen?",
        "slug": "will-x-happen",
        "eventSlug": "event-x",
    }
    result = parse_trade(raw)
    assert result is not None
    assert result["market_id"] == "0xabc123"
    assert result["token_id"] == "token_1"
    assert result["price"] == 0.35
    assert result["size"] == 1000.0
    assert result["usd_value"] == 350.0
    assert result["side"] == "BUY"
    assert result["outcome"] == "YES"  # normalized to uppercase
    assert result["taker_address"] == "0xwallet1"
    assert result["maker_address"] == ""
    assert result["ts_unix"] == 1712500000
    assert result["_title"] == "Will X happen?"
    assert result["_event_slug"] == "event-x"


def test_parse_trade_zero_price():
    raw = {"conditionId": "0xabc", "price": 0, "size": 100, "outcome": "Yes"}
    assert parse_trade(raw) is None


def test_parse_trade_zero_size():
    raw = {"conditionId": "0xabc", "price": 0.5, "size": 0, "outcome": "Yes"}
    assert parse_trade(raw) is None


def test_parse_trade_negative_price():
    raw = {"conditionId": "0xabc", "price": -0.5, "size": 100, "outcome": "Yes"}
    assert parse_trade(raw) is None


def test_parse_trade_missing_condition_id():
    raw = {"price": 0.5, "size": 100, "outcome": "Yes"}
    assert parse_trade(raw) is None


def test_parse_trade_empty_condition_id():
    raw = {"conditionId": "", "price": 0.5, "size": 100, "outcome": "Yes"}
    assert parse_trade(raw) is None


def test_parse_trade_missing_fields_uses_defaults():
    raw = {"conditionId": "0xabc", "price": 0.5, "size": 100}
    result = parse_trade(raw)
    assert result is not None
    assert result["side"] == "BUY"
    assert result["outcome"] == "YES"
    assert result["taker_address"] == ""
    assert result["_title"] == ""


def test_parse_trade_invalid_timestamp():
    raw = {
        "conditionId": "0xabc",
        "price": 0.5,
        "size": 100,
        "timestamp": "not_a_number",
    }
    result = parse_trade(raw)
    assert result is not None
    assert result["ts_unix"] == 0


def test_parse_trade_outcome_normalization():
    for outcome_in, expected in [("Yes", "YES"), ("No", "NO"), ("yes", "YES"), ("NO", "NO")]:
        raw = {"conditionId": "0xabc", "price": 0.5, "size": 100, "outcome": outcome_in}
        result = parse_trade(raw)
        assert result["outcome"] == expected


def test_parse_trade_usd_value_calculation():
    raw = {"conditionId": "0xabc", "price": 0.75, "size": 4000}
    result = parse_trade(raw)
    assert result["usd_value"] == 3000.0
