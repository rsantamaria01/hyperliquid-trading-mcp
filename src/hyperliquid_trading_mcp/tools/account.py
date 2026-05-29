"""Account, funding, and vault read tools."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..app import _get_client, _get_risk, mcp
from ..models import (
    AccountState,
    Fills,
    FundingResult,
    HistoricalFunding,
    OpenOrders,
    OrderStatusResult,
    VaultResult,
)

# ---------------------------------------------------------------------- account


@mcp.tool()
async def get_account_state() -> AccountState:
    """Account snapshot: balance, total value, open positions with PnL."""
    state = await _get_client().get_user_state()
    _get_risk().record_initial_balance(state.get("balance", 0))
    return AccountState(**state)


@mcp.tool()
async def get_open_orders() -> OpenOrders:
    """List open / resting orders, including TP/SL triggers."""
    return OpenOrders(orders=await _get_client().get_open_orders())


@mcp.tool()
async def get_recent_fills(limit: Annotated[int, Field(ge=1)] = 50) -> Fills:
    """Recent fills (executed trades)."""
    return Fills(fills=await _get_client().get_recent_fills(limit))


@mcp.tool()
async def get_order_status(oid: int) -> OrderStatusResult:
    """Status of a single order by id (filled / resting / cancelled / etc.)."""
    return OrderStatusResult(oid=oid, status=await _get_client().get_order_status(oid))


# ---------------------------------------------------------------------- funding


@mcp.tool()
async def get_user_funding(
    start_time_ms: int | None = None, end_time_ms: int | None = None
) -> FundingResult:
    """Your funding payment history. Defaults to last 7 days."""
    fills = await _get_client().get_user_funding(start_time_ms, end_time_ms)
    return FundingResult(funding=fills, count=len(fills))


@mcp.tool()
async def get_historical_funding(
    asset: str, start_time_ms: int | None = None, end_time_ms: int | None = None
) -> HistoricalFunding:
    """Funding rate history for an asset. Defaults to last 7 days."""
    rates = await _get_client().get_historical_funding(asset, start_time_ms, end_time_ms)
    return HistoricalFunding(asset=asset, rates=rates, count=len(rates))


# ----------------------------------------------------------------------- vaults


@mcp.tool()
async def get_vault_details(vault_address: str) -> VaultResult:
    """Get details on a Hyperliquid vault by address."""
    return VaultResult(**await _get_client().get_vault_details(vault_address))


@mcp.tool()
async def get_vault_performance(vault_address: str) -> VaultResult:
    """Performance metrics for a Hyperliquid vault."""
    return VaultResult(**await _get_client().get_vault_performance(vault_address))
