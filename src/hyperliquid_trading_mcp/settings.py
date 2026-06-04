"""Persistent runtime settings for the MCP server.

Lives in a JSON file at SETTINGS_PATH. The default is per-workspace —
`CLAUDE_PROJECT_DIR/.hl-mcp/settings.json` (the workspace Claude spawned the
server in) — so each workspace keeps its own live_trading flag and risk caps.
`HYPERLIQUID_SETTINGS_PATH` overrides the location. The path is resolved at
import time, after `server.py` has loaded the workspace `.env`.

Secrets (HYPERLIQUID_PRIVATE_KEY, HYPERLIQUID_VAULT_ADDRESS) are NOT stored here
— they only come from env. Everything else (risk caps, network, LIVE_TRADING)
is here and editable at runtime via MCP tools.

------------------------------------------------------------------------------
READING THE RISK CAPS — three words to keep straight
------------------------------------------------------------------------------
Every percentage cap below is measured against ACCOUNT EQUITY. Know these:

  • EQUITY   — your total account value (the `total_value` field). Base for
               every % cap here.
  • NOTIONAL — position size = coins × price = total market value you control.
               THIS is what drives your profit and loss.
  • MARGIN   — collateral locked to hold a position = notional ÷ leverage.
               Only a small slice of equity.

  Example: an $11 notional position at 10x leverage locks just $11 ÷ 10 =
  $1.10 of margin. The $11 drives your PnL; the $1.10 is only the deposit.

The per-key comments below give worked examples at a $100 and a $1000 account
so the mental math is easy. ⚠️ marks a cap currently set loose — weak
protection on a LIVE account.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

SETTINGS_PATH = Path(
    os.getenv("HYPERLIQUID_SETTINGS_PATH")
    or os.path.join(os.getenv("CLAUDE_PROJECT_DIR", "."), ".hl-mcp", "settings.json")
)


DEFAULTS: dict[str, Any] = {
    # --- Trading mode ---
    # Master switch. False = DRY-RUN (orders simulated, never sent). True = LIVE
    # (real money). Env LIVE_TRADING overrides this. Default False everywhere.
    "live_trading": False,
    # Which Hyperliquid network to trade. "mainnet" (real funds) or "testnet"
    # (free practice money). Changing this rebuilds the client (SDK base URL is
    # baked in at construction).
    "network": "mainnet",
    # --- Risk caps (all % are measured against account EQUITY) ---
    # Biggest SINGLE position, as a % of equity (caps NOTIONAL, not margin).
    # Over-cap requests are clamped DOWN, not rejected; floor is the $11 exchange
    # minimum.  $100 equity -> max $25 position.  $1000 -> max $250.
    "max_position_pct": 25.0,
    # Auto-cut a loser: if an open position's unrealized loss reaches this % of
    # its OWN notional, force_close_losing_positions() closes it.
    #   $25 position -> closed at -$15.   $250 position -> closed at -$150.
    # ⚠️ 60% is a deep bleed before the bot reacts (was 20% = -$5 / -$50).
    "max_loss_per_position_pct": 60.0,
    # Leverage ceiling (the MARGIN-side guard: leverage = notional / equity).
    # update_leverage() sets this before every entry; check_leverage rejects any
    # trade whose notional exceeds this × equity.  $100 -> up to $1000 notional;
    # $1000 -> up to $10,000.  Far above max_position_pct, so that cap is the
    # one that actually binds; leverage mainly sets how little margin is locked.
    "max_leverage": 10,
    # Max COMBINED notional across ALL open positions, as a % of equity. A new
    # trade pushing the sum over this is rejected.  $100 -> $75 total across all
    # positions.  $1000 -> $750 (e.g. three $250 positions = full).
    "max_total_exposure_pct": 75.0,
    # Daily kill-switch. If equity drops this % below the day's high (UTC), all
    # new entries halt until the next UTC day.  $100 high -> halt at $60;
    # $1000 high -> halt at $600.  ⚠️ allows a 40% daily drawdown (was 10%).
    "daily_loss_circuit_breaker_pct": 40.0,
    # Auto stop-loss distance. If a trade omits an SL, one is placed this % from
    # entry (below for longs, above for shorts).  Entry $100 long -> SL $50.
    # ⚠️ at 10x a position LIQUIDATES near a ~10% adverse move, long before
    # price travels 50%, so a 50%-away SL effectively never fires — protection
    # on paper only. Was 5% (SL $95), which triggers before liquidation.
    "mandatory_sl_pct": 50.0,
    # Hard count limit — never more than this many positions open at once. Pure
    # count, no equity math (total-exposure cap usually fills up first).
    "max_concurrent_positions": 10,
    # Untouchable reserve: block new entries once equity falls below this % of
    # the INITIAL recorded balance.  $100 initial -> stop below $20;
    # $1000 -> stop below $200.  Keeps dry powder you can't trade away.
    "min_balance_reserve_pct": 20.0,
    # --- Networking (plumbing, not risk) ---
    # Max simultaneous read requests to the exchange (bursty fan-out guard
    # against self-inflicted rate limits). Applied when the client is built.
    "read_concurrency": 5,
}

# Settings the user is allowed to change via update_settings.
EDITABLE: set[str] = set(DEFAULTS.keys())

# Types coerced on write so callers can pass strings from chat.
TYPES: dict[str, type] = {
    "live_trading": bool,
    "network": str,
    "max_position_pct": float,
    "max_loss_per_position_pct": float,
    "max_leverage": int,
    "max_total_exposure_pct": float,
    "daily_loss_circuit_breaker_pct": float,
    "mandatory_sl_pct": float,
    "max_concurrent_positions": int,
    "min_balance_reserve_pct": float,
    "read_concurrency": int,
}


_lock = threading.Lock()


def _coerce(key: str, value: Any) -> Any:
    target = TYPES.get(key)
    if target is None:
        return value
    if target is bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    try:
        return target(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"setting {key!r} must be {target.__name__}: {e}") from e


def _read_disk() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def _write_disk(data: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(SETTINGS_PATH)


def load() -> dict[str, Any]:
    """Return the full effective settings (defaults overlaid with persisted)."""
    with _lock:
        data = {**DEFAULTS, **_read_disk()}
        return data


def get(key: str) -> Any:
    return load().get(key)


def update(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into persisted settings. Returns the new full settings."""
    rejected = [k for k in updates if k not in EDITABLE]
    if rejected:
        raise ValueError(f"not editable: {rejected}. Editable keys: {sorted(EDITABLE)}")
    coerced = {k: _coerce(k, v) for k, v in updates.items()}
    with _lock:
        current = _read_disk()
        current.update(coerced)
        _write_disk(current)
        return {**DEFAULTS, **current}


def reset() -> dict[str, Any]:
    """Wipe persisted overrides — settings revert to defaults."""
    with _lock:
        if SETTINGS_PATH.exists():
            SETTINGS_PATH.unlink()
        return dict(DEFAULTS)


def diff_from_defaults() -> dict[str, Any]:
    """Return only the settings the user has changed from defaults."""
    current = load()
    return {k: v for k, v in current.items() if DEFAULTS.get(k) != v}
