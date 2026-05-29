"""Price/size tick-rounding tests.

A bare HyperliquidClient is built with object.__new__ (skipping __init__, which
needs keys + network) and a pre-seeded meta cache, so the pure rounding logic is
exercised offline."""

from __future__ import annotations

from hyperliquid_trading_mcp.hyperliquid_client import HyperliquidClient


def client_with_meta(sz_decimals: int) -> HyperliquidClient:
    c = object.__new__(HyperliquidClient)
    c._meta_cache = [{"universe": [{"name": "BTC", "szDecimals": sz_decimals}]}]
    c._hip3_meta_cache = {}
    return c


async def test_round_price_applies_sig_figs_and_decimals():
    c = client_with_meta(3)  # max_decimals = 6 - 3 = 3
    # 100.123456 -> 3 decimals -> 100.123 -> 5 sig figs (3 int digits) -> 2 decimals -> 100.12
    assert await c.round_price("BTC", 100.123456) == 100.12


async def test_round_price_small_value_keeps_precision():
    c = client_with_meta(5)  # max_decimals = 1
    assert await c.round_price("BTC", 0.123456) == 0.1


async def test_round_price_nonpositive_returned_asis():
    c = client_with_meta(2)
    assert await c.round_price("BTC", 0) == 0
    assert await c.round_price("BTC", -5) == -5


async def test_round_size_uses_sz_decimals():
    c = client_with_meta(3)
    assert await c.round_size("BTC", 0.123456) == 0.123


async def test_round_size_unknown_asset_falls_back():
    c = client_with_meta(3)
    # asset not in universe -> default 8 decimals
    assert await c.round_size("DOGE", 0.123456789) == 0.12345679
