"""Tests for detection/signal_manager.py — compute_composite_score()"""

from detection.signal_manager import compute_composite_score
from detection.heuristic_filter import RuleHit


def _hit(name="TEST_RULE", priority=7, market_id="0x1", direction="YES"):
    return RuleHit(rule_name=name, priority=priority, market_id=market_id, direction=direction)


def test_composite_score_no_hits():
    score, conf, rec = compute_composite_score([], None)
    assert score == 1
    assert conf == 0.0
    assert rec == "SKIP"


def test_composite_score_single_hit_no_ai():
    score, conf, rec = compute_composite_score([_hit(priority=7)], None)
    assert score == 7
    assert conf == 0.3  # Low confidence without AI
    assert rec == "HOLD"  # Never recommend buying without AI


def test_composite_score_multiple_hits_bonus():
    hits = [_hit(priority=7), _hit(name="RULE2", priority=6)]
    score, conf, rec = compute_composite_score(hits, None)
    assert score == 8  # 7 (max) + 1 (2+ rules)


def test_composite_score_four_plus_hits():
    hits = [_hit(priority=7), _hit(name="R2", priority=6), _hit(name="R3", priority=5), _hit(name="R4", priority=5)]
    score, conf, rec = compute_composite_score(hits, None)
    assert score == 9  # 7 + 1 (2+) + 1 (4+)


def test_composite_score_clamps_to_10():
    hits = [_hit(priority=9), _hit(name="R2", priority=8), _hit(name="R3", priority=7), _hit(name="R4", priority=7)]
    score, conf, rec = compute_composite_score(hits, None)
    assert score <= 10


def test_composite_score_with_ai_strong_buy():
    ai = {"insider_score": 9, "confidence": 0.85, "recommendation": "STRONG_BUY", "news_justification": False}
    score, conf, rec = compute_composite_score([_hit(priority=7)], ai)
    assert score >= 8  # 7 + 1 (AI higher) + 1 (STRONG_BUY)
    assert conf == 0.85
    assert rec == "STRONG_BUY"


def test_composite_score_with_ai_skip():
    ai = {"insider_score": 3, "confidence": 0.3, "recommendation": "SKIP", "news_justification": False}
    score, conf, rec = compute_composite_score([_hit(priority=7)], ai)
    assert score <= 6  # 7 - 1 (AI lower) - 2 (SKIP) = 4, clamped
    assert rec == "SKIP"


def test_composite_score_news_justification_reduces():
    ai = {"insider_score": 7, "confidence": 0.7, "recommendation": "BUY", "news_justification": True}
    score, conf, rec = compute_composite_score([_hit(priority=7)], ai)
    # news_justification reduces by 2
    assert score <= 6


def test_composite_score_recommendation_without_ai():
    # Without AI, always HOLD — never recommend buying without AI confirmation
    score, conf, rec = compute_composite_score([_hit(priority=9)], None)
    assert rec == "HOLD"
    assert conf == 0.3

    score, conf, rec = compute_composite_score([_hit(priority=4)], None)
    assert rec == "HOLD"
