"""Hyperliquid Trading MCP server.

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
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from . import settings
from .hyperliquid_client import HyperliquidClient
from .indicators import compute_summary
from .risk_manager import RiskManager


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
        "buy": "buy", "long": "buy", "b": "buy",
        "sell": "sell", "short": "sell", "s": "sell",
    }
    return m.get(str(s).strip().lower())


mcp = FastMCP("hyperliquid-trading-mcp")

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


# ============================================================ settings


@mcp.tool()
async def get_settings() -> dict:
    """Return the current persisted runtime settings (risk caps, trading mode, network).

    Settings live in /data/settings.json by default (Docker named volume) — they
    survive container restarts. Use update_settings() to change them.
    """
    return {
        "settings": settings.load(),
        "diff_from_defaults": settings.diff_from_defaults(),
        "settings_path": str(settings.SETTINGS_PATH),
    }


@mcp.tool()
async def update_settings(updates: dict) -> dict:
    """Update one or more settings and persist to disk.

    Editable keys: live_trading (bool), network ("mainnet"|"testnet"),
    max_position_pct, max_loss_per_position_pct, max_leverage,
    max_total_exposure_pct, daily_loss_circuit_breaker_pct, mandatory_sl_pct,
    max_concurrent_positions, min_balance_reserve_pct.

    Example: update_settings({"live_trading": true, "max_leverage": 5})

    Note: changing `network` requires a server restart to take effect on
    existing connections; new tool calls after restart pick it up.
    """
    try:
        new = settings.update(updates)
        # Reset cached client if network changed (the SDK base_url is baked in)
        global _client
        if "network" in updates:
            _client = None
        return {"status": "ok", "settings": new, "applied": list(updates.keys())}
    except ValueError as e:
        return {"status": "error", "reason": str(e)}


@mcp.tool()
async def reset_settings() -> dict:
    """Wipe all persisted setting overrides. Reverts to defaults."""
    defaults = settings.reset()
    global _client
    _client = None
    return {"status": "ok", "settings": defaults}


# ============================================================ market data


@mcp.tool()
async def get_current_price(asset: str) -> dict:
    """Latest mid-price for an asset. e.g. "BTC", "ETH", "xyz:GOLD", "xyz:TSLA"."""
    px = await _get_client().get_current_price(asset)
    return {"asset": asset, "price": px}


@mcp.tool()
async def get_candles(asset: str, interval: str = "5m", count: int = 100) -> dict:
    """Fetch recent OHLCV candles.

    interval: "1m", "5m", "15m", "1h", "4h", "1d", etc. count: 1..5000.
    """
    candles = await _get_client().get_candles(asset, interval, count)
    return {"asset": asset, "interval": interval, "count": len(candles), "candles": candles}


@mcp.tool()
async def get_market_context(asset: str, interval: str = "5m", count: int = 200) -> dict:
    """One-shot bundle for analysis: price + indicators (latest values) + OI +
    funding rate + last 20 candles."""
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


@mcp.tool()
async def get_order_book(asset: str, depth: int = 20) -> dict:
    """Order book — top `depth` levels of bids and asks. Useful for spread,
    liquidity, and limit-price placement."""
    return await _get_client().get_order_book(asset, depth)


@mcp.tool()
async def get_recent_trades(asset: str, limit: int = 50) -> dict:
    """Recent public trades on the asset (the tape). Useful for momentum read."""
    trades = await _get_client().get_recent_trades(asset, limit)
    return {"asset": asset, "trades": trades, "count": len(trades)}


# ============================================================ account


@mcp.tool()
async def get_account_state() -> dict:
    """Account snapshot: balance, total value, open positions with PnL."""
    state = await _get_client().get_user_state()
    _get_risk().record_initial_balance(state.get("balance", 0))
    return state


@mcp.tool()
async def get_open_orders() -> dict:
    """List open / resting orders, including TP/SL triggers."""
    return {"orders": await _get_client().get_open_orders()}


@mcp.tool()
async def get_recent_fills(limit: int = 50) -> dict:
    """Recent fills (executed trades)."""
    return {"fills": await _get_client().get_recent_fills(limit)}


@mcp.tool()
async def get_order_status(oid: int) -> dict:
    """Status of a single order by id (filled / resting / cancelled / etc.)."""
    return {"oid": oid, "status": await _get_client().get_order_status(oid)}


# ============================================================ funding


@mcp.tool()
async def get_user_funding(start_time_ms: Optional[int] = None, end_time_ms: Optional[int] = None) -> dict:
    """Your funding payment history. Defaults to last 7 days."""
    fills = await _get_client().get_user_funding(start_time_ms, end_time_ms)
    return {"funding": fills, "count": len(fills)}


@mcp.tool()
async def get_historical_funding(asset: str, start_time_ms: Optional[int] = None, end_time_ms: Optional[int] = None) -> dict:
    """Funding rate history for an asset. Defaults to last 7 days."""
    rates = await _get_client().get_historical_funding(asset, start_time_ms, end_time_ms)
    return {"asset": asset, "rates": rates, "count": len(rates)}


# ============================================================ vaults


@mcp.tool()
async def get_vault_details(vault_address: str) -> dict:
    """Get details on a Hyperliquid vault by address."""
    return await _get_client().get_vault_details(vault_address)


@mcp.tool()
async def get_vault_performance(vault_address: str) -> dict:
    """Performance metrics for a Hyperliquid vault."""
    return await _get_client().get_vault_performance(vault_address)


# ============================================================ risk


@mcp.tool()
async def get_risk_limits() -> dict:
    """Risk-manager configuration + runtime state (caps, breakers, initial balance)."""
    return _get_risk().summary()


@mcp.tool()
async def check_losing_positions() -> dict:
    """Identify positions over the max-loss threshold — does NOT close.
    Use force_close_losing_positions() to act."""
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
    """Run a proposed trade through the risk manager WITHOUT executing.

    Returns the (possibly adjusted) trade and the rejection reason if any.
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
        "asset": asset, "action": canonical, "allocation_usd": allocation_usd,
        "sl_price": sl_price, "tp_price": tp_price, "current_price": current,
    }
    ok, reason, adjusted = risk.validate_trade(trade, state)
    return {"allowed": ok, "reason": reason, "trade": adjusted, "current_price": current, "action_canonical": canonical}


# ============================================================ orders


@mcp.tool()
async def place_market_order(
    asset: str,
    side: str,
    allocation_usd: float,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    slippage: float = 0.01,
) -> dict:
    """Open a market position with risk validation + auto leverage enforcement
    + auto SL/TP bracket attachment.

    DRY-RUN by default. Set `live_trading: true` via update_settings to send
    real orders.
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
        "asset": asset, "action": canonical, "allocation_usd": allocation_usd,
        "sl_price": sl_price, "tp_price": tp_price, "current_price": current,
    }
    ok, reason, adjusted = risk.validate_trade(trade, state)
    if not ok:
        return {"status": "rejected", "reason": reason, "trade": adjusted, "mode": _mode_tag()}

    size = adjusted["allocation_usd"] / current
    is_buy = canonical == "buy"

    if not _live_trading():
        return {
            "status": "ok", "mode": "DRY-RUN",
            "simulated_entry": {
                "asset": asset, "side": canonical, "size": size, "price": current,
                "allocation_usd": adjusted["allocation_usd"],
                "sl_price": adjusted.get("sl_price"),
                "tp_price": adjusted.get("tp_price"),
                "would_set_leverage": int(risk.max_leverage),
            },
            "note": 'Set live_trading=true via update_settings to execute real orders.',
        }

    lev_resp = await client.update_leverage(asset, int(risk.max_leverage), is_cross=True)
    entry_resp = await client.market_open(asset, is_buy, size, slippage)
    result: dict[str, Any] = {
        "status": "ok", "mode": "LIVE",
        "leverage_set": int(risk.max_leverage),
        "leverage_response": lev_resp,
        "entry": entry_resp,
    }
    sl_resp = await client.place_stop_loss(asset, is_buy, size, adjusted["sl_price"])
    result["stop_loss"] = sl_resp
    if adjusted.get("tp_price"):
        result["take_profit"] = await client.place_take_profit(asset, is_buy, size, adjusted["tp_price"])
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
    """Place a limit order with optional atomic SL/TP brackets.

    All orders submitted via single `bulk_orders` call — reduce-only triggers
    sit dormant until the entry fills, then activate. Same atomic-submission
    pattern as edkdev/hyperliquid-mcp.

    tif: "Gtc" (good-til-cancel), "Ioc" (immediate-or-cancel), "Alo" (post-only).
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

    if not _live_trading():
        return {
            "status": "ok", "mode": "DRY-RUN",
            "simulated_order": {
                "asset": asset, "side": canonical, "size": size, "limit_price": limit_price,
                "sl_price": adjusted.get("sl_price"), "tp_price": adjusted.get("tp_price"),
                "tif": tif, "would_set_leverage": int(risk.max_leverage),
                "brackets_attached": bool(adjusted.get("sl_price") or adjusted.get("tp_price")),
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
async def modify_order(
    asset: str,
    oid: int,
    side: str,
    size: float,
    limit_price: float,
    tif: str = "Gtc",
    reduce_only: bool = False,
) -> dict:
    """Modify an existing resting order in-place (no cancel + replace)."""
    canonical = _normalize_side(side)
    if canonical is None:
        return {"status": "error", "reason": f"side must be buy/sell/long/short (got {side!r})"}
    if not _live_trading():
        return {"status": "ok", "mode": "DRY-RUN",
                "would_modify": {"asset": asset, "oid": oid, "side": canonical,
                                 "size": size, "limit_price": limit_price, "tif": tif}}
    resp = await _get_client().modify_order(
        asset, oid, canonical == "buy", size, limit_price, tif, reduce_only,
    )
    return {"status": "ok", "mode": "LIVE", "response": resp}


@mcp.tool()
async def close_position(asset: str) -> dict:
    """Market-close an existing position on `asset`."""
    if not _live_trading():
        return {"status": "ok", "mode": "DRY-RUN", "would_close": asset}
    return {"status": "ok", "mode": "LIVE", "response": await _get_client().market_close(asset)}


@mcp.tool()
async def force_close_losing_positions() -> dict:
    """Close every position where loss% >= max_loss_per_position_pct setting.

    Run at the top of every trading cycle as a safety net.
    """
    client = _get_client()
    risk = _get_risk()
    state = await client.get_user_state()
    targets = risk.check_losing_positions(state.get("positions", []))
    results = []
    live = _live_trading()
    for t in targets:
        if not live:
            results.append({"coin": t["coin"], "status": "DRY-RUN", "would_close": True, **t})
            continue
        resp = await client.market_close(t["coin"])
        results.append({"coin": t["coin"], "status": "closed", "response": resp, **t})
    return {"mode": _mode_tag(), "closed": results}


@mcp.tool()
async def cancel_order(asset: str, oid: int) -> dict:
    """Cancel a specific order by ID."""
    if not _live_trading():
        return {"status": "ok", "mode": "DRY-RUN", "would_cancel": {"asset": asset, "oid": oid}}
    return {"status": "ok", "mode": "LIVE", "response": await _get_client().cancel_order(asset, oid)}


@mcp.tool()
async def cancel_all_orders(asset: str) -> dict:
    """Cancel every open order for `asset` (entries + triggers)."""
    if not _live_trading():
        return {"status": "ok", "mode": "DRY-RUN", "would_cancel_all_for": asset}
    return await _get_client().cancel_all_orders(asset)


@mcp.tool()
async def set_stop_loss(asset: str, is_long: bool, size: float, sl_price: float) -> dict:
    """Attach a stop-loss trigger to an existing position."""
    if not _live_trading():
        return {"status": "ok", "mode": "DRY-RUN", "would_set_sl": {"asset": asset, "size": size, "sl_price": sl_price}}
    return {"status": "ok", "mode": "LIVE",
            "response": await _get_client().place_stop_loss(asset, is_long, size, sl_price)}


@mcp.tool()
async def set_take_profit(asset: str, is_long: bool, size: float, tp_price: float) -> dict:
    """Attach a take-profit trigger to an existing position."""
    if not _live_trading():
        return {"status": "ok", "mode": "DRY-RUN", "would_set_tp": {"asset": asset, "size": size, "tp_price": tp_price}}
    return {"status": "ok", "mode": "LIVE",
            "response": await _get_client().place_take_profit(asset, is_long, size, tp_price)}


@mcp.tool()
async def set_leverage(asset: str, leverage: int, is_cross: bool = True) -> dict:
    """Manual per-asset leverage override. The plugin auto-calls this with
    max_leverage before every entry, so you only need this to drop leverage
    on a specific volatile asset."""
    if not _live_trading():
        return {"status": "ok", "mode": "DRY-RUN",
                "would_set": {"asset": asset, "leverage": leverage, "is_cross": is_cross}}
    return await _get_client().update_leverage(asset, leverage, is_cross)


# ============================================================ meta


@mcp.tool()
async def trading_mode() -> dict:
    """Report mode (DRY-RUN vs LIVE), network, signer and account addresses."""
    try:
        c = _get_client()
        return {
            "mode": _mode_tag(),
            "network": settings.get("network") or "mainnet",
            "signer_address": c.wallet.address,
            "account_address": c.account_address,
            "live_trading": _live_trading(),
            "settings_path": str(settings.SETTINGS_PATH),
        }
    except Exception as e:
        return {"mode": _mode_tag(), "error": str(e)}


@mcp.tool()
async def get_server_time() -> dict:
    """Server timestamp + round-trip latency (useful for sanity-checking clock skew)."""
    return await _get_client().get_server_time()


# ============================================================ entrypoint


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
