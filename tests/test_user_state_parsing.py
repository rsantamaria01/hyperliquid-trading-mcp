"""Parsing tests for HyperliquidClient.get_user_state.

The rest of the suite mocks ``get_user_state`` wholesale, so the mapping from a
raw Hyperliquid ``clearinghouseState`` payload to ``balance``/``total_value`` is
never exercised. These tests feed realistic raw payloads through the real
parser to guard against the zero-balance bug: ``accountValue`` lives under
``marginSummary``/``crossMarginSummary`` (never top-level), and ``withdrawable``
(free collateral) transiently reads ~0 in the tick right after a fill.
"""

from __future__ import annotations

import pytest

from hyperliquid_trading_mcp.hyperliquid_client import HyperliquidClient


def _make_client(monkeypatch, raw_state, price=0.79555):
    """A HyperliquidClient with SDK I/O stubbed — no network, no keys."""
    client = HyperliquidClient.__new__(HyperliquidClient)
    client.query_address = "0xACCOUNT"

    class _Info:
        def user_state(self, addr):
            return raw_state

    client.info = _Info()

    async def _run(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def _get_current_price(coin, *a, **k):
        return price

    monkeypatch.setattr(client, "_run", _run)
    monkeypatch.setattr(client, "get_current_price", _get_current_price)
    return client


# Mirrors the live SUI repro: stable accountValue 188.8444, but withdrawable
# transiently ~0 right after the fill, and one open SUI long.
RAW_WITH_POSITION = {
    "marginSummary": {
        "accountValue": "188.8444",
        "totalNtlPos": "10.97859",
        "totalRawUsd": "189.93",
        "totalMarginUsed": "1.097859",
    },
    "crossMarginSummary": {
        "accountValue": "188.8444",
        "totalNtlPos": "10.97859",
        "totalRawUsd": "189.93",
        "totalMarginUsed": "1.097859",
    },
    "crossMaintenanceMarginUsed": "0.54",
    "withdrawable": "0.0055",
    "assetPositions": [
        {
            "type": "oneWay",
            "position": {
                "coin": "SUI",
                "szi": "13.8",
                "entryPx": "0.79533",
                "positionValue": "10.97859",
                "unrealizedPnl": "0.003036",
                "marginUsed": "1.097859",
                "leverage": {"type": "cross", "value": 10},
            },
        }
    ],
    "time": 1700000000,
}


@pytest.mark.asyncio
async def test_total_value_uses_account_equity_not_withdrawable(monkeypatch):
    """Right after a fill, withdrawable ~0 must NOT collapse total_value/balance."""
    client = _make_client(monkeypatch, RAW_WITH_POSITION)
    out = await client.get_user_state()

    # True equity comes from marginSummary.accountValue, not the volatile
    # top-level withdrawable.
    assert out["total_value"] == pytest.approx(188.8444, abs=0.01)
    assert out["balance"] == pytest.approx(188.8444, abs=0.01)


@pytest.mark.asyncio
async def test_total_value_never_below_locked_margin(monkeypatch):
    """Invariant: account equity can never be less than margin locked in positions."""
    client = _make_client(monkeypatch, RAW_WITH_POSITION)
    out = await client.get_user_state()

    margin_used = sum(float(p["marginUsed"]) for p in out["positions"])
    assert out["total_value"] >= margin_used


@pytest.mark.asyncio
async def test_no_position_reads_account_value(monkeypatch):
    """With no position, equity comes straight from marginSummary.accountValue."""
    raw = {
        "marginSummary": {"accountValue": "189.9778", "totalMarginUsed": "0.0"},
        "crossMarginSummary": {"accountValue": "189.9778", "totalMarginUsed": "0.0"},
        "withdrawable": "189.9778",
        "assetPositions": [],
        "time": 1700000000,
    }
    client = _make_client(monkeypatch, raw)
    out = await client.get_user_state()

    assert out["balance"] == pytest.approx(189.9778, abs=0.01)
    assert out["total_value"] == pytest.approx(189.9778, abs=0.01)
    assert out["positions"] == []
