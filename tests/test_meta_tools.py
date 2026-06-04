"""Observability + resilience guards for the meta tools.

These cover the failure mode behind the "server-wide outage" misdiagnosis: a
stale pre-fix build crashes in HyperliquidClient.__init__ (SDK spot-meta
IndexError), so every client-touching tool raised a bare `list index out of
range`. A health probe must instead (a) survive client construction failing —
returning a structured error, not propagating — and (b) surface the running
server version so a stale build is visible without guessing.
"""

from __future__ import annotations

from hyperliquid_trading_mcp import app
from hyperliquid_trading_mcp.models import ServerTime, TradingMode
from hyperliquid_trading_mcp.tools import meta


async def test_get_server_time_carries_version(fake_client):
    out = await meta.get_server_time()
    assert isinstance(out, ServerTime)
    assert out.version  # non-empty version string surfaced on the health probe


async def test_get_server_time_survives_client_init_failure(monkeypatch):
    """If _get_client() raises (the stale-build IndexError), the health probe
    must report it as a structured error and still surface the version — not
    propagate a bare exception that reads like an exchange outage."""

    def boom() -> None:
        raise IndexError("list index out of range")

    monkeypatch.setattr(meta, "_get_client", boom)
    out = await meta.get_server_time()
    assert isinstance(out, ServerTime)
    assert out.error and "list index out of range" in out.error
    assert out.version  # version still reported even when the client is dead


async def test_trading_mode_carries_version(fake_client):
    out = await meta.trading_mode()
    assert isinstance(out, TradingMode)
    assert out.version


def test_server_version_is_nonempty():
    assert app._server_version()
