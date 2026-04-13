"""Tests for trading/position_sizer.py — kelly_fraction(), score_to_bracket()"""

from trading.position_sizer import kelly_fraction, score_to_bracket, BRACKET_PRIORS


def test_kelly_fraction_basic():
    # p=0.80, b=3.0 → f* = (0.80*3 - 0.20)/3 = 2.2/3 = 0.733 → half = 0.367
    result = kelly_fraction(0.80, 3.0)
    assert abs(result - 0.3667) < 0.001


def test_kelly_fraction_50_50():
    # p=0.50, b=2.0 → f* = (0.50*2 - 0.50)/2 = 0.25 → half = 0.125
    result = kelly_fraction(0.50, 2.0)
    assert abs(result - 0.125) < 0.001


def test_kelly_fraction_losing_edge():
    # p=0.30, b=1.0 → f* = (0.30*1 - 0.70)/1 = -0.40 → half = -0.20 → clamped to 0
    result = kelly_fraction(0.30, 1.0)
    assert result == 0


def test_kelly_fraction_perfect_edge():
    # p=1.0, b=2.0 → f* = (1.0*2 - 0)/2 = 1.0 → half = 0.50
    result = kelly_fraction(1.0, 2.0)
    assert abs(result - 0.50) < 0.001


def test_kelly_fraction_zero_payoff():
    result = kelly_fraction(0.80, 0)
    assert result == 0


def test_score_to_bracket():
    assert score_to_bracket(1) == "5-6"
    assert score_to_bracket(5) == "5-6"
    assert score_to_bracket(6) == "5-6"
    assert score_to_bracket(7) == "7"
    assert score_to_bracket(8) == "8-10"
    assert score_to_bracket(9) == "8-10"
    assert score_to_bracket(10) == "8-10"


def test_bracket_priors_exist():
    for bracket in ["5-6", "7", "8-10"]:
        assert bracket in BRACKET_PRIORS
        prior = BRACKET_PRIORS[bracket]
        assert 0 < prior["win_prob"] <= 1
        assert prior["avg_payoff_ratio"] > 0
        assert prior["max_pct"] > 0


def test_bracket_priors_increasing_confidence():
    # Higher brackets should have higher win probability
    assert BRACKET_PRIORS["5-6"]["win_prob"] < BRACKET_PRIORS["7"]["win_prob"]
    assert BRACKET_PRIORS["7"]["win_prob"] < BRACKET_PRIORS["8-10"]["win_prob"]

    # Higher brackets should allow larger positions
    assert BRACKET_PRIORS["5-6"]["max_pct"] < BRACKET_PRIORS["7"]["max_pct"]
    assert BRACKET_PRIORS["7"]["max_pct"] < BRACKET_PRIORS["8-10"]["max_pct"]
