"""Order-execution tools. Every tool reads the live `live_trading` setting and
returns a simulated response in DRY-RUN. Risk validation + leverage enforcement
+ SL/TP bracketing happen here before any SDK call.

LIVE SDK calls are wrapped so an exchange/SDK error surfaces as a structured
OrderResult(status="error") rather than an unhandled exception."""

from __future__ import annotations

from typing import Any

from ..app import _get_client, _get_risk, _live_trading, _mode_tag, mcp, side_or_error
from ..models import OrderResult


def _error(e: Exception) -> OrderResult:
    """Structured error envelope for a failed LIVE SDK call."""
    return OrderResult(status="error", mode="LIVE", reason=str(e))


@mcp.tool()
async def place_market_order(
    asset: str,
    side: str,
    allocation_usd: float,
    sl_price: float | None = None,
    tp_price: float | None = None,
    slippage: float = 0.01,
) -> OrderResult:
    """Open a market position with risk validation + auto leverage enforcement
    + auto SL/TP bracket attachment.

    DRY-RUN by default. Set `live_trading: true` via update_settings to send
    real orders.
    """
    canonical, err = side_or_error(side)
    if err:
        return OrderResult(status="error", reason=err)
    client = _get_client()
    risk = _get_risk()
    state = await client.get_user_state()
    risk.record_initial_balance(state.get("balance", 0))
    current = await client.get_current_price(asset)
    if current <= 0:
        return OrderResult(status="error", reason=f"could not fetch price for {asset}")

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
        return OrderResult(status="rejected", reason=reason, trade=adjusted, mode=_mode_tag())

    size = adjusted["allocation_usd"] / current
    is_buy = canonical == "buy"

    if not _live_trading():
        return OrderResult(
            status="ok",
            mode="DRY-RUN",
            simulated_entry={
                "asset": asset,
                "side": canonical,
                "size": size,
                "price": current,
                "allocation_usd": adjusted["allocation_usd"],
                "sl_price": adjusted.get("sl_price"),
                "tp_price": adjusted.get("tp_price"),
                "would_set_leverage": int(risk.max_leverage),
            },
            note="Set live_trading=true via update_settings to execute real orders.",
        )

    try:
        lev_resp = await client.update_leverage(asset, int(risk.max_leverage), is_cross=True)
        entry_resp = await client.market_open(asset, is_buy, size, slippage)
        result: dict[str, Any] = {
            "leverage_set": int(risk.max_leverage),
            "leverage_response": lev_resp,
            "entry": entry_resp,
        }
        sl = adjusted.get("sl_price")
        if sl is not None:
            result["stop_loss"] = await client.place_stop_loss(asset, is_buy, size, sl)
        if adjusted.get("tp_price"):
            result["take_profit"] = await client.place_take_profit(
                asset, is_buy, size, adjusted["tp_price"]
            )
        return OrderResult(status="ok", mode="LIVE", **result)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def place_limit_order(
    asset: str,
    side: str,
    allocation_usd: float,
    limit_price: float,
    sl_price: float | None = None,
    tp_price: float | None = None,
    tif: str = "Gtc",
) -> OrderResult:
    """Place a limit order with optional atomic SL/TP brackets.

    All orders submitted via single `bulk_orders` call — reduce-only triggers
    sit dormant until the entry fills, then activate.

    tif: "Gtc" (good-til-cancel), "Ioc" (immediate-or-cancel), "Alo" (post-only).
    """
    canonical, err = side_or_error(side)
    if err:
        return OrderResult(status="error", reason=err)
    client = _get_client()
    risk = _get_risk()
    state = await client.get_user_state()
    risk.record_initial_balance(state.get("balance", 0))
    trade = {
        "asset": asset,
        "action": canonical,
        "allocation_usd": allocation_usd,
        "current_price": limit_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
    }
    ok, reason, adjusted = risk.validate_trade(trade, state)
    if not ok:
        return OrderResult(status="rejected", reason=reason, mode=_mode_tag())
    size = adjusted["allocation_usd"] / limit_price
    is_buy = canonical == "buy"

    if not _live_trading():
        return OrderResult(
            status="ok",
            mode="DRY-RUN",
            simulated_order={
                "asset": asset,
                "side": canonical,
                "size": size,
                "limit_price": limit_price,
                "sl_price": adjusted.get("sl_price"),
                "tp_price": adjusted.get("tp_price"),
                "tif": tif,
                "would_set_leverage": int(risk.max_leverage),
                "brackets_attached": bool(adjusted.get("sl_price") or adjusted.get("tp_price")),
            },
        )

    try:
        lev_resp = await client.update_leverage(asset, int(risk.max_leverage), is_cross=True)
        resp = await client.limit_order_with_brackets(
            asset,
            is_buy,
            size,
            limit_price,
            sl_price=adjusted.get("sl_price"),
            tp_price=adjusted.get("tp_price"),
            tif=tif,
        )
        return OrderResult(
            status="ok",
            mode="LIVE",
            leverage_set=int(risk.max_leverage),
            leverage_response=lev_resp,
            brackets_attached=bool(adjusted.get("sl_price") or adjusted.get("tp_price")),
            order=resp,
        )
    except Exception as e:
        return _error(e)


@mcp.tool()
async def modify_order(
    asset: str,
    oid: int,
    side: str,
    size: float,
    limit_price: float,
    tif: str = "Gtc",
    reduce_only: bool = False,
) -> OrderResult:
    """Modify an existing resting order in-place (no cancel + replace)."""
    canonical, err = side_or_error(side)
    if err:
        return OrderResult(status="error", reason=err)
    if not _live_trading():
        return OrderResult(
            status="ok",
            mode="DRY-RUN",
            would_modify={
                "asset": asset,
                "oid": oid,
                "side": canonical,
                "size": size,
                "limit_price": limit_price,
                "tif": tif,
            },
        )
    try:
        resp = await _get_client().modify_order(
            asset,
            oid,
            canonical == "buy",
            size,
            limit_price,
            tif,
            reduce_only,
        )
        return OrderResult(status="ok", mode="LIVE", response=resp)
    except Exception as e:
        return _error(e)


@mcp.tool()
async def close_position(asset: str) -> OrderResult:
    """Market-close an existing position on `asset`."""
    if not _live_trading():
        return OrderResult(status="ok", mode="DRY-RUN", would_close=asset)
    try:
        return OrderResult(
            status="ok", mode="LIVE", response=await _get_client().market_close(asset)
        )
    except Exception as e:
        return _error(e)


@mcp.tool()
async def force_close_losing_positions() -> OrderResult:
    """Close every position where loss% >= max_loss_per_position_pct setting.

    Run at the top of every trading cycle as a safety net. A failure closing one
    position is recorded and the loop continues to the rest.
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
        try:
            resp = await client.market_close(t["coin"])
            results.append({"coin": t["coin"], "status": "closed", "response": resp, **t})
        except Exception as e:
            results.append({"coin": t["coin"], "status": "error", "reason": str(e), **t})
    return OrderResult(status="ok", mode=_mode_tag(), closed=results)


@mcp.tool()
async def cancel_order(asset: str, oid: int) -> OrderResult:
    """Cancel a specific order by ID."""
    if not _live_trading():
        return OrderResult(status="ok", mode="DRY-RUN", would_cancel={"asset": asset, "oid": oid})
    try:
        return OrderResult(
            status="ok", mode="LIVE", response=await _get_client().cancel_order(asset, oid)
        )
    except Exception as e:
        return _error(e)


@mcp.tool()
async def cancel_all_orders(asset: str) -> OrderResult:
    """Cancel every open order for `asset` (entries + triggers)."""
    if not _live_trading():
        return OrderResult(status="ok", mode="DRY-RUN", would_cancel_all_for=asset)
    try:
        return OrderResult(**await _get_client().cancel_all_orders(asset))
    except Exception as e:
        return _error(e)


@mcp.tool()
async def set_stop_loss(asset: str, is_long: bool, size: float, sl_price: float) -> OrderResult:
    """Attach a stop-loss trigger to an existing position."""
    if not _live_trading():
        return OrderResult(
            status="ok",
            mode="DRY-RUN",
            would_set_sl={"asset": asset, "size": size, "sl_price": sl_price},
        )
    try:
        return OrderResult(
            status="ok",
            mode="LIVE",
            response=await _get_client().place_stop_loss(asset, is_long, size, sl_price),
        )
    except Exception as e:
        return _error(e)


@mcp.tool()
async def set_take_profit(asset: str, is_long: bool, size: float, tp_price: float) -> OrderResult:
    """Attach a take-profit trigger to an existing position."""
    if not _live_trading():
        return OrderResult(
            status="ok",
            mode="DRY-RUN",
            would_set_tp={"asset": asset, "size": size, "tp_price": tp_price},
        )
    try:
        return OrderResult(
            status="ok",
            mode="LIVE",
            response=await _get_client().place_take_profit(asset, is_long, size, tp_price),
        )
    except Exception as e:
        return _error(e)


@mcp.tool()
async def set_leverage(asset: str, leverage: int, is_cross: bool = True) -> OrderResult:
    """Manual per-asset leverage override. The plugin auto-calls this with
    max_leverage before every entry, so you only need this to drop leverage
    on a specific volatile asset."""
    if not _live_trading():
        return OrderResult(
            status="ok",
            mode="DRY-RUN",
            would_set={"asset": asset, "leverage": leverage, "is_cross": is_cross},
        )
    try:
        return OrderResult(**await _get_client().update_leverage(asset, leverage, is_cross))
    except Exception as e:
        return _error(e)
