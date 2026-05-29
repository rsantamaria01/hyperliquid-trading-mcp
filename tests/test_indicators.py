"""Indicator math tests."""

from __future__ import annotations

from conftest import make_candles

from hyperliquid_trading_mcp import indicators


def test_sma_basic():
    out = indicators.sma([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == 2.0 and out[4] == 4.0


def test_ema_seeds_with_sma():
    out = indicators.ema([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == 2.0  # first EMA value is the SMA seed


def test_compute_summary_insufficient_candles():
    assert indicators.compute_summary([]) == {"error": "insufficient candles"}
    assert indicators.compute_summary(make_candles(10)) == {"error": "insufficient candles"}


def test_compute_summary_full_series_has_all_keys():
    summary = indicators.compute_summary(make_candles(60))
    for key in ("last_close", "ema20", "ema50", "rsi14", "macd", "atr14", "bb_upper", "adx"):
        assert key in summary
    assert summary["last_close"] is not None


def test_obv_responds_to_direction():
    candles = make_candles(5)  # monotonically rising closes -> OBV accumulates volume
    out = indicators.obv(candles)
    assert out[-1] > 0


def test_atr_short_series_is_none_padded():
    out = indicators.atr(make_candles(3), period=14)
    assert all(v is None for v in out)
