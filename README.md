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
- **Setup tools**: `link_env_file(path)` lets clients connect to a `.env` file on disk without the user pasting credentials into chat.
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

## Quick start — Python

```bash
pip install -e .
# or with uv
uv pip install -e .

# Run
hyperliquid-trading-mcp
```

## Configuration

Required env vars (in `.env` or your host env):

- `HYPERLIQUID_PRIVATE_KEY` — **agent wallet** private key (signer only, no funds). Create one at app.hyperliquid.xyz → Settings → API Wallets.
- `HYPERLIQUID_VAULT_ADDRESS` — **main wallet** address (the funded one).

Optional:

- `LIVE_TRADING=true` — enable real orders. Default `false` (dry-run).
- `HYPERLIQUID_NETWORK=testnet` — use testnet. Default `mainnet`.
- Risk caps: `MAX_POSITION_PCT`, `MAX_LEVERAGE`, `MAX_TOTAL_EXPOSURE_PCT`, `DAILY_LOSS_CIRCUIT_BREAKER_PCT`, `MANDATORY_SL_PCT`, `MAX_CONCURRENT_POSITIONS`, `MIN_BALANCE_RESERVE_PCT`, `MAX_LOSS_PER_POSITION_PCT`. See `.env.example` for defaults.

## Connecting from an MCP client

### Claude Code / Cowork plugin

Add to your plugin's `plugin.json`:

```json
{
  "mcpServers": {
    "hyperliquid-trading": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "${HOME}/.config/hyperliquid-mcp/.env",
        "rsantamaria01/hyperliquid-trading-mcp:latest"
      ]
    }
  }
}
```

Or via `uvx` from git (no Docker required):

```json
{
  "mcpServers": {
    "hyperliquid-trading": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/rsantamaria01/hyperliquid-trading-mcp",
        "hyperliquid-trading-mcp"
      ],
      "env": {
        "HYPERLIQUID_PLUGIN_ENV": "${HOME}/.config/hyperliquid-mcp/.env"
      }
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
      "command": "uvx",
      "args": ["--from", "git+https://github.com/rsantamaria01/hyperliquid-trading-mcp", "hyperliquid-trading-mcp"],
      "env": {
        "HYPERLIQUID_PRIVATE_KEY": "0x...",
        "HYPERLIQUID_VAULT_ADDRESS": "0x...",
        "LIVE_TRADING": "false"
      }
    }
  }
}
```

## Tool surface

22 MCP tools across:

- Market data: `get_current_price`, `get_candles`, `get_market_context`
- Account: `get_account_state`, `get_open_orders`, `get_recent_fills`
- Risk: `get_risk_limits`, `check_losing_positions`, `validate_trade`
- Orders: `place_market_order`, `place_limit_order` (with optional brackets), `close_position`, `force_close_losing_positions`, `set_stop_loss`, `set_take_profit`, `set_leverage`, `cancel_order`, `cancel_all_orders`
- Setup: `link_env_file`, `unlink_env_file`, `get_setup_status`
- Meta: `trading_mode`

Each order tool checks `LIVE_TRADING`. In dry-run it returns a simulated response — safe for testing skills/prompts without spending USDC.

## Related projects

- **Plugin layer for Cowork/Claude Code** with skills, strategies, and slash commands → [rsantamaria01/hyperliquid-trading-agent](https://github.com/rsantamaria01/hyperliquid-trading-agent)
- Upstream MCP server → [edkdev/hyperliquid-mcp](https://github.com/edkdev/hyperliquid-mcp)
- Original trading loop → [sanketagarwal/hyperliquid-trading-agent](https://github.com/sanketagarwal/hyperliquid-trading-agent)

## License

MIT.
