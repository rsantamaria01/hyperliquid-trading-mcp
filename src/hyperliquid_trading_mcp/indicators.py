"""Local technical indicator computation from OHLCV candles.

Copied verbatim (with minor formatting) from
https://github.com/sanketagarwal/hyperliquid-trading-agent/blob/master/src/indicators/local_indicators.py
so the MCP server has no external indicator dependency.
"""

from __future__ import annotations

import math


def _closes(candles: list[dict]) -> list[float]:
    return [c["close"] for c in candles]


def _volumes(candles: list[dict]) -> list[float]:
    return [c["volume"] for c in candles]


def sma(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1 : i + 1]) / period)
    return result


def ema(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    k = 2.0 / (period + 1)
    prev = None
    for i, v in enumerate(values):
        if i < period - 1:
            result.append(None)
        elif i == period - 1:
            prev = sum(values[:period]) / period
            result.append(prev)
        else:
            prev = v * k + prev * (1 - k)
            result.append(prev)
    return result


def rsi(candles: list[dict], period: int = 14) -> list[float | None]:
    closes = _closes(candles)
    if len(closes) < period + 1:
        return [None] * len(closes)
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    result: list[float | None] = [None] * period
    gains = [max(d, 0) for d in deltas[:period]]
    losses = [abs(min(d, 0)) for d in deltas[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(round(100.0 - (100.0 / (1.0 + rs)), 4))
    for i in range(period, len(deltas)):
        gain = max(deltas[i], 0)
        loss = abs(min(deltas[i], 0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(round(100.0 - (100.0 / (1.0 + rs)), 4))
    return result


def macd(candles: list[dict], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    closes = _closes(candles)
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line: list[float | None] = []
    for f, s in zip(ema_fast, ema_slow, strict=False):
        if f is not None and s is not None:
            macd_line.append(round(f - s, 6))
        else:
            macd_line.append(None)
    valid_macd = [v for v in macd_line if v is not None]
    signal_line_raw = (
        ema(valid_macd, signal) if len(valid_macd) >= signal else [None] * len(valid_macd)
    )
    signal_line: list[float | None] = [None] * (len(macd_line) - len(valid_macd))
    signal_line.extend(signal_line_raw)
    histogram: list[float | None] = []
    for m, s in zip(macd_line, signal_line, strict=False):
        if m is not None and s is not None:
            histogram.append(round(m - s, 6))
        else:
            histogram.append(None)
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def atr(candles: list[dict], period: int = 14) -> list[float | None]:
    if len(candles) < 2:
        return [None] * len(candles)
    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        lo = candles[i]["low"]
        prev_c = candles[i - 1]["close"]
        tr = max(h - lo, abs(h - prev_c), abs(lo - prev_c))
        true_ranges.append(tr)
    result: list[float | None] = [None] * period
    if len(true_ranges) < period:
        return [None] * len(candles)
    avg = sum(true_ranges[:period]) / period
    result.append(round(avg, 6))
    for i in range(period, len(true_ranges)):
        avg = (avg * (period - 1) + true_ranges[i]) / period
        result.append(round(avg, 6))
    return result


def bbands(candles: list[dict], period: int = 20, std_dev: float = 2.0) -> dict:
    closes = _closes(candles)
    middle = sma(closes, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i in range(len(closes)):
        if middle[i] is None:
            upper.append(None)
            lower.append(None)
        else:
            window = closes[i - period + 1 : i + 1]
            mean = middle[i]
            variance = sum((x - mean) ** 2 for x in window) / period
            sd = math.sqrt(variance)
            upper.append(round(mean + std_dev * sd, 6))
            lower.append(round(mean - std_dev * sd, 6))
    return {"upper": upper, "middle": middle, "lower": lower}


def adx(candles: list[dict], period: int = 14) -> list[float | None]:
    if len(candles) < period + 1:
        return [None] * len(candles)
    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []
    tr_list: list[float] = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        lo = candles[i]["low"]
        prev_h = candles[i - 1]["high"]
        prev_l = candles[i - 1]["low"]
        prev_c = candles[i - 1]["close"]
        plus_dm = max(h - prev_h, 0) if (h - prev_h) > (prev_l - lo) else 0
        minus_dm = max(prev_l - lo, 0) if (prev_l - lo) > (h - prev_h) else 0
        tr = max(h - lo, abs(h - prev_c), abs(lo - prev_c))
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)
    if len(tr_list) < period:
        return [None] * len(candles)
    atr_val = sum(tr_list[:period])
    plus_dm_smooth = sum(plus_dm_list[:period])
    minus_dm_smooth = sum(minus_dm_list[:period])
    dx_list: list[float] = []
    plus_di = (plus_dm_smooth / atr_val) * 100 if atr_val else 0
    minus_di = (minus_dm_smooth / atr_val) * 100 if atr_val else 0
    di_sum = plus_di + minus_di
    dx_list.append(abs(plus_di - minus_di) / di_sum * 100 if di_sum else 0)
    for i in range(period, len(tr_list)):
        atr_val = atr_val - (atr_val / period) + tr_list[i]
        plus_dm_smooth = plus_dm_smooth - (plus_dm_smooth / period) + plus_dm_list[i]
        minus_dm_smooth = minus_dm_smooth - (minus_dm_smooth / period) + minus_dm_list[i]
        plus_di = (plus_dm_smooth / atr_val) * 100 if atr_val else 0
        minus_di = (minus_dm_smooth / atr_val) * 100 if atr_val else 0
        di_sum = plus_di + minus_di
        dx_list.append(abs(plus_di - minus_di) / di_sum * 100 if di_sum else 0)
    result: list[float | None] = [None] * (period * 2)
    if len(dx_list) >= period:
        adx_val = sum(dx_list[:period]) / period
        result.append(round(adx_val, 4))
        for i in range(period, len(dx_list)):
            adx_val = (adx_val * (period - 1) + dx_list[i]) / period
            result.append(round(adx_val, 4))
    while len(result) < len(candles):
        result.insert(0, None)
    return result[: len(candles)]


def obv(candles: list[dict]) -> list[float]:
    closes = _closes(candles)
    volumes = _volumes(candles)
    result = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            result.append(result[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            result.append(result[-1] - volumes[i])
        else:
            result.append(result[-1])
    return result


def vwap(candles: list[dict]) -> list[float | None]:
    cum_vol = 0.0
    cum_tp_vol = 0.0
    result: list[float | None] = []
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        cum_vol += c["volume"]
        cum_tp_vol += tp * c["volume"]
        if cum_vol > 0:
            result.append(round(cum_tp_vol / cum_vol, 6))
        else:
            result.append(None)
    return result


def _latest(series):
    for v in reversed(series):
        if v is not None:
            return v
    return None


def compute_summary(candles: list[dict]) -> dict:
    """Compute indicators and return only the latest values (compact for LLM context)."""
    if not candles or len(candles) < 30:
        return {"error": "insufficient candles"}
    closes = _closes(candles)
    macd_d = macd(candles)
    bb = bbands(candles)
    return {
        "last_close": closes[-1],
        "ema20": _latest(ema(closes, 20)),
        "ema50": _latest(ema(closes, 50)),
        "rsi7": _latest(rsi(candles, 7)),
        "rsi14": _latest(rsi(candles, 14)),
        "macd": _latest(macd_d["macd"]),
        "macd_signal": _latest(macd_d["signal"]),
        "macd_histogram": _latest(macd_d["histogram"]),
        "atr14": _latest(atr(candles, 14)),
        "bb_upper": _latest(bb["upper"]),
        "bb_middle": _latest(bb["middle"]),
        "bb_lower": _latest(bb["lower"]),
        "adx": _latest(adx(candles)),
        "obv": _latest(obv(candles)),
        "vwap": _latest(vwap(candles)),
    }
