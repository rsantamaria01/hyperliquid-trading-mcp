"""Risk management — all safety guards enforced in code, not LLM prompts.

Adapted from the upstream repo. Claude (acting through MCP tools) cannot
override these limits: they run inside every trade tool before the order
hits the exchange.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class RiskManager:
    def __init__(self) -> None:
        self.max_position_pct = _f("MAX_POSITION_PCT", 10)
        self.max_loss_per_position_pct = _f("MAX_LOSS_PER_POSITION_PCT", 20)
        self.max_leverage = _f("MAX_LEVERAGE", 10)
        self.max_total_exposure_pct = _f("MAX_TOTAL_EXPOSURE_PCT", 50)
        self.daily_loss_circuit_breaker_pct = _f("DAILY_LOSS_CIRCUIT_BREAKER_PCT", 10)
        self.mandatory_sl_pct = _f("MANDATORY_SL_PCT", 5)
        self.max_concurrent_positions = _i("MAX_CONCURRENT_POSITIONS", 10)
        self.min_balance_reserve_pct = _f("MIN_BALANCE_RESERVE_PCT", 20)

        self.daily_high_value: float | None = None
        self.daily_high_date = None
        self.circuit_breaker_active = False
        self.initial_balance: float | None = None

    # ---- helpers ----
    def _reset_daily_if_needed(self, account_value: float) -> None:
        today = datetime.now(timezone.utc).date()
        if self.daily_high_date != today:
            self.daily_high_value = account_value
            self.daily_high_date = today
            self.circuit_breaker_active = False
        elif self.daily_high_value is None or account_value > self.daily_high_value:
            self.daily_high_value = account_value

    def record_initial_balance(self, balance: float) -> None:
        if self.initial_balance is None and balance > 0:
            self.initial_balance = balance

    # ---- individual checks ----
    def check_position_size(self, alloc_usd, account_value):
        if account_value <= 0:
            return False, "Account value is zero or negative"
        cap = account_value * (self.max_position_pct / 100.0)
        if alloc_usd > cap:
            return False, f"Allocation ${alloc_usd:.2f} > {self.max_position_pct}% cap (${cap:.2f})"
        return True, ""

    def check_total_exposure(self, positions, new_alloc, account_value):
        exposure = 0.0
        for p in positions:
            qty = abs(float(p.get("quantity") or p.get("szi") or 0))
            entry = float(p.get("entry_price") or p.get("entryPx") or 0)
            exposure += qty * entry
        cap = account_value * (self.max_total_exposure_pct / 100.0)
        if exposure + new_alloc > cap:
            return False, f"Total exposure ${exposure + new_alloc:.2f} > {self.max_total_exposure_pct}% cap (${cap:.2f})"
        return True, ""

    def check_leverage(self, alloc_usd, balance):
        if balance <= 0:
            return False, "Balance is zero or negative"
        lev = alloc_usd / balance
        if lev > self.max_leverage:
            return False, f"Effective leverage {lev:.1f}x > max {self.max_leverage}x"
        return True, ""

    def check_daily_drawdown(self, account_value):
        self._reset_daily_if_needed(account_value)
        if self.circuit_breaker_active:
            return False, "Daily loss circuit breaker active — no new trades until tomorrow (UTC)"
        if self.daily_high_value and self.daily_high_value > 0:
            dd = ((self.daily_high_value - account_value) / self.daily_high_value) * 100
            if dd >= self.daily_loss_circuit_breaker_pct:
                self.circuit_breaker_active = True
                return False, f"Daily drawdown {dd:.2f}% exceeds {self.daily_loss_circuit_breaker_pct}%"
        return True, ""

    def check_concurrent_positions(self, count):
        if count >= self.max_concurrent_positions:
            return False, f"Already at max concurrent positions ({self.max_concurrent_positions})"
        return True, ""

    def check_balance_reserve(self, balance):
        if self.initial_balance is None or self.initial_balance <= 0:
            return True, ""
        floor = self.initial_balance * (self.min_balance_reserve_pct / 100.0)
        if balance < floor:
            return False, f"Balance ${balance:.2f} < reserve floor ${floor:.2f}"
        return True, ""

    def enforce_stop_loss(self, sl_price, entry_price, is_buy):
        if sl_price is not None:
            return sl_price
        dist = entry_price * (self.mandatory_sl_pct / 100.0)
        return round(entry_price - dist if is_buy else entry_price + dist, 6)

    def check_losing_positions(self, positions):
        to_close = []
        for p in positions:
            coin = p.get("coin") or p.get("symbol")
            entry = float(p.get("entryPx") or p.get("entry_price") or 0)
            size = float(p.get("szi") or p.get("quantity") or 0)
            pnl = float(p.get("pnl") or p.get("unrealized_pnl") or 0)
            if entry == 0 or size == 0:
                continue
            notional = abs(size) * entry
            if notional == 0:
                continue
            loss_pct = abs(pnl / notional) * 100 if pnl < 0 else 0
            if loss_pct >= self.max_loss_per_position_pct:
                to_close.append({
                    "coin": coin,
                    "size": abs(size),
                    "is_long": size > 0,
                    "loss_pct": round(loss_pct, 2),
                    "pnl": round(pnl, 2),
                })
        return to_close

    def validate_trade(self, trade, account_state):
        # Normalize action — accept buy/sell/long/short in any case
        raw_action = str(trade.get("action", "hold")).strip().lower()
        action_map = {"buy": "buy", "long": "buy", "sell": "sell", "short": "sell", "hold": "hold"}
        action = action_map.get(raw_action, raw_action)
        if action not in ("buy", "sell", "hold"):
            return False, f"Unknown action {trade.get('action')!r}; expected buy/sell/long/short/hold", trade
        trade = {**trade, "action": action}
        if action == "hold":
            return True, "", trade
        alloc_usd = float(trade.get("allocation_usd", 0))
        if alloc_usd <= 0:
            return False, "Zero or negative allocation", trade
        if alloc_usd < 11.0:
            alloc_usd = 11.0
            trade = {**trade, "allocation_usd": alloc_usd}
        account_value = float(account_state.get("total_value", 0))
        balance = float(account_state.get("balance", 0))
        positions = account_state.get("positions", [])
        is_buy = action == "buy"
        self.record_initial_balance(balance)

        for check in (
            self.check_daily_drawdown(account_value),
            self.check_balance_reserve(balance),
        ):
            ok, reason = check
            if not ok:
                return False, reason, trade

        ok, reason = self.check_position_size(alloc_usd, account_value)
        if not ok:
            cap = account_value * (self.max_position_pct / 100.0)
            alloc_usd = max(cap, 11.0)
            trade = {**trade, "allocation_usd": alloc_usd}
            logging.warning("Risk: capped allocation to $%.2f", alloc_usd)

        for check in (
            self.check_total_exposure(positions, alloc_usd, account_value),
            self.check_leverage(alloc_usd, balance),
        ):
            ok, reason = check
            if not ok:
                return False, reason, trade

        active = sum(1 for p in positions if abs(float(p.get("szi") or p.get("quantity") or 0)) > 0)
        ok, reason = self.check_concurrent_positions(active)
        if not ok:
            return False, reason, trade

        current_price = float(trade.get("current_price", 0)) or 1.0
        sl_price = trade.get("sl_price")
        trade = {**trade, "sl_price": self.enforce_stop_loss(sl_price, current_price, is_buy)}
        return True, "", trade

    def summary(self) -> dict:
        return {
            "max_position_pct": self.max_position_pct,
            "max_loss_per_position_pct": self.max_loss_per_position_pct,
            "max_leverage": self.max_leverage,
            "max_total_exposure_pct": self.max_total_exposure_pct,
            "daily_loss_circuit_breaker_pct": self.daily_loss_circuit_breaker_pct,
            "mandatory_sl_pct": self.mandatory_sl_pct,
            "max_concurrent_positions": self.max_concurrent_positions,
            "min_balance_reserve_pct": self.min_balance_reserve_pct,
            "circuit_breaker_active": self.circuit_breaker_active,
            "initial_balance": self.initial_balance,
        }
