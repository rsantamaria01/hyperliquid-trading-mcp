"""Resilience of read-only market-data fetches under bursty fan-out.

A trade-loop tick fires many get_market_context calls at once; transient 429/502
or the SDK's own IndexError on a truncated body must be retried (not crash the
tool), the lazy meta fetch must be serialized (one network call, not a stampede),
and a persistently-failing fetch must degrade to empty/zero so analysis HOLDs
rather than raising.

Bare clients are built with object.__new__ (skipping __init__, which needs keys +
network) and the concurrency primitives seeded inside the running loop.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from hyperliquid_trading_mcp.hyperliquid_client import HyperliquidClient


def _bare_client(info: MagicMock | None = None) -> HyperliquidClient:
    c = object.__new__(HyperliquidClient)
    c._meta_cache = None
    c._hip3_meta_cache = {}
    c._read_sem = asyncio.Semaphore(5)
    c._meta_lock = asyncio.Lock()
    c.info = info or MagicMock()
    return c


async def test_run_read_retries_then_succeeds():
    c = _bare_client()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise IndexError("list index out of range")  # the SDK's failure shape
        return "ok"

    assert await c._run_read(flaky, base_delay=0) == "ok"
    assert calls["n"] == 3


async def test_run_read_raises_after_exhaustion():
    c = _bare_client()

    def always_fail():
        raise IndexError("list index out of range")

    with pytest.raises(IndexError):
        await c._run_read(always_fail, retries=3, base_delay=0)


async def test_get_candles_degrades_to_empty_on_persistent_failure():
    info = MagicMock()
    info.candles_snapshot = MagicMock(side_effect=IndexError("list index out of range"))
    c = _bare_client(info)
    # Must not raise — degrades to [] so compute_summary reports "insufficient".
    assert await c.get_candles("BTC", "5m", 200) == []


async def test_get_candles_non_list_body_degrades_to_empty():
    info = MagicMock()
    info.candles_snapshot = MagicMock(return_value={"error": "rate limited"})
    c = _bare_client(info)
    assert await c.get_candles("BTC", "5m", 200) == []


async def test_get_current_price_degrades_to_zero():
    info = MagicMock()
    info.all_mids = MagicMock(side_effect=IndexError("list index out of range"))
    c = _bare_client(info)
    assert await c.get_current_price("BTC") == 0.0


async def test_meta_fetched_once_under_concurrency():
    info = MagicMock()
    calls = {"n": 0}

    def meta_once():
        calls["n"] += 1
        time.sleep(0.01)  # widen the race window
        return [{"universe": [{"name": "BTC", "szDecimals": 3}]}, [{"openInterest": "1"}]]

    info.meta_and_asset_ctxs = meta_once
    c = _bare_client(info)

    # 20 concurrent callers; the double-checked lock must collapse to one fetch.
    results = await asyncio.gather(*[c._meta_for("BTC") for _ in range(20)])
    assert calls["n"] == 1
    assert all(r is results[0] for r in results)


def test_read_concurrency_is_a_setting():
    """read_concurrency is a persistent setting (not an env var), editable like
    the risk caps, and the client helper reads it."""
    from hyperliquid_trading_mcp import settings
    from hyperliquid_trading_mcp.hyperliquid_client import _read_concurrency

    assert "read_concurrency" in settings.EDITABLE
    assert _read_concurrency() == 5  # default
    settings.update({"read_concurrency": 3})
    assert _read_concurrency() == 3
