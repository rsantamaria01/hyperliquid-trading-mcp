"""Tool-layer tests in DRY-RUN with the SDK mocked.

Proves each tool returns its declared Pydantic contract and that order tools
place no real orders while `live_trading` is false."""

from __future__ import annotations

from pydantic import BaseModel

from hyperliquid_trading_mcp.models import (
    AccountState,
    CandlesResult,
    MarketContext,
    OrderBook,
    OrderResult,
    PriceResult,
    RiskLimits,
    TradingMode,
    ValidateTradeResult,
)
from hyperliquid_trading_mcp.tools import account, market, meta, orders, risk, settings_tools

# ------------------------------------------------------------------ read tools


async def test_get_current_price_returns_model(fake_client):
    out = await market.get_current_price("BTC")
    assert isinstance(out, PriceResult) and out.price == 100.0


async def test_get_candles_wraps_candles(fake_client):
    fake_client.get_candles.return_value = [
        {"t": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}
    ]
    out = await market.get_candles("BTC", "5m", 1)
    assert isinstance(out, CandlesResult) and out.count == 1
    assert out.candles[0].close == 1.5


async def test_get_market_context_returns_model(fake_client):
    out = await market.get_market_context("BTC")
    assert isinstance(out, MarketContext) and out.current_price == 100.0
    assert isinstance(out.indicators, dict)


async def test_get_order_book_returns_model(fake_client):
    out = await market.get_order_book("BTC")
    assert isinstance(out, OrderBook) and out.bids[0].px == 99.0


async def test_get_account_state_returns_model(fake_client):
    out = await account.get_account_state()
    assert isinstance(out, AccountState) and out.balance == 1000.0


async def test_get_risk_limits_returns_model(fake_client):
    out = await risk.get_risk_limits()
    assert isinstance(out, RiskLimits) and out.max_leverage == 10


async def test_trading_mode_reports_dry_run(fake_client):
    out = await meta.trading_mode()
    assert isinstance(out, TradingMode)
    assert out.mode == "DRY-RUN" and out.live_trading is False


async def test_get_settings_returns_model(fake_client):
    out = await settings_tools.get_settings()
    assert out.settings["live_trading"] is False


# ------------------------------------------------------------ validate / risk


async def test_validate_trade_allows_in_cap_trade(fake_client):
    out = await risk.validate_trade("BTC", "long", 50.0)
    assert isinstance(out, ValidateTradeResult)
    assert out.allowed and out.action_canonical == "buy"
    assert out.trade["sl_price"] == 95.0  # mandatory SL applied


# --------------------------------------------------- DRY-RUN order safety net


async def test_place_market_order_dry_run_places_nothing(fake_client):
    out = await orders.place_market_order("BTC", "buy", 50.0)
    assert isinstance(out, OrderResult)
    assert out.status == "ok" and out.mode == "DRY-RUN"
    assert out.model_dump()["simulated_entry"]["side"] == "buy"
    # the safety property: no real exchange calls
    fake_client.market_open.assert_not_called()
    fake_client.update_leverage.assert_not_called()
    fake_client.place_stop_loss.assert_not_called()


async def test_place_limit_order_dry_run_places_nothing(fake_client):
    out = await orders.place_limit_order("BTC", "sell", 50.0, 101.0)
    assert out.mode == "DRY-RUN"
    fake_client.limit_order_with_brackets.assert_not_called()
    fake_client.update_leverage.assert_not_called()


async def test_close_position_dry_run(fake_client):
    out = await orders.close_position("BTC")
    assert out.mode == "DRY-RUN"
    fake_client.market_close.assert_not_called()


async def test_cancel_all_orders_dry_run(fake_client):
    out = await orders.cancel_all_orders("BTC")
    assert out.mode == "DRY-RUN"
    # the LIVE path calls client.cancel_all_orders — assert that, not cancel_order
    fake_client.cancel_all_orders.assert_not_called()


async def test_modify_order_dry_run(fake_client):
    out = await orders.modify_order("BTC", 1, "buy", 0.1, 100.0)
    assert out.mode == "DRY-RUN"
    fake_client.modify_order.assert_not_called()


async def test_set_stop_loss_dry_run(fake_client):
    out = await orders.set_stop_loss("BTC", True, 0.1, 95.0)
    assert out.mode == "DRY-RUN"
    fake_client.place_stop_loss.assert_not_called()


async def test_set_take_profit_dry_run(fake_client):
    out = await orders.set_take_profit("BTC", True, 0.1, 110.0)
    assert out.mode == "DRY-RUN"
    fake_client.place_take_profit.assert_not_called()


async def test_set_leverage_dry_run(fake_client):
    out = await orders.set_leverage("BTC", 5)
    assert out.mode == "DRY-RUN"
    fake_client.update_leverage.assert_not_called()


async def test_force_close_losing_positions_dry_run(fake_client):
    # a position 25% underwater (> 20% max_loss threshold) should be targeted
    fake_client.get_user_state.return_value = {
        "balance": 1000.0,
        "total_value": 975.0,
        "positions": [{"coin": "BTC", "entryPx": 100.0, "szi": 1.0, "pnl": -25.0}],
    }
    out = await orders.force_close_losing_positions()
    assert out.status == "ok" and out.mode == "DRY-RUN"
    closed = out.model_dump()["closed"]
    assert len(closed) == 1 and closed[0]["coin"] == "BTC" and closed[0]["status"] == "DRY-RUN"
    fake_client.market_close.assert_not_called()


async def test_invalid_side_is_rejected_without_client_calls(fake_client):
    out = await orders.place_market_order("BTC", "sideways", 50.0)
    assert out.status == "error" and "buy/sell" in out.reason
    fake_client.get_user_state.assert_not_called()


async def test_order_rejected_when_account_value_zero(fake_client):
    fake_client.get_user_state.return_value = {
        "balance": 0.0,
        "total_value": 0.0,
        "positions": [],
    }
    out = await orders.place_market_order("BTC", "buy", 50.0)
    assert out.status == "rejected"
    fake_client.market_open.assert_not_called()


# ----------------------------------------------------- broad contract sweep


async def test_all_read_tools_return_base_models(fake_client):
    results = [
        await market.get_recent_trades("BTC"),
        await account.get_open_orders(),
        await account.get_recent_fills(),
        await account.get_order_status(1),
        await account.get_user_funding(),
        await account.get_historical_funding("BTC"),
        await account.get_vault_details("0xvault"),
        await account.get_vault_performance("0xvault"),
        await risk.check_losing_positions(),
        await meta.get_server_time(),
    ]
    assert all(isinstance(r, BaseModel) for r in results)
    # spot-check propagated values, not just types
    assert results[0].trades == [{"px": 100.0, "sz": 0.5}]  # get_recent_trades
    assert results[3].oid == 1  # get_order_status
    assert results[4].count == 1  # get_user_funding
    assert results[8].to_close == []  # check_losing_positions
