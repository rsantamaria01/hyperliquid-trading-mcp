"""Thin async wrapper around the hyperliquid-python-sdk.

Adapted from the upstream repo's `src/trading/hyperliquid_api.py`. Drops the
aggressive retry/reset machinery in favor of simpler retries — the MCP
calls are typically driven by a human in chat, not a tight 24/7 loop.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from . import settings


class HyperliquidClient:
    def __init__(self) -> None:
        priv = os.getenv("HYPERLIQUID_PRIVATE_KEY")
        if not priv:
            raise RuntimeError(
                "HYPERLIQUID_PRIVATE_KEY env var is required (agent wallet signer key)"
            )
        self.wallet = Account.from_key(priv)

        # Network now lives in settings (persistent), env can still override for ops.
        network = (os.getenv("HYPERLIQUID_NETWORK") or settings.get("network") or "mainnet").lower()
        self.network = network  # recorded so app._get_client() can detect a stale network
        self.base_url = os.getenv("HYPERLIQUID_BASE_URL") or (
            getattr(constants, "TESTNET_API_URL", constants.MAINNET_API_URL)
            if network == "testnet"
            else constants.MAINNET_API_URL
        )

        self.account_address = os.getenv("HYPERLIQUID_VAULT_ADDRESS")
        self.query_address = self.account_address or self.wallet.address

        # SDK 0.20.1's Info/Exchange eagerly build spot asset maps in __init__
        # (`spot_meta["tokens"][base]`), which raises IndexError against current
        # Hyperliquid mainnet spot meta — taking the whole client down even
        # though we only trade perps. Passing an empty spot_meta short-circuits
        # that loop; perp meta is fetched separately and is unaffected. spot
        # balances (info.spot_user_state) are a runtime REST call and still work.
        # skip_ws: we issue REST calls only, never subscribe — avoids a dangling
        # websocket thread in the stdio subprocess.
        empty_spot_meta = {"universe": [], "tokens": []}
        self.info = Info(self.base_url, skip_ws=True, spot_meta=empty_spot_meta)
        self.exchange = Exchange(
            self.wallet,
            self.base_url,
            account_address=self.account_address,
            spot_meta=empty_spot_meta,
        )

        self._meta_cache: list | None = None
        self._hip3_meta_cache: dict[str, list] = {}

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ------ metadata / sizing ------
    async def _meta_for(self, asset: str):
        if ":" in asset:
            dex = asset.split(":")[0]
            if dex not in self._hip3_meta_cache:
                data = await self._run(
                    self.info.post, "/info", {"type": "metaAndAssetCtxs", "dex": dex}
                )
                self._hip3_meta_cache[dex] = data
            return self._hip3_meta_cache.get(dex)
        if not self._meta_cache:
            self._meta_cache = await self._run(self.info.meta_and_asset_ctxs)
        return self._meta_cache

    async def round_size(self, asset: str, amount: float) -> float:
        data = await self._meta_for(asset)
        if isinstance(data, list) and data:
            universe = data[0].get("universe", [])
            info = next((u for u in universe if u.get("name") == asset), None)
            if info:
                return round(amount, info.get("szDecimals", 8))
        return round(amount, 8)

    async def round_price(self, asset: str, price: float) -> float:
        """Round a price to Hyperliquid's tick rules.

        Perps: max 5 significant figures, AND (6 - szDecimals) decimal places.
        See https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size

        Without this, trigger orders (SL/TP) often get rejected with
        "Invalid TP/SL price. asset=N".
        """
        if price <= 0:
            return price
        data = await self._meta_for(asset)
        sz_decimals = 8
        if isinstance(data, list) and data:
            universe = data[0].get("universe", [])
            info = next((u for u in universe if u.get("name") == asset), None)
            if info:
                sz_decimals = info.get("szDecimals", 8)
        # Cap decimal places to (6 - szDecimals), floor at 0
        max_decimals = max(0, 6 - sz_decimals)
        rounded = round(price, max_decimals)
        # Cap to 5 significant figures
        from math import floor, log10

        if rounded > 0:
            digits_before_decimal = max(1, int(floor(log10(abs(rounded)))) + 1)
            sig_decimals = max(0, 5 - digits_before_decimal)
            rounded = round(rounded, min(max_decimals, sig_decimals))
        return rounded

    async def update_leverage(self, asset: str, leverage: int, is_cross: bool = True) -> dict:
        """Set the per-asset leverage on Hyperliquid before opening a position.

        Without this, Hyperliquid uses the account's last-set leverage for the
        asset (often 20x default on majors), regardless of the plugin's
        MAX_LEVERAGE config. Call before every entry so MAX_LEVERAGE is real.
        """
        try:
            resp = await self._run(self.exchange.update_leverage, int(leverage), asset, is_cross)
            return {"status": "ok", "response": resp}
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    # ------ price / market data ------
    async def get_current_price(self, asset: str) -> float:
        if ":" in asset:
            dex = asset.split(":")[0]
            mids = await self._run(self.info.post, "/info", {"type": "allMids", "dex": dex})
        else:
            mids = await self._run(self.info.all_mids)
        return float(mids.get(asset, 0.0))

    async def get_candles(self, asset: str, interval: str = "5m", count: int = 100) -> list[dict]:
        interval_ms = {
            "1m": 60_000,
            "3m": 180_000,
            "5m": 300_000,
            "15m": 900_000,
            "30m": 1_800_000,
            "1h": 3_600_000,
            "2h": 7_200_000,
            "4h": 14_400_000,
            "8h": 28_800_000,
            "12h": 43_200_000,
            "1d": 86_400_000,
            "3d": 259_200_000,
            "1w": 604_800_000,
        }.get(interval, 300_000)
        end = int(time.time() * 1000)
        start = end - count * interval_ms
        if ":" in asset:
            raw = await self._run(
                self.info.post,
                "/info",
                {
                    "type": "candleSnapshot",
                    "req": {
                        "coin": asset,
                        "interval": interval,
                        "startTime": start,
                        "endTime": end,
                    },
                },
            )
        else:
            raw = await self._run(self.info.candles_snapshot, asset, interval, start, end)
        return [
            {
                "t": c.get("t"),
                "open": float(c.get("o", 0)),
                "high": float(c.get("h", 0)),
                "low": float(c.get("l", 0)),
                "close": float(c.get("c", 0)),
                "volume": float(c.get("v", 0)),
            }
            for c in raw
        ]

    async def get_open_interest(self, asset: str) -> float | None:
        data = await self._meta_for(asset)
        if isinstance(data, list) and len(data) >= 2:
            meta, ctxs = data[0], data[1]
            universe = meta.get("universe", [])
            idx = next((i for i, u in enumerate(universe) if u.get("name") == asset), None)
            if idx is not None and idx < len(ctxs):
                oi = ctxs[idx].get("openInterest")
                return round(float(oi), 2) if oi else None
        return None

    async def get_funding_rate(self, asset: str) -> float | None:
        data = await self._meta_for(asset)
        if isinstance(data, list) and len(data) >= 2:
            meta, ctxs = data[0], data[1]
            universe = meta.get("universe", [])
            idx = next((i for i, u in enumerate(universe) if u.get("name") == asset), None)
            if idx is not None and idx < len(ctxs):
                f = ctxs[idx].get("funding")
                return round(float(f), 8) if f else None
        return None

    # ------ account ------
    async def get_user_state(self) -> dict:
        state = await self._run(self.info.user_state, self.query_address)
        positions = state.get("assetPositions", [])
        total_value = float(state.get("accountValue", 0.0))
        enriched = []
        for w in positions:
            pos = w["position"]
            entry = float(pos.get("entryPx", 0) or 0)
            size = float(pos.get("szi", 0) or 0)
            side = "long" if size > 0 else "short"
            current = await self.get_current_price(pos["coin"]) if entry and size else 0.0
            pnl = (current - entry) * abs(size) if side == "long" else (entry - current) * abs(size)
            pos["pnl"] = round(pnl, 4)
            pos["current_price"] = current
            pos["notional_entry"] = abs(size) * entry
            pos["side"] = side
            enriched.append(pos)
        balance = float(state.get("withdrawable", 0.0))
        if balance == 0 and total_value == 0:
            try:
                spot = await self._run(self.info.spot_user_state, self.query_address)
                for b in spot.get("balances", []):
                    if b.get("coin") == "USDC":
                        balance = float(b.get("total", 0)) - float(b.get("hold", 0))
                        total_value = balance + sum(p.get("pnl", 0.0) for p in enriched)
                        break
            except Exception:
                pass
        if not total_value:
            total_value = balance + sum(max(p.get("pnl", 0.0), 0.0) for p in enriched)
        return {
            "balance": round(balance, 4),
            "total_value": round(total_value, 4),
            "positions": enriched,
        }

    async def get_open_orders(self) -> list[dict]:
        try:
            orders = await self._run(self.info.frontend_open_orders, self.query_address)
            for o in orders:
                ot = o.get("orderType")
                if isinstance(ot, dict) and "trigger" in ot:
                    trig = ot.get("trigger") or {}
                    if "triggerPx" in trig:
                        o["triggerPx"] = float(trig["triggerPx"])
            return orders
        except Exception:
            return []

    async def get_recent_fills(self, limit: int = 50) -> list[dict]:
        fn = getattr(self.info, "user_fills", None) or getattr(self.info, "fills", None)
        if not fn:
            return []
        try:
            fills = await self._run(fn, self.query_address)
            return fills[-limit:] if isinstance(fills, list) else []
        except Exception:
            return []

    # ------ order execution ------
    async def market_open(
        self, asset: str, is_buy: bool, size: float, slippage: float = 0.01
    ) -> Any:
        size = await self.round_size(asset, size)
        return await self._run(self.exchange.market_open, asset, is_buy, size, None, slippage)

    async def market_close(self, asset: str) -> Any:
        return await self._run(self.exchange.market_close, asset)

    async def limit_order_with_brackets(
        self,
        asset: str,
        is_buy: bool,
        size: float,
        limit_price: float,
        sl_price: float | None = None,
        tp_price: float | None = None,
        tif: str = "Gtc",
    ) -> Any:
        """Submit a limit entry plus optional SL/TP brackets as one atomic batch.

        Sends all orders in a single `bulk_orders()` call (SDK default
        `grouping="na"`). The SL and TP are reduce-only triggers — they sit
        on the exchange and can't fire until the entry fills. Same pattern
        edkdev/hyperliquid-mcp uses successfully.

        Returns the raw bulk_orders response with statuses for each leg.
        """
        size = await self.round_size(asset, size)
        limit_price = await self.round_price(asset, limit_price)

        orders: list[dict] = [
            {
                "coin": asset,
                "is_buy": is_buy,
                "sz": size,
                "limit_px": limit_price,
                "order_type": {"limit": {"tif": tif}},
                "reduce_only": False,
            }
        ]
        if tp_price is not None:
            tp_price = await self.round_price(asset, tp_price)
            orders.append(
                {
                    "coin": asset,
                    "is_buy": not is_buy,
                    "sz": size,
                    "limit_px": tp_price,
                    "order_type": {
                        "trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}
                    },
                    "reduce_only": True,
                }
            )
        if sl_price is not None:
            sl_price = await self.round_price(asset, sl_price)
            orders.append(
                {
                    "coin": asset,
                    "is_buy": not is_buy,
                    "sz": size,
                    "limit_px": sl_price,
                    "order_type": {
                        "trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}
                    },
                    "reduce_only": True,
                }
            )

        return await self._run(lambda: self.exchange.bulk_orders(orders))

    async def place_stop_loss(self, asset: str, is_buy: bool, size: float, sl_price: float) -> Any:
        size = await self.round_size(asset, size)
        sl_price = await self.round_price(asset, sl_price)
        ot = {"trigger": {"triggerPx": sl_price, "isMarket": True, "tpsl": "sl"}}
        return await self._run(self.exchange.order, asset, not is_buy, size, sl_price, ot, True)

    async def place_take_profit(
        self, asset: str, is_buy: bool, size: float, tp_price: float
    ) -> Any:
        size = await self.round_size(asset, size)
        tp_price = await self.round_price(asset, tp_price)
        ot = {"trigger": {"triggerPx": tp_price, "isMarket": True, "tpsl": "tp"}}
        return await self._run(self.exchange.order, asset, not is_buy, size, tp_price, ot, True)

    async def cancel_order(self, asset: str, oid: int) -> Any:
        return await self._run(self.exchange.cancel, asset, oid)

    async def cancel_all_orders(self, asset: str) -> dict:
        try:
            orders = await self._run(self.info.frontend_open_orders, self.query_address)
            count = 0
            for o in orders:
                if o.get("coin") == asset and o.get("oid"):
                    await self.cancel_order(asset, o["oid"])
                    count += 1
            return {"status": "ok", "cancelled": count}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ---------- order management (modify, status, update_leverage) ----------
    async def modify_order(
        self,
        asset: str,
        oid: int,
        is_buy: bool,
        size: float,
        limit_price: float,
        tif: str = "Gtc",
        reduce_only: bool = False,
    ) -> Any:
        """Modify an existing resting order without cancel+replace."""
        size = await self.round_size(asset, size)
        limit_price = await self.round_price(asset, limit_price)
        modify_req = {
            "oid": oid,
            "order": {
                "coin": asset,
                "is_buy": is_buy,
                "sz": size,
                "limit_px": limit_price,
                "order_type": {"limit": {"tif": tif}},
                "reduce_only": reduce_only,
            },
        }
        return await self._run(self.exchange.bulk_modify_orders_new, [modify_req])

    async def get_order_status(self, oid: int) -> Any:
        """Return status of a single order by id (filled, resting, cancelled, etc.)."""
        return await self._run(self.info.query_order_by_oid, self.query_address, oid)

    # ---------- market depth & trades ----------
    async def get_order_book(self, asset: str, depth: int = 20) -> dict:
        """Order book — top `depth` levels of bids and asks."""
        l2 = await self._run(self.info.l2_snapshot, asset)
        levels = l2.get("levels") if isinstance(l2, dict) else None
        bids, asks = [], []
        if isinstance(levels, list) and len(levels) >= 2:
            bids = [
                {"px": float(x["px"]), "sz": float(x["sz"]), "n": int(x.get("n", 0))}
                for x in levels[0][:depth]
            ]
            asks = [
                {"px": float(x["px"]), "sz": float(x["sz"]), "n": int(x.get("n", 0))}
                for x in levels[1][:depth]
            ]
        return {"asset": asset, "bids": bids, "asks": asks, "depth": depth}

    async def get_recent_trades(self, asset: str, limit: int = 50) -> list[dict]:
        """Recent trades on the asset (public tape)."""
        try:
            trades = await self._run(
                self.info.post, "/info", {"type": "recentTrades", "coin": asset}
            )
            if isinstance(trades, list):
                return trades[-limit:]
            return []
        except Exception:
            return []

    # ---------- funding ----------
    async def get_user_funding(
        self, start_time_ms: int | None = None, end_time_ms: int | None = None
    ) -> list[dict]:
        """User's funding payment history. Defaults to last 7 days."""
        end_time_ms = end_time_ms or int(time.time() * 1000)
        start_time_ms = start_time_ms or (end_time_ms - 7 * 86_400_000)
        try:
            return (
                await self._run(
                    self.info.post,
                    "/info",
                    {
                        "type": "userFunding",
                        "user": self.query_address,
                        "startTime": start_time_ms,
                        "endTime": end_time_ms,
                    },
                )
                or []
            )
        except Exception:
            return []

    async def get_historical_funding(
        self, asset: str, start_time_ms: int | None = None, end_time_ms: int | None = None
    ) -> list[dict]:
        """Funding rate history for `asset`. Defaults to last 7 days."""
        end_time_ms = end_time_ms or int(time.time() * 1000)
        start_time_ms = start_time_ms or (end_time_ms - 7 * 86_400_000)
        try:
            return (
                await self._run(
                    self.info.post,
                    "/info",
                    {
                        "type": "fundingHistory",
                        "coin": asset,
                        "startTime": start_time_ms,
                        "endTime": end_time_ms,
                    },
                )
                or []
            )
        except Exception:
            return []

    # ---------- vaults ----------
    async def get_vault_details(self, vault_address: str) -> dict:
        try:
            return (
                await self._run(
                    self.info.post,
                    "/info",
                    {"type": "vaultDetails", "vaultAddress": vault_address},
                )
                or {}
            )
        except Exception as e:
            return {"error": str(e)}

    async def get_vault_performance(self, vault_address: str) -> dict:
        try:
            return (
                await self._run(
                    self.info.post,
                    "/info",
                    {"type": "vaultPerformance", "vaultAddress": vault_address},
                )
                or {}
            )
        except Exception as e:
            return {"error": str(e)}

    # ---------- meta ----------
    async def get_server_time(self) -> dict:
        """Round-trip time + server timestamp (for clock-skew sanity checks)."""
        t0 = time.time() * 1000
        try:
            resp = await self._run(self.info.post, "/info", {"type": "meta"})
            t1 = time.time() * 1000
            return {"local_ms": int(t1), "rtt_ms": round(t1 - t0, 1), "meta_ok": bool(resp)}
        except Exception as e:
            return {"local_ms": int(time.time() * 1000), "error": str(e)}
