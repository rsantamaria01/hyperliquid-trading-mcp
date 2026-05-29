"""Risk tools: limits inspection, losing-position scan, pre-trade validation."""

from __future__ import annotations

from typing import Optional

from ..app import _get_client, _get_risk, _normalize_side, mcp
from ..models import LosingPositions, RiskLimits, ValidateTradeResult


@mcp.tool()
async def get_risk_limits() -> RiskLimits:
    """Risk-manager configuration + runtime state (caps, breakers, initial balance)."""
    return RiskLimits(**_get_risk().summary())


@mcp.tool()
async def check_losing_positions() -> LosingPositions:
    """Identify positions over the max-loss threshold — does NOT close.
    Use force_close_losing_positions() to act."""
    state = await _get_client().get_user_state()
    return LosingPositions(to_close=_get_risk().check_losing_positions(state.get("positions", [])))


@mcp.tool()
async def validate_trade(
    asset: str,
    action: str,
    allocation_usd: float,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
) -> ValidateTradeResult:
    """Run a proposed trade through the risk manager WITHOUT executing.

    Returns the (possibly adjusted) trade and the rejection reason if any.
    """
    canonical = "hold" if str(action).strip().lower() == "hold" else _normalize_side(action)
    if canonical is None:
        return ValidateTradeResult(
            allowed=False,
            reason=f"unrecognized action {action!r}; use buy/sell/long/short/hold",
            trade={"asset": asset, "action": action, "allocation_usd": allocation_usd},
        )
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
    return ValidateTradeResult(
        allowed=ok,
        reason=reason,
        trade=adjusted,
        current_price=current,
        action_canonical=canonical,
    )
