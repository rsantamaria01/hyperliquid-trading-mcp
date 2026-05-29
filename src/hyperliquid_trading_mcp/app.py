"""Shared application core: the FastMCP instance, runtime helpers, and the
lazily-built client/risk singletons.

Tool modules under `tools/` import from here and register themselves on `mcp`
via the `@mcp.tool()` decorator. `server.py` imports the `tools` package to
trigger that registration and owns the transport entrypoint.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import settings
from .hyperliquid_client import HyperliquidClient
from .risk_manager import RiskManager

mcp = FastMCP("hyperliquid-trading-mcp")

_client: HyperliquidClient | None = None
_risk: RiskManager | None = None


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return (str(v) or "").strip().lower() in {"1", "true", "yes", "on"}


def _live_trading() -> bool:
    """LIVE_TRADING comes from persistent settings; env can override at runtime."""
    env_override = os.getenv("LIVE_TRADING")
    if env_override is not None and env_override != "":
        return _truthy(env_override)
    return bool(settings.get("live_trading"))


def _mode_tag() -> str:
    return "LIVE" if _live_trading() else "DRY-RUN"


def _normalize_side(s: str | None) -> str | None:
    if not s:
        return None
    m = {
        "buy": "buy",
        "long": "buy",
        "b": "buy",
        "sell": "sell",
        "short": "sell",
        "s": "sell",
    }
    return m.get(str(s).strip().lower())


def side_or_error(side: str | None) -> tuple[str | None, str | None]:
    """Normalize a trade side to 'buy'/'sell', or return a rejection message.

    Returns (canonical, None) on success or (None, message) when unrecognized,
    so order tools share one rejection contract instead of restating it.
    """
    canonical = _normalize_side(side)
    if canonical is None:
        return None, f"side must be buy/sell/long/short (got {side!r})"
    return canonical, None


def _get_client() -> HyperliquidClient:
    global _client
    if _client is None:
        _client = HyperliquidClient()
    return _client


def _get_risk() -> RiskManager:
    global _risk
    if _risk is None:
        _risk = RiskManager()
    return _risk


def reset_client() -> None:
    """Drop the cached client so the next call rebuilds it (e.g. after a
    network change — the SDK base_url is baked in at construction)."""
    global _client
    _client = None
