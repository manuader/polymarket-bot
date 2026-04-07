"""
Dynamic slippage model based on order book depth and trade size.
Replaces the SPEC's fixed 2% with a realistic estimate.
"""

from pipeline.orderbook_cache import estimate_slippage


def compute_entry_price(
    market_price: float,
    token_id: str,
    usd_amount: float,
    side: str = "BUY",
) -> float:
    """
    Compute realistic entry price including slippage.

    Args:
        market_price: Current market price (0.01-0.99)
        token_id: Token ID for order book lookup
        usd_amount: Size of the trade in USD
        side: BUY or SELL

    Returns:
        Adjusted price after slippage
    """
    slippage = estimate_slippage(token_id, usd_amount, side)

    if side == "BUY":
        entry = market_price * (1 + slippage)
    else:
        entry = market_price * (1 - slippage)

    # Clamp to valid price range
    return max(0.01, min(0.99, entry))


def compute_exit_price(
    market_price: float,
    token_id: str,
    usd_amount: float,
) -> float:
    """Compute realistic exit price (selling) including slippage."""
    return compute_entry_price(market_price, token_id, usd_amount, side="SELL")
