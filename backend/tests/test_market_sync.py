"""Tests for pipeline/market_sync.py — parse_market()"""

from pipeline.market_sync import parse_market


def test_parse_market_with_tokens_array():
    raw = {
        "conditionId": "0xmarket1",
        "question": "Will BTC hit 100k?",
        "description": "Test market",
        "category": "Crypto",
        "endDate": "2026-12-31T23:59:59Z",
        "slug": "btc-100k",
        "tags": ["crypto", "btc"],
        "volume": 1500000,
        "liquidity": 250000,
        "active": True,
        "negRisk": False,
        "image": "https://img.png",
        "tokens": [
            {"token_id": "t1", "price": 0.62},
            {"token_id": "t2", "price": 0.38},
        ],
    }
    result = parse_market(raw)
    assert result["condition_id"] == "0xmarket1"
    assert result["question"] == "Will BTC hit 100k?"
    assert result["category"] == "Crypto"
    assert result["clob_token_ids"] == ["t1", "t2"]
    assert result["outcome_prices"] == [0.62, 0.38]
    assert result["volume"] == 1500000
    assert result["neg_risk"] is False


def test_parse_market_with_json_string_token_ids():
    raw = {
        "conditionId": "0xmarket2",
        "question": "Test?",
        "clobTokenIds": '["token_a", "token_b"]',
        "outcomePrices": '[0.55, 0.45]',
        "volume": 0,
        "liquidity": 0,
    }
    result = parse_market(raw)
    assert result["clob_token_ids"] == ["token_a", "token_b"]
    assert result["outcome_prices"] == [0.55, 0.45]


def test_parse_market_missing_end_date():
    raw = {"conditionId": "0xm3", "question": "Test?"}
    result = parse_market(raw)
    assert result["end_date"] is None


def test_parse_market_malformed_volume():
    raw = {"conditionId": "0xm4", "question": "Test?", "volume": "invalid", "liquidity": None}
    result = parse_market(raw)
    assert result["volume"] == 0
    assert result["liquidity"] == 0


def test_parse_market_empty_condition_id():
    raw = {"question": "Test?"}
    result = parse_market(raw)
    assert result["condition_id"] == ""


def test_parse_market_tags_as_string():
    raw = {"conditionId": "0xm5", "question": "Test?", "tags": "crypto, politics"}
    result = parse_market(raw)
    assert "crypto" in result["tags"]
    assert "politics" in result["tags"]


def test_parse_market_end_date_parsing():
    raw = {"conditionId": "0xm6", "question": "Test?", "endDate": "2026-07-31T12:00:00Z"}
    result = parse_market(raw)
    assert result["end_date"] is not None
    assert result["end_date"].year == 2026
    assert result["end_date"].month == 7
