"""Hyperliquid Trading MCP server — transport entrypoint.

The FastMCP instance and shared helpers live in `app.py`; the tools live under
`tools/`. Importing the `tools` package here registers every `@mcp.tool()` on
the instance before the server starts.

Only env vars are HYPERLIQUID_PRIVATE_KEY + HYPERLIQUID_VAULT_ADDRESS (and
optionally HYPERLIQUID_NETWORK / HYPERLIQUID_SETTINGS_PATH). All runtime
config (risk caps, LIVE_TRADING, network) lives in a persistent JSON file
the MCP exposes via settings tools.

Transport:
- Default: stdio (for direct uvx invocation from a client).
- MCP_TRANSPORT=sse + MCP_HTTP_PORT=8000 exposes over HTTP/SSE (intended for
  Docker deployment where the client connects to http://host:port/sse).
"""

from __future__ import annotations

import os

from . import tools  # noqa: F401 — import registers all @mcp.tool()s on `mcp`
from .app import mcp


def main() -> None:
    transport = (os.getenv("MCP_TRANSPORT") or "stdio").lower()
    if transport == "sse":
        port = int(os.getenv("MCP_HTTP_PORT") or "8000")
        host = os.getenv("MCP_HTTP_HOST") or "0.0.0.0"
        # FastMCP exposes settings on the instance for the SSE server
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
