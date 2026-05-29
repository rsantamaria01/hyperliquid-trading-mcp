"""Shared test fixtures.

The Hyperliquid SDK is never touched: tools reach the exchange through
``app._get_client()``, so injecting a ``MagicMock`` client into ``app._client``
exercises every tool offline, with no network and no keys. The hermetic
settings fixture isolates the persistent settings file to a tmp path and
guarantees DRY-RUN.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hyperliquid_trading_mcp import app, settings


@pytest.fixture(autouse=True)
def hermetic_settings(tmp_path, monkeypatch):
    """Isolate settings to a tmp file and force DRY-RUN; reset singletons."""
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    monkeypatch.delenv("HYPERLIQUID_NETWORK", raising=False)
    app._client = None
    app._risk = None
    yield
    app._client = None
    app._risk = None


def build_fake_client() -> MagicMock:
    """A fake HyperliquidClient with canned async responses."""
    c = MagicMock(name="HyperliquidClient")
    c.wallet = MagicMock()
    c.wallet.address = "0xSIGNER"
    c.account_address = "0xACCOUNT"

    # read paths
    c.get_user_state = AsyncMock(
        return_value={"balance": 1000.0, "total_value": 1000.0, "positions": []}
    )
    c.get_current_price = AsyncMock(return_value=100.0)
    c.get_candles = AsyncMock(return_value=[])
    c.get_open_interest = AsyncMock(return_value=123.0)
    c.get_funding_rate = AsyncMock(return_value=0.0001)
    c.get_order_book = AsyncMock(
        return_value={
            "asset": "BTC",
            "bids": [{"px": 99.0, "sz": 1.0, "n": 2}],
            "asks": [{"px": 101.0, "sz": 1.0, "n": 3}],
            "depth": 20,
        }
    )
    c.get_recent_trades = AsyncMock(return_value=[{"px": 100.0, "sz": 0.5}])
    c.get_open_orders = AsyncMock(return_value=[{"oid": 1}])
    c.get_recent_fills = AsyncMock(return_value=[{"oid": 1}])
    c.get_order_status = AsyncMock(return_value={"status": "open"})
    c.get_user_funding = AsyncMock(return_value=[{"delta": 1}])
    c.get_historical_funding = AsyncMock(return_value=[{"fundingRate": 0.0001}])
    c.get_vault_details = AsyncMock(return_value={"name": "vault"})
    c.get_vault_performance = AsyncMock(return_value={"pnl": 1.0})
    c.get_server_time = AsyncMock(return_value={"local_ms": 1, "rtt_ms": 2.0, "meta_ok": True})

    # mutation paths — must NOT be called in DRY-RUN
    c.update_leverage = AsyncMock(return_value={"status": "ok", "response": {}})
    c.market_open = AsyncMock(return_value={"filled": True})
    c.market_close = AsyncMock(return_value={"closed": True})
    c.place_stop_loss = AsyncMock(return_value={"sl": True})
    c.place_take_profit = AsyncMock(return_value={"tp": True})
    c.limit_order_with_brackets = AsyncMock(return_value={"order": True})
    c.modify_order = AsyncMock(return_value={"mod": True})
    c.cancel_order = AsyncMock(return_value={"cancel": True})
    c.cancel_all_orders = AsyncMock(return_value={"status": "ok", "cancelled": 2})
    return c


@pytest.fixture
def fake_client():
    """Inject a fake client into the app singleton; tools pick it up via _get_client()."""
    c = build_fake_client()
    app._client = c
    return c


def make_candles(n: int, start: float = 100.0, step: float = 1.0) -> list[dict]:
    """Synthetic OHLCV series for indicator tests."""
    candles = []
    price = start
    for i in range(n):
        o = price
        cl = price + step
        candles.append(
            {
                "t": i,
                "open": o,
                "high": max(o, cl) + 0.5,
                "low": min(o, cl) - 0.5,
                "close": cl,
                "volume": 10.0 + i,
            }
        )
        price = cl
    return candles
