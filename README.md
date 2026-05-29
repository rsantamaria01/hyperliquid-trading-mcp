# Hyperliquid Trading MCP

A Model Context Protocol server for trading Hyperliquid perpetual futures. Designed to be consumed by AI clients (Claude Code, Cowork, Claude Desktop, Cursor, any MCP-aware host).

> **Forked from** [edkdev/hyperliquid-mcp](https://github.com/edkdev/hyperliquid-mcp). Risk-management layer (position cap, leverage enforcement, mandatory stop-loss, daily drawdown circuit breaker, force-close at max loss) adapted from [sanketagarwal/hyperliquid-trading-agent](https://github.com/sanketagarwal/hyperliquid-trading-agent).

> ⚠️ **Real exchange. Real money.** Not audited. Trade at your own risk. Default mode is dry-run (`LIVE_TRADING=false`).

## What's different from the upstream

This fork keeps the same MCP-server-for-Hyperliquid shape but adds:

- **Hard-coded risk guards**: enforced in Python before every order — position size cap, leverage cap, total exposure cap, daily drawdown circuit breaker, mandatory SL. The LLM cannot override them.
- **Leverage enforcement**: `update_leverage(MAX_LEVERAGE, asset)` is called before every entry so positions actually respect the configured cap (Hyperliquid's account default is usually 20x).
- **Price tick rounding**: SL/TP prices are rounded to Hyperliquid's per-asset tick rule (max 5 sig figs, max `6 − szDecimals` decimal places). No more "Invalid TP/SL price" rejections.
- **Action normalization**: tools accept `buy`/`long`/`sell`/`short` in any case.
- **Bracket limit orders**: entry + reduce-only SL trigger + reduce-only TP trigger submitted atomically via `bulk_orders`.
- **Force-close loop**: `force_close_losing_positions()` for the agent's safety net at every trading-cycle iteration.

## Quick start — Docker (recommended)

```bash
git clone https://github.com/rsantamaria01/hyperliquid-trading-mcp.git
cd hyperliquid-trading-mcp
cp .env.example .env
# edit .env with your wallet keys (see Configuration below)
docker compose build
```

To smoke-test that it boots and registers tools:

```bash
docker compose run --rm mcp
# you should see MCP protocol handshake bytes; Ctrl-C to exit
```

To keep a long-lived container running for fast attach:

```bash
docker compose up -d mcp-daemon
```

## Quick start — Python (uv)

This project is managed with [uv](https://docs.astral.sh/uv/). Dependencies are pinned in `uv.lock`.

```bash
uv sync                          # create .venv from the lockfile
uv run hyperliquid-trading-mcp   # run the server from source

# Dev tooling (tests, lint) comes from the dev dependency group:
uv run pytest
uv run ruff check .
```

## Configuration

Required env vars (in `.env` or your host env):

- `HYPERLIQUID_PRIVATE_KEY` — **agent wallet** private key (signer only, no funds). Create one at app.hyperliquid.xyz → Settings → API Wallets.
- `HYPERLIQUID_VAULT_ADDRESS` — **main wallet** address (the funded one).

Optional env overrides (otherwise everything below is configured via MCP `update_settings` tool and persisted to the volume):

- `LIVE_TRADING=false` — emergency kill-switch that beats `settings.json`. Useful if the file accidentally has `live_trading: true` and you need to disable it before the next deploy.
- `HYPERLIQUID_NETWORK=testnet` — overrides `settings.network`.
- `HYPERLIQUID_SETTINGS_PATH=/data/settings.json` — change the settings file location.

All other config (risk caps, `live_trading`, network) lives in `/data/settings.json` and is editable at runtime via the `update_settings` MCP tool. No restart needed.

## Connecting from an MCP client

The server speaks **HTTP/SSE on `http://localhost:8000/sse`**. Add it to your MCP client config:

### Claude Code / Cowork plugin

In `plugin.json`:

```json
{
  "mcpServers": {
    "hyperliquid-trading": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

### Claude Desktop

Edit `~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hyperliquid-trading": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

The server already has your keys (from its `.env`) and persistent settings (from the Docker volume), so no `env` block is needed on the client side. Plugin = transport pointer only.

### Stdio fallback (if your client doesn't support SSE URLs)

If your MCP client only accepts stdio servers, override the transport at container start:

```yaml
# docker-compose.override.yml
services:
  mcp:
    environment:
      MCP_TRANSPORT: stdio
    ports: []           # no HTTP port in stdio mode
```

Then have the client spawn the server via `docker exec`:

```json
{
  "mcpServers": {
    "hyperliquid-trading": {
      "command": "docker",
      "args": ["exec", "-i", "hyperliquid-trading-mcp", "hyperliquid-trading-mcp"]
    }
  }
}
```

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

- **Plugin layer for Cowork/Claude Code** with skills, strategies, and slash commands → [rsantamaria01/hyperliquid-trading-agent](https://github.com/rsantamaria01/hyperliquid-trading-agent)
- Upstream MCP server → [edkdev/hyperliquid-mcp](https://github.com/edkdev/hyperliquid-mcp)
- Original trading loop → [sanketagarwal/hyperliquid-trading-agent](https://github.com/sanketagarwal/hyperliquid-trading-agent)

## License

MIT.
