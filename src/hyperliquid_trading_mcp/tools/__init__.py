"""Importing this package registers every tool on the shared `mcp` instance.

Each submodule decorates its functions with `@mcp.tool()` at import time, so a
single `from . import tools` (or importing the submodules below) is enough to
populate the server's tool surface.
"""

from __future__ import annotations

from . import account, market, meta, orders, risk, settings_tools  # noqa: F401

__all__ = ["account", "market", "meta", "orders", "risk", "settings_tools"]
