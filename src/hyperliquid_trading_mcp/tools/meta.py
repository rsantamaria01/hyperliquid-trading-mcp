"""Meta tools: mode/identity report and server time."""

from __future__ import annotations

from .. import settings
from ..app import _get_client, _live_trading, _mode_tag, mcp
from ..models import ServerTime, TradingMode


@mcp.tool()
async def trading_mode() -> TradingMode:
    """Report mode (DRY-RUN vs LIVE), network, signer and account addresses."""
    try:
        c = _get_client()
        return TradingMode(
            mode=_mode_tag(),
            network=settings.get("network") or "mainnet",
            signer_address=c.wallet.address,
            account_address=c.account_address,
            live_trading=_live_trading(),
            settings_path=str(settings.SETTINGS_PATH),
        )
    except Exception as e:
        return TradingMode(mode=_mode_tag(), error=str(e))


@mcp.tool()
async def get_server_time() -> ServerTime:
    """Server timestamp + round-trip latency (useful for sanity-checking clock skew)."""
    return ServerTime(**await _get_client().get_server_time())
