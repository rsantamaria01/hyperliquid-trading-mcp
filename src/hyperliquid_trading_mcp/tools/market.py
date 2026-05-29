"""Market-data tools: price, candles, market context, order book, trades."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..app import _get_client, mcp
from ..indicators import compute_summary
from ..models import (
    Candle,
    CandlesResult,
    MarketContext,
    OrderBook,
    PriceResult,
    RecentTrades,
)


@mcp.tool()
async def get_current_price(asset: str) -> PriceResult:
    """Latest mid-price for an asset. e.g. "BTC", "ETH", "xyz:GOLD", "xyz:TSLA"."""
    px = await _get_client().get_current_price(asset)
    return PriceResult(asset=asset, price=px)


@mcp.tool()
async def get_candles(
    asset: str,
    interval: str = "5m",
    count: Annotated[int, Field(ge=1, le=5000)] = 100,
) -> CandlesResult:
    """Fetch recent OHLCV candles.

    interval: "1m", "5m", "15m", "1h", "4h", "1d", etc. count: 1..5000.
    """
    candles = await _get_client().get_candles(asset, interval, count)
    return CandlesResult(
        asset=asset,
        interval=interval,
        count=len(candles),
        candles=[Candle(**c) for c in candles],
    )


@mcp.tool()
async def get_market_context(
    asset: str,
    interval: str = "5m",
    count: Annotated[int, Field(ge=1, le=5000)] = 200,
) -> MarketContext:
    """One-shot bundle for analysis: price + indicators (latest values) + OI +
    funding rate + last 20 candles."""
    client = _get_client()
    candles = await client.get_candles(asset, interval, count)
    indicators = compute_summary(candles)
    return MarketContext(
        asset=asset,
        interval=interval,
        current_price=await client.get_current_price(asset),
        open_interest=await client.get_open_interest(asset),
        funding_rate=await client.get_funding_rate(asset),
        indicators=indicators,
        recent_candles=[Candle(**c) for c in candles[-20:]],
    )


@mcp.tool()
async def get_order_book(
    asset: str,
    depth: Annotated[int, Field(ge=1)] = 20,
) -> OrderBook:
    """Order book — top `depth` levels of bids and asks. Useful for spread,
    liquidity, and limit-price placement."""
    book = await _get_client().get_order_book(asset, depth)
    return OrderBook(**book)


@mcp.tool()
async def get_recent_trades(
    asset: str,
    limit: Annotated[int, Field(ge=1)] = 50,
) -> RecentTrades:
    """Recent public trades on the asset (the tape). Useful for momentum read."""
    trades = await _get_client().get_recent_trades(asset, limit)
    return RecentTrades(asset=asset, trades=trades, count=len(trades))
