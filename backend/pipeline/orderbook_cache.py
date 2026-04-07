"""
Caches order book depth from the CLOB API for dynamic slippage estimation.
Used by the paper trading engine to compute realistic slippage based on
position size relative to available liquidity.
"""

import asyncio
import time
from datetime import datetime, timezone

import httpx
import structlog

from config import get_settings

log = structlog.get_logger()
settings = get_settings()

# In-memory cache: token_id -> {bids, asks, updated_at}
_book_cache: dict[str, dict] = {}
CACHE_TTL = 30  # seconds
REFRESH_INTERVAL = 60  # seconds


async def fetch_orderbook(client: httpx.AsyncClient, token_id: str) -> dict | None:
    """Fetch order book for a token from CLOB API."""
    try:
        url = f"{settings.clob_api_url}/book"
        params = {"token_id": token_id}
        resp = await client.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            log.warning("orderbook_rate_limited", token=token_id[:10])
        return None
    except Exception as e:
        log.error("orderbook_fetch_error", token=token_id[:10], error=str(e))
        return None


def parse_book(raw: dict) -> dict:
    """Parse order book into sorted bids and asks with cumulative depth."""
    bids = []
    asks = []

    for bid in raw.get("bids", []):
        try:
            price = float(bid.get("price", 0))
            size = float(bid.get("size", 0))
            if price > 0 and size > 0:
                bids.append({"price": price, "size": size})
        except (TypeError, ValueError):
            continue

    for ask in raw.get("asks", []):
        try:
            price = float(ask.get("price", 0))
            size = float(ask.get("size", 0))
            if price > 0 and size > 0:
                asks.append({"price": price, "size": size})
        except (TypeError, ValueError):
            continue

    # Sort: bids descending by price, asks ascending
    bids.sort(key=lambda x: x["price"], reverse=True)
    asks.sort(key=lambda x: x["price"])

    return {"bids": bids, "asks": asks, "updated_at": time.monotonic()}


def get_cached_book(token_id: str) -> dict | None:
    """Get cached order book if still fresh."""
    entry = _book_cache.get(token_id)
    if entry and (time.monotonic() - entry["updated_at"]) < CACHE_TTL:
        return entry
    return None


def estimate_slippage(token_id: str, usd_amount: float, side: str = "BUY") -> float:
    """
    Estimate slippage for a given trade size based on order book depth.

    Returns slippage as a fraction (e.g., 0.02 = 2%).
    Falls back to a conservative estimate if no book data is available.
    """
    book = get_cached_book(token_id)
    if not book:
        # Conservative fallback based on trade size
        if usd_amount < 1000:
            return 0.015
        elif usd_amount < 5000:
            return 0.02
        elif usd_amount < 20000:
            return 0.03
        else:
            return 0.05

    levels = book["asks"] if side == "BUY" else book["bids"]
    if not levels:
        return 0.03

    # Walk the book to fill our order
    remaining_usd = usd_amount
    total_cost = 0.0
    total_shares = 0.0
    best_price = levels[0]["price"]

    for level in levels:
        level_usd = level["price"] * level["size"]
        if remaining_usd <= level_usd:
            shares = remaining_usd / level["price"]
            total_cost += remaining_usd
            total_shares += shares
            remaining_usd = 0
            break
        else:
            total_cost += level_usd
            total_shares += level["size"]
            remaining_usd -= level_usd

    if remaining_usd > 0:
        # Not enough liquidity — high slippage
        return 0.05

    if total_shares == 0:
        return 0.03

    avg_price = total_cost / total_shares
    slippage = (avg_price - best_price) / best_price if best_price > 0 else 0.03
    return max(slippage, 0.005)  # Minimum 0.5% slippage


async def update_cache_for_tokens(token_ids: list[str]):
    """Update order book cache for a list of tokens."""
    async with httpx.AsyncClient(timeout=10) as client:
        for token_id in token_ids:
            raw = await fetch_orderbook(client, token_id)
            if raw:
                _book_cache[token_id] = parse_book(raw)
            await asyncio.sleep(0.1)  # Respect rate limits


async def run_orderbook_cache(token_ids_fn, interval_seconds: int = REFRESH_INTERVAL):
    """Run order book cache refresh loop.

    token_ids_fn: async callable that returns list of token IDs to cache.
    Only caches books for markets with active signals or high volume.
    """
    log.info("orderbook_cache_starting", interval=interval_seconds)
    while True:
        try:
            token_ids = await token_ids_fn()
            if token_ids:
                await update_cache_for_tokens(token_ids[:50])  # Limit to avoid rate limits
                log.info("orderbook_cache_updated", tokens=len(token_ids[:50]))
        except Exception as e:
            log.error("orderbook_cache_error", error=str(e))
        await asyncio.sleep(interval_seconds)
