"""LIVE-mode tool tests with the SDK mocked. Verifies the real call sequence
fires when live_trading is true, that SDK errors surface as structured
OrderResult(status='error'), and that a risk-cap settings change resets the
RiskManager singleton."""

from __future__ import annotations

import pytest
from conftest import build_fake_client

from hyperliquid_trading_mcp import app, settings
from hyperliquid_trading_mcp.tools import orders, settings_tools


@pytest.fixture
def live(fake_client):
    """fake_client injected + live_trading flipped on (hermetic settings file)."""
    settings.update({"live_trading": True})
    return fake_client


# --------------------------------------------------------- live call sequence


async def test_place_market_order_live_fires_full_sequence(live):
    out = await orders.place_market_order("BTC", "buy", 50.0, tp_price=110.0)
    assert out.status == "ok" and out.mode == "LIVE"
    live.update_leverage.assert_awaited_once()
    live.market_open.assert_awaited_once()
    live.place_stop_loss.assert_awaited_once()  # mandatory SL always attached
    live.place_take_profit.assert_awaited_once()  # tp_price supplied


async def test_place_limit_order_live_uses_brackets(live):
    out = await orders.place_limit_order("BTC", "sell", 50.0, 101.0, sl_price=105.0)
    assert out.status == "ok" and out.mode == "LIVE"
    live.update_leverage.assert_awaited_once()
    live.limit_order_with_brackets.assert_awaited_once()


async def test_close_position_live_calls_market_close(live):
    out = await orders.close_position("BTC")
    assert out.status == "ok" and out.mode == "LIVE"
    live.market_close.assert_awaited_once_with("BTC")


async def test_set_leverage_live_calls_update_leverage(live):
    out = await orders.set_leverage("BTC", 5)
    assert out.status == "ok"
    live.update_leverage.assert_awaited_once()


# ------------------------------------------------------------ error handling


async def test_live_sdk_error_returns_structured_error(live):
    live.market_open.side_effect = RuntimeError("exchange down")
    out = await orders.place_market_order("BTC", "buy", 50.0)
    assert out.status == "error" and out.mode == "LIVE"
    assert "exchange down" in out.reason


async def test_force_close_continues_after_one_failure(live):
    live.get_user_state.return_value = {
        "balance": 1000.0,
        "total_value": 900.0,
        "positions": [
            {"coin": "BTC", "entryPx": 100.0, "szi": 1.0, "pnl": -30.0},
            {"coin": "ETH", "entryPx": 100.0, "szi": 1.0, "pnl": -30.0},
        ],
    }
    live.market_close.side_effect = [RuntimeError("nope"), {"closed": True}]
    out = await orders.force_close_losing_positions()
    closed = out.model_dump()["closed"]
    assert len(closed) == 2
    statuses = {c["coin"]: c["status"] for c in closed}
    assert statuses["BTC"] == "error" and statuses["ETH"] == "closed"


# -------------------------------------------------- risk singleton reset (R1)


async def test_risk_cap_change_resets_risk_singleton(fake_client):
    risk = app._get_risk()
    risk.circuit_breaker_active = True
    await settings_tools.update_settings({"max_leverage": 5})
    assert app._risk is None  # reset on risk-cap change
    assert app._get_risk().circuit_breaker_active is False


async def test_non_risk_setting_keeps_risk_singleton(fake_client):
    risk = app._get_risk()
    await settings_tools.update_settings({"live_trading": True})
    assert app._risk is risk  # live_trading is not a risk cap — no reset


async def test_reset_settings_clears_risk_singleton(fake_client):
    risk = app._get_risk()
    risk.circuit_breaker_active = True
    await settings_tools.reset_settings()
    assert app._risk is None


# ------------------------------------------ stale-client network rebuild (R3)


async def test_get_client_rebuilds_when_network_changes(fake_client, monkeypatch):
    # fake_client is built for mainnet and cached; settings now want testnet
    settings.update({"network": "testnet"})  # via module, bypassing the tool's reset_client
    rebuilt = build_fake_client()
    rebuilt.network = "testnet"
    monkeypatch.setattr(app, "HyperliquidClient", lambda: rebuilt)
    got = app._get_client()
    assert got is rebuilt and got.network == "testnet"
