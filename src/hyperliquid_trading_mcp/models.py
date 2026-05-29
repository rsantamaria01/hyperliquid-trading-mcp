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

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Passthrough(BaseModel):
    """Base for responses that carry raw SDK payloads through unmodified."""

    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------- market data


class PriceResult(BaseModel):
    asset: str
    price: float


class Candle(BaseModel):
    t: int | None = None
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
    open_interest: float | None = None
    funding_rate: float | None = None
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
    initial_balance: float | None = None


class LosingPositions(BaseModel):
    to_close: list[dict[str, Any]]


class ValidateTradeResult(BaseModel):
    allowed: bool
    reason: str
    trade: dict[str, Any]
    current_price: float | None = None
    action_canonical: str | None = None


# ---------------------------------------------------------------------- orders


class OrderResult(_Passthrough):
    """Result of an order/leverage/cancel tool.

    Pins the common envelope fields; the entry/order/simulated_* legs that
    embed raw SDK responses ride along as extra fields.
    """

    status: str | None = None
    mode: str | None = None
    reason: str | None = None


# -------------------------------------------------------------------- settings


class SettingsResult(_Passthrough):
    """get/update/reset settings. Shape varies by tool; common fields pinned."""

    settings: dict[str, Any] | None = None
    status: str | None = None


# ------------------------------------------------------------------------ meta


class TradingMode(BaseModel):
    # Strict: every field is built from values we control (see meta.trading_mode).
    mode: str
    network: str | None = None
    signer_address: str | None = None
    account_address: str | None = None
    live_trading: bool | None = None
    settings_path: str | None = None
    error: str | None = None


class ServerTime(BaseModel):
    # Strict: keys mirror HyperliquidClient.get_server_time exactly.
    local_ms: int | None = None
    rtt_ms: float | None = None
    meta_ok: bool | None = None
    error: str | None = None
