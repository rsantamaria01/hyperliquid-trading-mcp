"""Meta tools: mode/identity report and server time."""

from __future__ import annotations

from .. import settings
from ..app import _get_client, _live_trading, _mode_tag, _server_version, mcp
from ..models import ServerTime, TradingMode


@mcp.tool()
async def trading_mode() -> TradingMode:
    """Report mode (DRY-RUN vs LIVE), network, signer and account addresses."""
    version = _server_version()
    try:
        c = _get_client()
        return TradingMode(
            mode=_mode_tag(),
            network=settings.get("network") or "mainnet",
            signer_address=c.wallet.address,
            account_address=c.account_address,
            live_trading=_live_trading(),
            settings_path=str(settings.SETTINGS_PATH),
            version=version,
        )
    except Exception as e:
        return TradingMode(mode=_mode_tag(), version=version, error=str(e))


@mcp.tool()
async def get_server_time() -> ServerTime:
    """Server timestamp + round-trip latency + running build version.

    Doubles as the cheapest health probe: it takes no wallet, asset, or network
    argument. Client construction is guarded so a broken build (e.g. a stale
    spawn hitting the SDK spot-meta IndexError) returns a structured `error`
    plus the `version` instead of propagating a bare exception that reads like
    an exchange outage.
    """
    version = _server_version()
    try:
        data = await _get_client().get_server_time()
    except Exception as e:  # noqa: BLE001 — client init can raise (stale-build SDK IndexError)
        return ServerTime(version=version, error=str(e))
    return ServerTime(version=version, **data)
