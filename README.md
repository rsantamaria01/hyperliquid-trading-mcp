# Hyperliquid Trading MCP

A Model Context Protocol server for trading Hyperliquid perpetual futures. Runs as a **local stdio subprocess** that an MCP client (Claude Code CLI) spawns on demand — no server to host, no network port, no auth.

> **Forked from** [edkdev/hyperliquid-mcp](https://github.com/edkdev/hyperliquid-mcp). Risk-management layer (position cap, leverage enforcement, mandatory stop-loss, daily drawdown circuit breaker, force-close at max loss) adapted from [sanketagarwal/hyperliquid-trading-agent](https://github.com/sanketagarwal/hyperliquid-trading-agent).

> ⚠️ **Real exchange. Real money.** Not audited. Trade at your own risk. Default mode is dry-run (`live_trading: false`).

## What's different from the upstream

This fork keeps the same MCP-server-for-Hyperliquid shape but adds:

- **Hard-coded risk guards**: enforced in Python before every order — position size cap, leverage cap, total exposure cap, daily drawdown circuit breaker, mandatory SL. The LLM cannot override them.
- **Leverage enforcement**: `update_leverage(MAX_LEVERAGE, asset)` is called before every entry so positions actually respect the configured cap (Hyperliquid's account default is usually 20x).
- **Price tick rounding**: SL/TP prices are rounded to Hyperliquid's per-asset tick rule (max 5 sig figs, max `6 − szDecimals` decimal places). No more "Invalid TP/SL price" rejections.
- **Action normalization**: tools accept `buy`/`long`/`sell`/`short` in any case.
- **Bracket limit orders**: entry + reduce-only SL trigger + reduce-only TP trigger submitted atomically via `bulk_orders`.
- **Force-close loop**: `force_close_losing_positions()` for the agent's safety net at every trading-cycle iteration.

## How it runs

The server speaks **local stdio** (`mcp.run()`). The MCP client launches it as a child process — there is no HTTP endpoint, no port, and no token. The boundary is the workspace: the server reads its secrets and settings from the directory the client spawned it in (`CLAUDE_PROJECT_DIR`).

The easiest launcher is [`uvx`](https://docs.astral.sh/uv/), which resolves the package from PyPI and runs the console script in one step.

## Quick start

1. **Install `uv`** (provides `uvx`):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. **Drop a `.env` in your workspace** (the folder you open Claude in) with your wallet keys:
   ```bash
   # .env
   HYPERLIQUID_PRIVATE_KEY=0x...   # agent wallet key (signer only, no funds)
   HYPERLIQUID_VAULT_ADDRESS=0x... # main wallet address (the funded one)
   ```
   Add `.env` and `.hl-mcp/` to the workspace `.gitignore` so secrets and per-workspace settings never get committed.
3. **Run it** (or let the [plugin](https://github.com/rsantamaria01/hyperliquid-trading-agent) auto-spawn it):
   ```bash
   uvx hyperliquid-trading-mcp
   ```
   At startup the server writes a one-line banner to **stderr**, e.g.
   ```
   hyperliquid-trading-mcp [DRY-RUN] — workspace: /home/you/myworkspace
   ```
   `[LIVE]` there means real orders for this workspace. stdout is reserved for the MCP protocol stream.

To develop locally from a checkout:

```bash
uv sync                          # create .venv from the lockfile
uv run hyperliquid-trading-mcp   # runs the stdio server
uv run pytest                    # offline mocked-SDK suite
uv run ruff check .
```

## Configuration

### Secrets — workspace `.env` (loaded from `CLAUDE_PROJECT_DIR`)

- `HYPERLIQUID_PRIVATE_KEY` — **agent wallet** private key (signer only, no funds). Create one at app.hyperliquid.xyz → Settings → API Wallets.
- `HYPERLIQUID_VAULT_ADDRESS` — **main wallet** address (the funded one).

The server loads `CLAUDE_PROJECT_DIR/.env` before anything else, so keys live per workspace. **Never paste keys into chat** — put them in the `.env` file on disk.

### Runtime settings — per-workspace `.hl-mcp/settings.json`

`live_trading`, `network`, and all risk caps live in `CLAUDE_PROJECT_DIR/.hl-mcp/settings.json` and are editable at runtime via the `update_settings` MCP tool — no restart needed. The file is created on first write. Each workspace keeps its own settings, so `live_trading` is scoped to the folder.

A fresh workspace starts in **DRY-RUN** (`live_trading: false`). Reopening a workspace that was previously LIVE surfaces `[LIVE]` in the startup banner, and the plugin's `trade-cycle` skill still requires an explicit GO/NO confirm before the first live order.

### Optional env overrides

- `LIVE_TRADING=false` — emergency kill-switch that beats `settings.json`.
- `HYPERLIQUID_NETWORK=testnet` — overrides `settings.network`.
- `HYPERLIQUID_SETTINGS_PATH=/path/settings.json` — override the settings file location (otherwise the per-workspace default applies).

## Connecting from an MCP client

### Claude Code CLI (supported)

The [plugin](https://github.com/rsantamaria01/hyperliquid-trading-agent) bundles the server config and auto-spawns it via `uvx`. To register it by hand instead:

```bash
claude mcp add --scope user hyperliquid-trading-agent -- uvx hyperliquid-trading-mcp
```

The CLI sets `CLAUDE_PROJECT_DIR` for the spawned process, so the server picks up the workspace `.env` and settings automatically.

### GUI clients (untested)

Desktop/GUI MCP hosts (e.g. Cowork) may not have `uvx`/`npx` on the GUI app's `PATH`, and may not set `CLAUDE_PROJECT_DIR`. If a GUI client can't find `uvx`, point it at an absolute path (`$(which uvx)`) or set `PATH` in the server's `env` block. This path is **untested** — the Claude Code CLI is the supported client.

## Tool surface

**31 MCP tools.** Highlights:

- **Settings (persistent)**: `get_settings`, `update_settings`, `reset_settings`
- **Market data**: `get_current_price`, `get_candles`, `get_market_context`, `get_order_book`, `get_recent_trades`
- **Account**: `get_account_state`, `get_open_orders`, `get_recent_fills`, `get_order_status`
- **Funding**: `get_user_funding`, `get_historical_funding`
- **Vaults**: `get_vault_details`, `get_vault_performance`
- **Risk**: `get_risk_limits`, `check_losing_positions`, `validate_trade`
- **Orders**: `place_market_order`, `place_limit_order` (with brackets), `modify_order`, `close_position`, `force_close_losing_positions`, `set_stop_loss`, `set_take_profit`, `set_leverage`, `cancel_order`, `cancel_all_orders`
- **Meta**: `trading_mode`, `get_server_time`

Each order tool reads the live `live_trading` setting. In dry-run it returns a simulated response — safe for testing without USDC.

## Related projects

- **Plugin layer for Claude Code** with skills, strategies, and slash commands → [rsantamaria01/hyperliquid-trading-agent](https://github.com/rsantamaria01/hyperliquid-trading-agent)
- Upstream MCP server → [edkdev/hyperliquid-mcp](https://github.com/edkdev/hyperliquid-mcp)
- Original trading loop → [sanketagarwal/hyperliquid-trading-agent](https://github.com/sanketagarwal/hyperliquid-trading-agent)

## License

MIT.
