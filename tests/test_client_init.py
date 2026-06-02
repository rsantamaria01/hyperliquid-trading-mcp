"""Regression guard for the SDK spot-meta IndexError.

SDK 0.20.1's Info/Exchange build spot asset maps in __init__ and crash with
`IndexError: list index out of range` against current Hyperliquid mainnet spot
meta. We only trade perps, so HyperliquidClient must pass an empty spot_meta to
both to short-circuit that loop. These tests patch the SDK classes (so no
network) and assert the short-circuit is in place — the rest of the suite mocks
the client wholesale and never exercises __init__, which is how the crash
shipped unnoticed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hyperliquid_trading_mcp import hyperliquid_client as hc

DUMMY_KEY = "0x" + "11" * 32


@pytest.fixture
def patched_sdk(monkeypatch):
    monkeypatch.setenv("HYPERLIQUID_PRIVATE_KEY", DUMMY_KEY)
    monkeypatch.delenv("HYPERLIQUID_VAULT_ADDRESS", raising=False)
    info = MagicMock(name="Info")
    exchange = MagicMock(name="Exchange")
    monkeypatch.setattr(hc, "Info", info)
    monkeypatch.setattr(hc, "Exchange", exchange)
    return info, exchange


def test_info_gets_empty_spot_meta_and_skip_ws(patched_sdk):
    info, _ = patched_sdk
    hc.HyperliquidClient()
    _, kwargs = info.call_args
    assert kwargs.get("skip_ws") is True
    assert kwargs.get("spot_meta") == {"universe": [], "tokens": []}


def test_exchange_gets_empty_spot_meta(patched_sdk):
    _, exchange = patched_sdk
    hc.HyperliquidClient()
    _, kwargs = exchange.call_args
    assert kwargs.get("spot_meta") == {"universe": [], "tokens": []}


def test_missing_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("HYPERLIQUID_PRIVATE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="HYPERLIQUID_PRIVATE_KEY"):
        hc.HyperliquidClient()
