"""Pydantic request/response contracts for the MCP tools.

Returning these models from `@mcp.tool()` functions makes FastMCP emit an
`outputSchema` and `structuredContent` alongside the response, so the wire
contract is derived from code and cannot drift from the handler.

Two flavours:
- **Strict** models (e.g. `PriceResult`, `CandlesResult`) pin every field.
- **Permissive** models (`model_config = extra="allow"`) wrap responses that
  embed raw Hyperliquid SDK payloads whose shape we do not control (order
  execution responses, vault details). They pin the documented top-level
  fields and carry the rest through verbatim, so no data is lost and the
  schema stays useful without being brittle.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class _Passthrough(BaseModel):
    """Base for responses that carry raw SDK payloads through unmodified."""

    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------- market data


class PriceResult(BaseModel):
    asset: str
    price: float


class Candle(BaseModel):
    t: Optional[int] = None
    open: float
    high: float
    low: float
    close: float
    volume: float


class CandlesResult(BaseModel):
    asset: str
    interval: str
    count: int
    candles: list[Candle]


class MarketContext(BaseModel):
    asset: str
    interval: str
    current_price: float
    open_interest: Optional[float] = None
    funding_rate: Optional[float] = None
    indicators: dict[str, Any]
    recent_candles: list[Candle]


class BookLevel(BaseModel):
    px: float
    sz: float
    n: int


class OrderBook(BaseModel):
    asset: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    depth: int


class RecentTrades(BaseModel):
    asset: str
    trades: list[dict[str, Any]]
    count: int


# --------------------------------------------------------------------- account


class AccountState(BaseModel):
    balance: float
    total_value: float
    positions: list[dict[str, Any]]


class OpenOrders(BaseModel):
    orders: list[dict[str, Any]]


class Fills(BaseModel):
    fills: list[dict[str, Any]]


class OrderStatusResult(BaseModel):
    oid: int
    status: Any


class FundingResult(BaseModel):
    funding: list[dict[str, Any]]
    count: int


class HistoricalFunding(BaseModel):
    asset: str
    rates: list[dict[str, Any]]
    count: int


class VaultResult(_Passthrough):
    """Vault details / performance — raw SDK payload."""


# ------------------------------------------------------------------------ risk


class RiskLimits(BaseModel):
    max_position_pct: float
    max_loss_per_position_pct: float
    max_leverage: int
    max_total_exposure_pct: float
    daily_loss_circuit_breaker_pct: float
    mandatory_sl_pct: float
    max_concurrent_positions: int
    min_balance_reserve_pct: float
    circuit_breaker_active: bool
    initial_balance: Optional[float] = None


class LosingPositions(BaseModel):
    to_close: list[dict[str, Any]]


class ValidateTradeResult(BaseModel):
    allowed: bool
    reason: str
    trade: dict[str, Any]
    current_price: Optional[float] = None
    action_canonical: Optional[str] = None


# ---------------------------------------------------------------------- orders


class OrderResult(_Passthrough):
    """Result of an order/leverage/cancel tool.

    Pins the common envelope fields; the entry/order/simulated_* legs that
    embed raw SDK responses ride along as extra fields.
    """

    status: Optional[str] = None
    mode: Optional[str] = None
    reason: Optional[str] = None


# -------------------------------------------------------------------- settings


class SettingsResult(_Passthrough):
    """get/update/reset settings. Shape varies by tool; common fields pinned."""

    settings: Optional[dict[str, Any]] = None
    status: Optional[str] = None


# ------------------------------------------------------------------------ meta


class TradingMode(_Passthrough):
    mode: str
    network: Optional[str] = None
    signer_address: Optional[str] = None
    account_address: Optional[str] = None
    live_trading: Optional[bool] = None
    settings_path: Optional[str] = None
    error: Optional[str] = None


class ServerTime(_Passthrough):
    local_ms: Optional[int] = None
    rtt_ms: Optional[float] = None
    meta_ok: Optional[bool] = None
    error: Optional[str] = None
