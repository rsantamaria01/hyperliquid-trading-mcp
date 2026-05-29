"""Risk-manager guard tests. Caps come from settings defaults (isolated by the
hermetic_settings fixture): max_position_pct=10, max_leverage=10,
max_total_exposure_pct=50, daily_loss_circuit_breaker_pct=10,
mandatory_sl_pct=5, max_concurrent_positions=10, min_balance_reserve_pct=20."""

from __future__ import annotations

from hyperliquid_trading_mcp.risk_manager import RiskManager

STATE = {"balance": 1000.0, "total_value": 1000.0, "positions": []}


def test_validate_trade_happy_path_sets_mandatory_sl():
    rm = RiskManager()
    trade = {"asset": "BTC", "action": "buy", "allocation_usd": 50.0, "current_price": 100.0}
    ok, reason, adjusted = rm.validate_trade(trade, STATE)
    assert ok and reason == ""
    # mandatory SL auto-set 5% below entry for a long
    assert adjusted["sl_price"] == 95.0


def test_long_alias_normalized():
    rm = RiskManager()
    ok, _, adjusted = rm.validate_trade(
        {"action": "long", "allocation_usd": 50.0, "current_price": 100.0}, STATE
    )
    assert ok and adjusted["action"] == "buy"


def test_min_allocation_bumped_to_eleven():
    rm = RiskManager()
    ok, _, adjusted = rm.validate_trade(
        {"action": "buy", "allocation_usd": 5.0, "current_price": 100.0}, STATE
    )
    assert ok and adjusted["allocation_usd"] == 11.0


def test_allocation_over_cap_is_capped_not_rejected():
    rm = RiskManager()
    # cap = 10% of 1000 = 100; ask for 500 -> capped to 100, still passes leverage
    ok, _, adjusted = rm.validate_trade(
        {"action": "buy", "allocation_usd": 500.0, "current_price": 100.0}, STATE
    )
    assert ok and adjusted["allocation_usd"] == 100.0


def test_zero_allocation_rejected():
    rm = RiskManager()
    ok, reason, _ = rm.validate_trade(
        {"action": "buy", "allocation_usd": 0.0, "current_price": 100.0}, STATE
    )
    assert not ok and "allocation" in reason.lower()


def test_unknown_action_rejected():
    rm = RiskManager()
    ok, reason, _ = rm.validate_trade(
        {"action": "sideways", "allocation_usd": 50.0, "current_price": 100.0}, STATE
    )
    assert not ok and "action" in reason.lower()


def test_hold_is_allowed_noop():
    rm = RiskManager()
    ok, _, adjusted = rm.validate_trade({"action": "hold"}, STATE)
    assert ok and adjusted["action"] == "hold"


def test_daily_drawdown_trips_circuit_breaker():
    rm = RiskManager()
    rm.check_daily_drawdown(1000.0)  # establishes daily high
    ok, reason = rm.check_daily_drawdown(889.0)  # ~11.1% drawdown > 10%
    assert not ok and "drawdown" in reason.lower()
    assert rm.circuit_breaker_active
    # once tripped, validate_trade is blocked
    ok2, reason2, _ = rm.validate_trade(
        {"action": "buy", "allocation_usd": 50.0, "current_price": 100.0},
        {"balance": 889.0, "total_value": 889.0, "positions": []},
    )
    assert not ok2 and "circuit breaker" in reason2.lower()


def test_balance_reserve_floor_rejects():
    rm = RiskManager()
    rm.record_initial_balance(1000.0)  # floor = 20% = 200
    ok, reason = rm.check_balance_reserve(150.0)
    assert not ok and "reserve" in reason.lower()


def test_concurrent_position_cap_rejects():
    rm = RiskManager()
    # tiny notional each so exposure stays under cap; only the count (10) trips
    positions = [{"szi": 0.01, "entryPx": 100.0} for _ in range(10)]
    state = {"balance": 1000.0, "total_value": 1000.0, "positions": positions}
    ok, reason, _ = rm.validate_trade(
        {"action": "buy", "allocation_usd": 50.0, "current_price": 100.0}, state
    )
    assert not ok and "concurrent" in reason.lower()


def test_total_exposure_cap_rejects():
    rm = RiskManager()
    # existing exposure 480 (one position), cap = 50% of 1000 = 500, new 50 -> 530 > 500
    positions = [{"szi": 4.8, "entryPx": 100.0}]
    state = {"balance": 1000.0, "total_value": 1000.0, "positions": positions}
    ok, reason, _ = rm.validate_trade(
        {"action": "buy", "allocation_usd": 50.0, "current_price": 100.0}, state
    )
    assert not ok and "exposure" in reason.lower()


def test_enforce_stop_loss_directions():
    rm = RiskManager()
    assert rm.enforce_stop_loss(None, 100.0, is_buy=True) == 95.0
    assert rm.enforce_stop_loss(None, 100.0, is_buy=False) == 105.0
    assert rm.enforce_stop_loss(88.0, 100.0, is_buy=True) == 88.0  # explicit kept


def test_check_losing_positions_flags_over_threshold():
    rm = RiskManager()
    # notional 100*1 = 100; pnl -25 -> 25% loss >= 20% threshold
    positions = [{"coin": "BTC", "entryPx": 100.0, "szi": 1.0, "pnl": -25.0}]
    out = rm.check_losing_positions(positions)
    assert len(out) == 1 and out[0]["coin"] == "BTC" and out[0]["is_long"] is True


def test_check_losing_positions_ignores_winners_and_small_losses():
    rm = RiskManager()
    positions = [
        {"coin": "ETH", "entryPx": 100.0, "szi": 1.0, "pnl": 50.0},  # winner
        {"coin": "SOL", "entryPx": 100.0, "szi": 1.0, "pnl": -5.0},  # 5% < 20%
    ]
    assert rm.check_losing_positions(positions) == []
