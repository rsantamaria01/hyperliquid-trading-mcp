"""Persistent runtime settings tools."""

from __future__ import annotations

from typing import Any

from .. import settings
from ..app import mcp, reset_client, reset_risk
from ..models import SettingsResult

# Keys whose change must invalidate the cached RiskManager so its transient
# state (circuit breaker, daily high, initial balance) does not outlive the caps.
_RISK_CAP_KEYS = {
    "max_position_pct",
    "max_loss_per_position_pct",
    "max_leverage",
    "max_total_exposure_pct",
    "daily_loss_circuit_breaker_pct",
    "mandatory_sl_pct",
    "max_concurrent_positions",
    "min_balance_reserve_pct",
}


@mcp.tool()
async def get_settings() -> SettingsResult:
    """Return the current persisted runtime settings (risk caps, trading mode, network).

    Settings live per workspace in CLAUDE_PROJECT_DIR/.hl-mcp/settings.json by
    default (override with HYPERLIQUID_SETTINGS_PATH). Use update_settings() to
    change them.
    """
    return SettingsResult(
        settings=settings.load(),
        diff_from_defaults=settings.diff_from_defaults(),
        settings_path=str(settings.SETTINGS_PATH),
    )


@mcp.tool()
async def update_settings(updates: dict[str, Any]) -> SettingsResult:
    """Update one or more settings and persist to disk.

    Editable keys: live_trading (bool), network ("mainnet"|"testnet"),
    max_position_pct, max_loss_per_position_pct, max_leverage,
    max_total_exposure_pct, daily_loss_circuit_breaker_pct, mandatory_sl_pct,
    max_concurrent_positions, min_balance_reserve_pct.

    Example: update_settings({"live_trading": true, "max_leverage": 5})

    Changing `network` rebuilds the client immediately (no restart needed).
    Changing a risk cap rebuilds the risk manager so stale circuit-breaker /
    daily-high state never outlives the configuration that produced it.
    """
    try:
        new = settings.update(updates)
        # Reset cached client if network changed (the SDK base_url is baked in)
        if "network" in updates:
            reset_client()
        # Reset cached risk manager if any risk cap changed
        if _RISK_CAP_KEYS.intersection(updates):
            reset_risk()
        return SettingsResult(status="ok", settings=new, applied=list(updates.keys()))
    except ValueError as e:
        return SettingsResult(status="error", reason=str(e))


@mcp.tool()
async def reset_settings() -> SettingsResult:
    """Wipe all persisted setting overrides. Reverts to defaults."""
    defaults = settings.reset()
    reset_client()
    reset_risk()
    return SettingsResult(status="ok", settings=defaults)
