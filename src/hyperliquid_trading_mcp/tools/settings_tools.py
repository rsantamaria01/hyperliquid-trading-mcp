"""Persistent runtime settings tools."""

from __future__ import annotations

from .. import settings
from ..app import mcp, reset_client
from ..models import SettingsResult


@mcp.tool()
async def get_settings() -> SettingsResult:
    """Return the current persisted runtime settings (risk caps, trading mode, network).

    Settings live in /data/settings.json by default (Docker named volume) — they
    survive container restarts. Use update_settings() to change them.
    """
    return SettingsResult(
        settings=settings.load(),
        diff_from_defaults=settings.diff_from_defaults(),
        settings_path=str(settings.SETTINGS_PATH),
    )


@mcp.tool()
async def update_settings(updates: dict) -> SettingsResult:
    """Update one or more settings and persist to disk.

    Editable keys: live_trading (bool), network ("mainnet"|"testnet"),
    max_position_pct, max_loss_per_position_pct, max_leverage,
    max_total_exposure_pct, daily_loss_circuit_breaker_pct, mandatory_sl_pct,
    max_concurrent_positions, min_balance_reserve_pct.

    Example: update_settings({"live_trading": true, "max_leverage": 5})

    Note: changing `network` requires a server restart to take effect on
    existing connections; new tool calls after restart pick it up.
    """
    try:
        new = settings.update(updates)
        # Reset cached client if network changed (the SDK base_url is baked in)
        if "network" in updates:
            reset_client()
        return SettingsResult(status="ok", settings=new, applied=list(updates.keys()))
    except ValueError as e:
        return SettingsResult(status="error", reason=str(e))


@mcp.tool()
async def reset_settings() -> SettingsResult:
    """Wipe all persisted setting overrides. Reverts to defaults."""
    defaults = settings.reset()
    reset_client()
    return SettingsResult(status="ok", settings=defaults)
