"""Hyperliquid Trading Agent — MCP server.

Exposes the Hyperliquid market-data, account, and order-execution surface as
MCP tools so Claude (in Claude Code or Cowork) can drive trading directly.

Safety model:
- LIVE_TRADING env var defaults to "false". In dry-run mode, every tool that
  would place or close an order returns a simulated response and does NOT
  hit the exchange. Market-data and account-read tools work in either mode.
- Risk guards (position size, leverage, exposure, daily drawdown, mandatory
  SL) are enforced in code via RiskManager before any order is submitted,
  regardless of what the LLM decides.

Run:
    python -m mcp_server.server
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

# Load .env from the plugin root before anything reads os.environ.
# Search: env var override -> plugin root -> two dirs up -> cwd.
try:
    from dotenv import load_dotenv
    _explicit = os.getenv("HYPERLIQUID_PLUGIN_ENV")
    _candidates = []
    if _explicit:
        _candidates.append(Path(_explicit))
    here = Path(__file__).resolve()
    _candidates.extend([here.parent / ".env", here.parent.parent / ".env", Path.cwd() / ".env"])
    for _p in _candidates:
        if _p.is_file():
            load_dotenv(_p, override=False)
            break
except ImportError:
    pass

from mcp.server.fastmcp import FastMCP

from .hyperliquid_client import HyperliquidClient
from .indicators import compute_summary
from .risk_manager import RiskManager


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


LIVE_TRADING = _truthy(os.getenv("LIVE_TRADING"))

mcp = FastMCP("hyperliquid-trading-agent")

# Lazily-instantiated singletons — fail fast on missing config when a tool is invoked,
# not at import time (so `list_tools` still works without env vars set).
_client: HyperliquidClient | None = None
_risk: RiskManager | None = None


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


def _mode_tag() -> str:
    return "LIVE" if LIVE_TRADING else "DRY-RUN"


def _normalize_side(s: str | None) -> str | None:
    """Canonicalize buy/sell/long/short (any case) -> 'buy' | 'sell'. Returns None
    if not recognized so callers can return a clear error."""
    if not s:
        return None
    m = {
        "buy": "buy", "long": "buy", "b": "buy",
        "sell": "sell", "short": "sell", "s": "sell",
    }
    return m.get(str(s).strip().lower())


# ------------------------------------------------------------------ market data


@mcp.tool()
async def get_current_price(asset: str) -> dict:
    """Get the latest mid-price for an asset.

    Args:
        asset: Symbol — e.g. "BTC", "ETH", "xyz:GOLD", "xyz:TSLA"
    """
    px = await _get_client().get_current_price(asset)
    return {"asset": asset, "price": px}


@mcp.tool()
async def get_candles(asset: str, interval: str = "5m", count: int = 100) -> dict:
    """Fetch recent OHLCV candles.

    Args:
        asset: Symbol (e.g. "BTC", "xyz:GOLD")
        interval: "1m", "5m", "15m", "1h", "4h", "1d", etc.
        count: Number of candles (max 5000).
    """
    candles = await _get_client().get_candles(asset, interval, count)
    return {"asset": asset, "interval": interval, "count": len(candles), "candles": candles}


@mcp.tool()
async def get_market_context(asset: str, interval: str = "5m", count: int = 200) -> dict:
    """Bundle everything Claude needs to analyze an asset: price, candles tail,
    computed indicators (latest values), open interest, funding rate.

    This is the recommended one-shot tool for market analysis.
    """
    client = _get_client()
    candles = await client.get_candles(asset, interval, count)
    indicators = compute_summary(candles)
    return {
        "asset": asset,
        "interval": interval,
        "current_price": await client.get_current_price(asset),
        "open_interest": await client.get_open_interest(asset),
        "funding_rate": await client.get_funding_rate(asset),
        "indicators": indicators,
        "recent_candles": candles[-20:],
    }


# ------------------------------------------------------------------ account


@mcp.tool()
async def get_account_state() -> dict:
    """Get the agent's account: balance, total value, open positions with PnL."""
    state = await _get_client().get_user_state()
    _get_risk().record_initial_balance(state.get("balance", 0))
    return state


@mcp.tool()
async def get_open_orders() -> dict:
    """List open / resting orders, including TP/SL triggers."""
    return {"orders": await _get_client().get_open_orders()}


@mcp.tool()
async def get_recent_fills(limit: int = 50) -> dict:
    """Recent fills (executed trades) — useful for reviewing recent activity."""
    return {"fills": await _get_client().get_recent_fills(limit)}


# ------------------------------------------------------------------ risk


@mcp.tool()
async def get_risk_limits() -> dict:
    """Return the risk-manager configuration (caps, breakers, current state)."""
    return _get_risk().summary()


@mcp.tool()
async def check_losing_positions() -> dict:
    """Identify positions that exceed the max-loss-per-position threshold and
    should be force-closed. Does NOT close them — use force_close_position()."""
    state = await _get_client().get_user_state()
    return {"to_close": _get_risk().check_losing_positions(state.get("positions", []))}


@mcp.tool()
async def validate_trade(
    asset: str,
    action: str,
    allocation_usd: float,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
) -> dict:
    """Run a proposed trade through all risk checks WITHOUT executing.

    Returns the (possibly adjusted) trade and a reason if rejected. Useful
    for sanity-checking a plan before calling place_market_order().

    Args:
        asset: Symbol
        action: "buy" / "long" / "sell" / "short" / "hold" (case-insensitive)
        allocation_usd: Notional in USD
        sl_price: Optional explicit stop-loss; if omitted, mandatory SL is auto-applied
        tp_price: Optional take-profit
    """
    canonical = "hold" if str(action).strip().lower() == "hold" else _normalize_side(action)
    if canonical is None:
        return {"allowed": False, "reason": f"unrecognized action {action!r}; use buy/sell/long/short/hold"}
    client = _get_client()
    risk = _get_risk()
    state = await client.get_user_state()
    risk.record_initial_balance(state.get("balance", 0))
    current = await client.get_current_price(asset)
    trade = {
        "asset": asset,
        "action": canonical,
        "allocation_usd": allocation_usd,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "current_price": current,
    }
    ok, reason, adjusted = risk.validate_trade(trade, state)
    return {"allowed": ok, "reason": reason, "trade": adjusted, "current_price": current, "action_canonical": canonical}


# ------------------------------------------------------------------ orders


@mcp.tool()
async def place_market_order(
    asset: str,
    side: str,
    allocation_usd: float,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    slippage: float = 0.01,
) -> dict:
    """Open a market position. Always runs through the risk manager first.
    Auto-places stop-loss (and optional take-profit) brackets after the
    entry fills.

    DRY-RUN by default. Set env LIVE_TRADING=true to send real orders.

    Args:
        asset: Symbol
        side: "buy" (long) or "sell" (short)
        allocation_usd: Notional USD to deploy
        sl_price: Optional stop-loss price; auto-set to MANDATORY_SL_PCT if omitted
        tp_price: Optional take-profit price
        slippage: Acceptable slippage as decimal (default 0.01 = 1%)
    """
    canonical = _normalize_side(side)
    if canonical is None:
        return {"status": "error", "reason": f"side must be buy/sell/long/short (got {side!r})"}
    client = _get_client()
    risk = _get_risk()
    state = await client.get_user_state()
    risk.record_initial_balance(state.get("balance", 0))
    current = await client.get_current_price(asset)
    if current <= 0:
        return {"status": "error", "reason": f"could not fetch price for {asset}"}

    trade = {
        "asset": asset,
        "action": canonical,
        "allocation_usd": allocation_usd,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "current_price": current,
    }
    ok, reason, adjusted = risk.validate_trade(trade, state)
    if not ok:
        return {"status": "rejected", "reason": reason, "trade": adjusted, "mode": _mode_tag()}

    size = adjusted["allocation_usd"] / current
    is_buy = canonical == "buy"

    if not LIVE_TRADING:
        return {
            "status": "ok",
            "mode": "DRY-RUN",
            "simulated_entry": {
                "asset": asset, "side": canonical, "size": size, "price": current,
                "allocation_usd": adjusted["allocation_usd"],
                "sl_price": adjusted.get("sl_price"),
                "tp_price": adjusted.get("tp_price"),
                "would_set_leverage": int(risk.max_leverage),
            },
            "note": "Set LIVE_TRADING=true env var to execute real orders.",
        }

    # Enforce MAX_LEVERAGE on the exchange before opening, so the actual position
    # respects the configured cap (default behavior is account-wide setting, usually 20x).
    lev_resp = await client.update_leverage(asset, int(risk.max_leverage), is_cross=True)
    entry_resp = await client.market_open(asset, is_buy, size, slippage)
    result: dict[str, Any] = {
        "status": "ok",
        "mode": "LIVE",
        "leverage_set": int(risk.max_leverage),
        "leverage_response": lev_resp,
        "entry": entry_resp,
    }

    sl_resp = await client.place_stop_loss(asset, is_buy, size, adjusted["sl_price"])
    result["stop_loss"] = sl_resp
    if adjusted.get("tp_price"):
        tp_resp = await client.place_take_profit(asset, is_buy, size, adjusted["tp_price"])
        result["take_profit"] = tp_resp
    return result


@mcp.tool()
async def place_limit_order(
    asset: str,
    side: str,
    allocation_usd: float,
    limit_price: float,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    tif: str = "Gtc",
) -> dict:
    """Place a limit order, optionally with SL and TP brackets attached.

    When sl_price and/or tp_price are provided, all orders are submitted as one
    atomic bracket group using Hyperliquid's "normalTpsl" grouping. The SL/TP
    triggers stay dormant until the limit fills, then activate as reduce-only
    market triggers. So the position is protected the moment it opens — no
    window of unbracketed exposure.

    The mandatory SL guard still applies: if sl_price is None, the risk manager
    auto-fills one at MANDATORY_SL_PCT from the limit price.

    Args:
        asset: Symbol
        side: "buy" / "long" / "sell" / "short"
        allocation_usd: Notional USD
        limit_price: Entry price for the limit
        sl_price: Optional stop-loss; auto-set if omitted
        tp_price: Optional take-profit
        tif: "Gtc" (good-til-cancel), "Ioc" (immediate-or-cancel), "Alo" (post-only)
    """
    canonical = _normalize_side(side)
    if canonical is None:
        return {"status": "error", "reason": f"side must be buy/sell/long/short (got {side!r})"}
    client = _get_client()
    risk = _get_risk()
    state = await client.get_user_state()
    risk.record_initial_balance(state.get("balance", 0))
    trade = {
        "asset": asset, "action": canonical,
        "allocation_usd": allocation_usd, "current_price": limit_price,
        "sl_price": sl_price, "tp_price": tp_price,
    }
    ok, reason, adjusted = risk.validate_trade(trade, state)
    if not ok:
        return {"status": "rejected", "reason": reason, "mode": _mode_tag()}
    size = adjusted["allocation_usd"] / limit_price
    is_buy = canonical == "buy"

    if not LIVE_TRADING:
        return {
            "status": "ok", "mode": "DRY-RUN",
            "simulated_order": {
                "asset": asset, "side": canonical, "size": size,
                "limit_price": limit_price,
                "sl_price": adjusted.get("sl_price"),
                "tp_price": adjusted.get("tp_price"),
                "tif": tif,
                "would_set_leverage": int(risk.max_leverage),
                "bracket_group": "normalTpsl" if (adjusted.get("sl_price") or adjusted.get("tp_price")) else "na",
            },
        }

    lev_resp = await client.update_leverage(asset, int(risk.max_leverage), is_cross=True)
    resp = await client.limit_order_with_brackets(
        asset, is_buy, size, limit_price,
        sl_price=adjusted.get("sl_price"),
        tp_price=adjusted.get("tp_price"),
        tif=tif,
    )
    return {
        "status": "ok", "mode": "LIVE",
        "leverage_set": int(risk.max_leverage),
        "leverage_response": lev_resp,
        "brackets_attached": bool(adjusted.get("sl_price") or adjusted.get("tp_price")),
        "order": resp,
    }


@mcp.tool()
async def close_position(asset: str) -> dict:
    """Market-close an existing position on `asset`."""
    if not LIVE_TRADING:
        return {"status": "ok", "mode": "DRY-RUN", "would_close": asset}
    resp = await _get_client().market_close(asset)
    return {"status": "ok", "mode": "LIVE", "response": resp}


@mcp.tool()
async def force_close_losing_positions() -> dict:
    """Close every position where loss% >= MAX_LOSS_PER_POSITION_PCT.

    The upstream repo runs this at the top of every loop iteration. Call it
    periodically (or via a scheduled task) to enforce the cap.
    """
    client = _get_client()
    risk = _get_risk()
    state = await client.get_user_state()
    targets = risk.check_losing_positions(state.get("positions", []))
    results = []
    for t in targets:
        if not LIVE_TRADING:
            results.append({"coin": t["coin"], "status": "DRY-RUN", "would_close": True, **t})
            continue
        resp = await client.market_close(t["coin"])
        results.append({"coin": t["coin"], "status": "closed", "response": resp, **t})
    return {"mode": _mode_tag(), "closed": results}


@mcp.tool()
async def cancel_order(asset: str, oid: int) -> dict:
    """Cancel a specific order by ID."""
    if not LIVE_TRADING:
        return {"status": "ok", "mode": "DRY-RUN", "would_cancel": {"asset": asset, "oid": oid}}
    resp = await _get_client().cancel_order(asset, oid)
    return {"status": "ok", "mode": "LIVE", "response": resp}


@mcp.tool()
async def cancel_all_orders(asset: str) -> dict:
    """Cancel every open order for `asset`."""
    if not LIVE_TRADING:
        return {"status": "ok", "mode": "DRY-RUN", "would_cancel_all_for": asset}
    return await _get_client().cancel_all_orders(asset)


@mcp.tool()
async def set_leverage(asset: str, leverage: int, is_cross: bool = True) -> dict:
    """Manually set Hyperliquid leverage for an asset. The plugin auto-calls
    this with MAX_LEVERAGE before every entry, so you only need this tool to
    override per-asset (e.g. low-leverage on a volatile small-cap)."""
    if not LIVE_TRADING:
        return {"status": "ok", "mode": "DRY-RUN", "would_set": {"asset": asset, "leverage": leverage, "is_cross": is_cross}}
    return await _get_client().update_leverage(asset, leverage, is_cross)


@mcp.tool()
async def set_stop_loss(asset: str, is_long: bool, size: float, sl_price: float) -> dict:
    """Attach a stop-loss trigger to an existing position."""
    if not LIVE_TRADING:
        return {"status": "ok", "mode": "DRY-RUN", "would_set_sl": {"asset": asset, "size": size, "sl_price": sl_price}}
    resp = await _get_client().place_stop_loss(asset, is_long, size, sl_price)
    return {"status": "ok", "mode": "LIVE", "response": resp}


@mcp.tool()
async def set_take_profit(asset: str, is_long: bool, size: float, tp_price: float) -> dict:
    """Attach a take-profit trigger to an existing position."""
    if not LIVE_TRADING:
        return {"status": "ok", "mode": "DRY-RUN", "would_set_tp": {"asset": asset, "size": size, "tp_price": tp_price}}
    resp = await _get_client().place_take_profit(asset, is_long, size, tp_price)
    return {"status": "ok", "mode": "LIVE", "response": resp}


# ------------------------------------------------------------------ setup


def _resolve_env_path() -> Path:
    """Where the .env file should live. Honors HYPERLIQUID_PLUGIN_ENV if set,
    otherwise defaults to the plugin root (parent of this package)."""
    explicit = os.getenv("HYPERLIQUID_PLUGIN_ENV")
    if explicit:
        return Path(explicit)
    return Path(__file__).resolve().parent.parent / ".env"


@mcp.tool()
async def get_setup_status() -> dict:
    """Report whether the plugin is configured. Returns the resolved .env path,
    whether it exists, and which required keys are missing.

    Call this first when a user says "set me up" — tells you whether to start
    a fresh setup or just edit specific values.
    """
    env_path = _resolve_env_path()
    exists = env_path.is_file()
    required = ["HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_VAULT_ADDRESS"]
    missing = [k for k in required if not os.getenv(k)]
    return {
        "env_path": str(env_path),
        "env_file_exists": exists,
        "configured": len(missing) == 0,
        "missing_required": missing,
        "live_trading": LIVE_TRADING,
        "network": os.getenv("HYPERLIQUID_NETWORK") or "mainnet",
    }


_ALLOWED_KEYS = {
    "HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_VAULT_ADDRESS",
    "HYPERLIQUID_NETWORK", "LIVE_TRADING",
    "MAX_POSITION_PCT", "MAX_LOSS_PER_POSITION_PCT", "MAX_LEVERAGE",
    "MAX_TOTAL_EXPOSURE_PCT", "DAILY_LOSS_CIRCUIT_BREAKER_PCT",
    "MANDATORY_SL_PCT", "MAX_CONCURRENT_POSITIONS",
    "MIN_BALANCE_RESERVE_PCT",
}

_SECRET_KEYS = {"HYPERLIQUID_PRIVATE_KEY", "HYPERLIQUID_VAULT_ADDRESS"}


@mcp.tool()
async def link_env_file(path: str, mode: str = "symlink") -> dict:
    """Connect an existing .env file to the plugin. ONLY accepts a filesystem
    path — never raw text — so secrets in the file never enter the chat log.

    The user creates a .env outside Claude with their private key, then passes
    the path. The plugin either symlinks (default) or copies the file into its
    own location and reloads.

    Args:
        path: Absolute filesystem path to an existing .env file the user owns.
        mode: "symlink" (default — file stays where it is, symlink points to it)
              or "copy" (one-time copy into the plugin folder).

    Returns:
        Result with the source path, destination, and a summary of recognized
        keys that were loaded. The secret values themselves are never echoed.
    """
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        return {"status": "error", "reason": f"path not found or not a file: {src}"}

    # Validate: confirm the file at least *looks* like an env file we recognize.
    recognized: list[str] = []
    secrets_seen: list[str] = []
    ignored: list[str] = []
    try:
        for line in src.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k = s.partition("=")[0].strip()
            if k in _ALLOWED_KEYS:
                recognized.append(k)
                if k in _SECRET_KEYS:
                    secrets_seen.append(k)
            else:
                ignored.append(k)
    except OSError as e:
        return {"status": "error", "reason": f"could not read {src}: {e}"}

    if not recognized:
        return {"status": "error", "reason": "no recognized keys in file", "path": str(src)}

    dest = _resolve_env_path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Replace any prior link or file at dest
    try:
        if dest.is_symlink() or dest.exists():
            dest.unlink()
    except OSError:
        pass

    if mode == "copy":
        import shutil
        shutil.copy2(src, dest)
        action = "copied"
    else:
        try:
            dest.symlink_to(src)
            action = "symlinked"
        except OSError as e:
            return {"status": "error", "reason": f"could not symlink: {e}. Try mode='copy'."}

    # Reload env from the new file location, then rebuild client/risk on next use
    try:
        from dotenv import load_dotenv
        load_dotenv(dest, override=True)
    except ImportError:
        pass

    global LIVE_TRADING, _client, _risk
    LIVE_TRADING = _truthy(os.getenv("LIVE_TRADING"))
    _client = None
    _risk = None

    return {
        "status": "ok",
        "action": action,
        "source": str(src),
        "destination": str(dest),
        "recognized_keys": sorted(set(recognized)),
        "secrets_detected": sorted(set(secrets_seen)),
        "ignored_keys": sorted(set(ignored)),
        "live_trading": LIVE_TRADING,
        "note": "Secret values stay in your file — they were not read into chat. Restart Claude if behavior looks stale.",
    }


@mcp.tool()
async def unlink_env_file() -> dict:
    """Remove the plugin's link to your .env file. Does not delete your source
    file. Use before uninstalling the plugin if you want to detach cleanly."""
    dest = _resolve_env_path()
    if not (dest.exists() or dest.is_symlink()):
        return {"status": "ok", "note": "no env file linked", "destination": str(dest)}
    target = None
    if dest.is_symlink():
        try:
            target = os.readlink(dest)
        except OSError:
            pass
    dest.unlink()
    return {"status": "ok", "destination": str(dest), "was_pointing_at": target}


# ------------------------------------------------------------------ meta


@mcp.tool()
async def trading_mode() -> dict:
    """Report whether the server is in DRY-RUN or LIVE trading mode and which
    wallet it's signing on behalf of."""
    try:
        c = _get_client()
        return {
            "mode": _mode_tag(),
            "network": (os.getenv("HYPERLIQUID_NETWORK") or "mainnet"),
            "signer_address": c.wallet.address,
            "account_address": c.account_address,
            "live_trading_env": os.getenv("LIVE_TRADING", "false"),
        }
    except Exception as e:
        return {"mode": _mode_tag(), "error": str(e)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
