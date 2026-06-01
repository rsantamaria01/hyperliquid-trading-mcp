"""Hyperliquid Trading MCP server — HTTP entrypoint.

The FastMCP instance and shared helpers live in `app.py`; the tools live under
`tools/`. Importing the `tools` package here registers every `@mcp.tool()` on
the instance before the server starts.

Secrets come from env: HYPERLIQUID_PRIVATE_KEY + HYPERLIQUID_VAULT_ADDRESS
(optionally HYPERLIQUID_NETWORK / HYPERLIQUID_SETTINGS_PATH). All runtime config
(risk caps, LIVE_TRADING, network) lives in a persistent JSON file the MCP
exposes via settings tools.

One transport: Streamable HTTP at `/mcp`, served by uvicorn — this runs like an
HTTP API server, only as a container, reachable at `<host-ip>:8000`. Optional
`MCP_AUTH_TOKEN` gates `/mcp` behind a bearer token; `/health` is always open
for container/proxy liveness checks. Host/port default to 0.0.0.0:8000,
overridable via `MCP_HTTP_HOST` / `MCP_HTTP_PORT`.
"""

from __future__ import annotations

import os

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from . import tools  # noqa: F401 — import registers all @mcp.tool()s on `mcp`
from .app import mcp
from .auth import BearerAuthMiddleware


def main() -> None:
    """Serve the MCP server over Streamable HTTP at /mcp."""
    host = os.getenv("MCP_HTTP_HOST") or "0.0.0.0"
    port = int(os.getenv("MCP_HTTP_PORT") or "8000")
    mcp.settings.host = host
    mcp.settings.port = port

    # Reached directly at <host-ip>:8000, so turn off the SDK's localhost-only
    # DNS-rebinding Host guard (it 421s any non-localhost Host). Access control
    # is the bearer token (MCP_AUTH_TOKEN) plus network/proxy controls.
    ts = mcp.settings.transport_security or TransportSecuritySettings()
    ts.enable_dns_rebinding_protection = False
    mcp.settings.transport_security = ts

    app = mcp.streamable_http_app()
    # Unauthenticated liveness endpoint — never needs the auth token.
    app.router.routes.append(
        Route("/health", lambda _req: PlainTextResponse("ok"), methods=["GET"])
    )

    token = (os.getenv("MCP_AUTH_TOKEN") or "").strip()
    if token:
        app = BearerAuthMiddleware(app, token, exempt_paths={"/health"})

    uvicorn.run(app, host=host, port=port, log_level=mcp.settings.log_level.lower())


if __name__ == "__main__":
    main()
