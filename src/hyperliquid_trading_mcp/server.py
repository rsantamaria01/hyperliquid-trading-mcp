"""Hyperliquid Trading MCP server — local stdio entrypoint.

The FastMCP instance and shared helpers live in `app.py`; the tools live under
`tools/`. Importing the `tools` package here registers every `@mcp.tool()` on
the instance before the server starts.

Secrets come from a per-workspace `.env`: HYPERLIQUID_PRIVATE_KEY +
HYPERLIQUID_VAULT_ADDRESS (optionally HYPERLIQUID_NETWORK /
HYPERLIQUID_SETTINGS_PATH). `.env` is loaded from `CLAUDE_PROJECT_DIR` (the
workspace Claude spawns the server in) at the very top of this module — before
`settings.py` and the client read any env — so a workspace can override keys and
the settings-file location. All runtime config (risk caps, live_trading,
network) lives in a per-workspace JSON file (default
`CLAUDE_PROJECT_DIR/.hl-mcp/settings.json`) the MCP exposes via settings tools.

One transport: **local stdio** via `mcp.run()`. Claude spawns the server as a
subprocess (e.g. `uvx hyperliquid-trading-mcp`); there is no network port, no
HTTP, and no auth — the workspace boundary is the access boundary. At startup
the server writes a workspace-path + LIVE/DRY-RUN banner to **stderr** (never
stdout, which is the MCP stdio channel and must stay clean).
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# Load the workspace `.env` BEFORE importing tools/app: settings.py reads
# HYPERLIQUID_SETTINGS_PATH (and its workspace default) at import time, and the
# client reads keys lazily — loading `.env` first makes any workspace override
# effective and keeps key resolution correct.
_PROJECT_DIR = os.getenv("CLAUDE_PROJECT_DIR") or "."
load_dotenv(os.path.join(_PROJECT_DIR, ".env"))

from . import tools  # noqa: E402,F401 — import registers all @mcp.tool()s on `mcp`
from .app import _mode_tag, mcp  # noqa: E402


def main() -> None:
    """Serve the MCP server over local stdio.

    Emits a workspace-path + LIVE/DRY-RUN banner to stderr, then hands the
    process to `mcp.run()` (stdio transport). stdout is reserved for the MCP
    protocol stream and must not be written to here.
    """
    workspace = os.path.abspath(_PROJECT_DIR)
    print(
        f"hyperliquid-trading-mcp [{_mode_tag()}] — workspace: {workspace}",
        file=sys.stderr,
        flush=True,
    )
    mcp.run()


if __name__ == "__main__":
    main()
