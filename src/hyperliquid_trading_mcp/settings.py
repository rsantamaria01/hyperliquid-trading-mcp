"""Persistent runtime settings for the MCP server.

Lives in a JSON file at SETTINGS_PATH (default /data/settings.json — backed by
a Docker named volume so changes survive container restarts).

Secrets (HYPERLIQUID_PRIVATE_KEY, HYPERLIQUID_VAULT_ADDRESS) are NOT stored here
— they only come from env. Everything else (risk caps, network, LIVE_TRADING)
is here and editable at runtime via MCP tools.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

SETTINGS_PATH = Path(os.getenv("HYPERLIQUID_SETTINGS_PATH") or "/data/settings.json")


DEFAULTS: dict[str, Any] = {
    # Trading mode
    "live_trading": False,
    "network": "mainnet",  # "mainnet" or "testnet"
    # Risk caps (percentages where applicable)
    "max_position_pct": 10.0,
    "max_loss_per_position_pct": 20.0,
    "max_leverage": 10,
    "max_total_exposure_pct": 50.0,
    "daily_loss_circuit_breaker_pct": 10.0,
    "mandatory_sl_pct": 5.0,
    "max_concurrent_positions": 10,
    "min_balance_reserve_pct": 20.0,
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
