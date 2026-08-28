"""Technical indicators implemented from their canonical definitions.

Design rules
------------
* **SMA-seeded EMA** (TradingView / Wilder convention), *not* the first-value
  seed used by ``pandas.Series.ewm(adjust=False)``.  The two converge quickly
  but differ during the warm-up window, so the convention is pinned here and
  covered by tests.
* **Wilder smoothing** (``alpha = 1/period``) is used for RSI, ATR, ADX/DMI,
  MFI and RMA-based signals - the definition used by every Iranian broker
  terminal and by TradingView.
* The warm-up period of each indicator is ``NAN``, never ``0``: a ``0`` RSI and
  a "not computable yet" RSI mean very different things downstream.
* Everything is pure Python on ``list[float]`` so the skill runs in any sandbox
  without numpy/pandas/TA-Lib.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .core import (
    NAN,
    UNSET,
    Candle,
    col,
    first_valid_index,
    is_nan,
    last_valid,
    linreg,
    mean,
    rolling,
    rolling_max,
    rolling_min,
    rolling_std,
    rolling_sum,
)

# ---------------------------------------------------------------------------
# moving averages
# ---------------------------------------------------------------------------


def sma(values: Sequence[float], period: int) -> list[float]:
    """Simple moving average."""
    if period <= 0:
        return [NAN] * len(values)
    out = [NAN] * len(values)
    running = 0.0
    valid = 0
    for i, v in enumerate(values):
        if not is_nan(v):
            running += v
            valid += 1
        j = i - period
        if j >= 0 and not is_nan(values[j]):
            running -= values[j]
            valid -= 1
        if i >= period - 1 and valid == period:
            out[i] = running / period
    return out


def ema(values: Sequence[float], period: int) -> list[float]:
    """Exponential moving average, seeded with the SMA of the first ``period`` values.

    NaN-prefixed input (e.g. a MACD line) is handled by seeding on the first
    ``period`` valid values instead of on index 0.
    """
    n = len(values)
    out = [NAN] * n
    if period <= 0:
        return out
    start = first_valid_index(values)
    if start < 0:
        return out
    if start + period > n:
        return out
    seed = sum(values[start : start + period]) / period
    out[start + period - 1] = seed
    k = 2.0 / (period + 1.0)
    prev = seed
    for i in range(start + period, n):
        v = values[i]
        if is_nan(v):
            out[i] = prev
            continue
        prev = v * k + prev * (1.0 - k)
        out[i] = prev
    return out


def rma(values: Sequence[float], period: int) -> list[float]:
    """Wilder's smoothing / running moving average (``alpha = 1/period``)."""
    n = len(values)
    out = [NAN] * n
    if period <= 0:
        return out
    start = first_valid_index(values)
    if start < 0 or start + period > n:
        return out
    seed = sum(values[start : start + period]) / period
    out[start + period - 1] = seed
    alpha = 1.0 / period
    prev = seed
    for i in range(start + period, n):
        v = values[i]
        if is_nan(v):
            out[i] = prev
            continue
        prev = v * alpha + prev * (1.0 - alpha)
        out[i] = prev
    return out


def wma(values: Sequence[float], period: int) -> list[float]:
    """Weighted (linearly decaying) moving average."""
    denom = period * (period + 1) / 2.0 if period else 0.0
    if period <= 0:
        return [NAN] * len(values)
    return rolling(
        values,
        period,
        lambda w: sum(v * (i + 1) for i, v in enumerate(w)) / denom,
    )


def dema(values: Sequence[float], period: int) -> list[float]:
    e1 = ema(values, period)
    e2 = ema(e1, period)
    return [NAN if is_nan(a) or is_nan(b) else 2.0 * a - b for a, b in zip(e1, e2)]


def tema(values: Sequence[float], period: int) -> list[float]:
    e1 = ema(values, period)
    e2 = ema(e1, period)
    e3 = ema(e2, period)
    return [
        NAN if is_nan(a) or is_nan(b) or is_nan(c) else 3.0 * a - 3.0 * b + c
        for a, b, c in zip(e1, e2, e3)
    ]


def hma(values: Sequence[float], period: int) -> list[float]:
    """Hull moving average: WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    if period <= 0:
        return [NAN] * len(values)
    half = max(int(period / 2), 1)
    root = max(int(math.sqrt(period)), 1)
    w1 = wma(values, half)
    w2 = wma(values, period)
    diff = [NAN if is_nan(a) or is_nan(b) else 2.0 * a - b for a, b in zip(w1, w2)]
    return wma(diff, root)


def ma_stack(values: Sequence[float], periods: Sequence[int] = (9, 20, 50, 100, 200)) -> dict[str, list[float]]:
    """EMA stack used by the trend setups.  Returns ``ema_<p>`` series."""
    return {f"ema_{p}": ema(values, p) for p in periods}


# ---------------------------------------------------------------------------
# volatility & range
# ---------------------------------------------------------------------------


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    out = [NAN] * len(close)
    for i in range(len(close)):
        if i == 0:
            out[i] = high[i] - low[i]
            continue
        prev_close = close[i - 1]
        out[i] = max(high[i] - low[i], abs(high[i] - prev_close), abs(low[i] - prev_close))
    return out


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14) -> list[float]:
    """Average True Range with Wilder smoothing."""
    return rma(true_range(high, low, close), period)


def atr_pct(atr_series: Sequence[float], close: Sequence[float]) -> list[float]:
    """ATR as a percentage of price - the volatility measure comparable across symbols."""
    out = [NAN] * len(close)
    for i, a in enumerate(atr_series):
        if is_nan(a) or not close[i]:
            continue
        out[i] = 100.0 * a / close[i]
    return out


def bollinger(values: Sequence[float], period: int = 20, mult: float = 2.0) -> dict[str, list[float]]:
    mid = sma(values, period)
    sd = rolling_std(values, period, sample=True)
    n = len(values)
    upper = [NAN] * n
    lower = [NAN] * n
    bandwidth = [NAN] * n
    percent_b = [NAN] * n
    for i in range(n):
        if is_nan(mid[i]) or is_nan(sd[i]):
            continue
        upper[i] = mid[i] + mult * sd[i]
        lower[i] = mid[i] - mult * sd[i]
        if mid[i]:
            bandwidth[i] = 100.0 * (upper[i] - lower[i]) / mid[i]
        rng = upper[i] - lower[i]
        if rng > 0:
            percent_b[i] = (values[i] - lower[i]) / rng
    return {"mid": mid, "upper": upper, "lower": lower, "std": sd, "bandwidth": bandwidth, "percent_b": percent_b}


def keltner(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 20,
    atr_mult: float = 1.5,
) -> dict[str, list[float]]:
    mid = ema(close, period)
    rng = atr(high, low, close, period)
    n = len(close)
    upper = [NAN] * n
    lower = [NAN] * n
    for i in range(n):
        if is_nan(mid[i]) or is_nan(rng[i]):
            continue
        upper[i] = mid[i] + atr_mult * rng[i]
        lower[i] = mid[i] - atr_mult * rng[i]
    return {"mid": mid, "upper": upper, "lower": lower, "atr": rng}


def donchian(high: Sequence[float], low: Sequence[float], period: int = 20) -> dict[str, list[float]]:
    upper = rolling_max(high, period)
    lower = rolling_min(low, period)
    mid = [
        NAN if is_nan(a) or is_nan(b) else (a + b) / 2.0
        for a, b in zip(upper, lower)
    ]
    return {"upper": upper, "lower": lower, "mid": mid}


def squeeze(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    bb_period: int = 20,
    bb_mult: float = 2.0,
    kc_period: int = 20,
    kc_mult: float = 1.5,
    momentum_period: int = 20,
) -> dict[str, list[float] | list[bool]]:
    """TTM Squeeze: Bollinger inside Keltner == stored energy, momentum == release.

    The momentum series follows LazyBear's formulation, which is what most
    traders mean when they say "squeeze momentum".
    """
    bb = bollinger(close, bb_period, bb_mult)
    kc = keltner(high, low, close, kc_period, kc_mult)
    hh = rolling_max(high, momentum_period)
    ll = rolling_min(low, momentum_period)
    mid_sma = sma(close, momentum_period)
    n = len(close)
    raw = [NAN] * n
    on = [False] * n
    for i in range(n):
        if is_nan(bb["upper"][i]) or is_nan(kc["upper"][i]):
            continue
        on[i] = bb["upper"][i] <= kc["upper"][i] and bb["lower"][i] >= kc["lower"][i]
        if is_nan(hh[i]) or is_nan(ll[i]) or is_nan(mid_sma[i]):
            continue
        raw[i] = close[i] - ((hh[i] + ll[i]) / 2.0 + mid_sma[i]) / 2.0
    lr = linreg(raw, momentum_period)
    fired = [False] * n
    for i in range(1, n):
        fired[i] = on[i - 1] and not on[i]
    return {"on": on, "momentum": lr["value"], "momentum_slope": lr["slope"], "fired": fired}


def historical_volatility(values: Sequence[float], period: int = 20, bars_per_year: int = 250) -> list[float]:
    """Annualised standard deviation of log returns, in percent."""
    n = len(values)
    logret = [NAN] * n
    for i in range(1, n):
        if values[i] > 0 and values[i - 1] > 0:
            logret[i] = math.log(values[i] / values[i - 1])
    sd = rolling_std(logret, period, sample=True)
    factor = math.sqrt(bars_per_year)
    return [NAN if is_nan(s) else 100.0 * s * factor for s in sd]


# ---------------------------------------------------------------------------
# oscillators
# ---------------------------------------------------------------------------


def rsi(values: Sequence[float], period: int = 14) -> list[float]:
    """Relative Strength Index with Wilder smoothing."""
    n = len(values)
    out = [NAN] * n
    if n <= period:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        delta = values[i] - values[i - 1]
        gains[i] = max(delta, 0.0)
        losses[i] = max(-delta, 0.0)
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def stoch_rsi(values: Sequence[float], rsi_period: int = 14, stoch_period: int = 14, k_period: int = 3, d_period: int = 3) -> dict[str, list[float]]:
    base = rsi(values, rsi_period)
    hi = rolling_max(base, stoch_period)
    lo = rolling_min(base, stoch_period)
    n = len(values)
    raw = [NAN] * n
    for i in range(n):
        if is_nan(base[i]) or is_nan(hi[i]) or is_nan(lo[i]):
            continue
        rng = hi[i] - lo[i]
        raw[i] = 50.0 if rng == 0 else 100.0 * (base[i] - lo[i]) / rng
    k = sma(raw, k_period)
    d = sma(k, d_period)
    return {"k": k, "d": d, "raw": raw}


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, list[float]]:
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    line = [NAN if is_nan(a) or is_nan(b) else a - b for a, b in zip(fast_ema, slow_ema)]
    sig = ema(line, signal)
    hist = [NAN if is_nan(a) or is_nan(b) else a - b for a, b in zip(line, sig)]
    return {"line": line, "signal": sig, "hist": hist}


def stochastic(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    k_period: int = 14,
    d_period: int = 3,
    smooth_k: int = 3,
) -> dict[str, list[float]]:
    hh = rolling_max(high, k_period)
    ll = rolling_min(low, k_period)
    n = len(close)
    raw = [NAN] * n
    for i in range(n):
        if is_nan(hh[i]) or is_nan(ll[i]):
            continue
        rng = hh[i] - ll[i]
        raw[i] = 50.0 if rng == 0 else 100.0 * (close[i] - ll[i]) / rng
    k = sma(raw, smooth_k)
    d = sma(k, d_period)
    return {"k": k, "d": d, "raw": raw}


def cci(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 20) -> list[float]:
    tp = [(h + lo + c) / 3.0 for h, lo, c in zip(high, low, close)]
    base = sma(tp, period)
    mad = rolling(tp, period, lambda w: sum(abs(v - mean(w)) for v in w) / len(w))
    n = len(close)
    out = [NAN] * n
    for i in range(n):
        if is_nan(base[i]) or is_nan(mad[i]) or mad[i] == 0:
            continue
        out[i] = (tp[i] - base[i]) / (0.015 * mad[i])
    return out


def williams_r(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14) -> list[float]:
    hh = rolling_max(high, period)
    ll = rolling_min(low, period)
    out = [NAN] * len(close)
    for i in range(len(close)):
        if is_nan(hh[i]) or is_nan(ll[i]):
            continue
        rng = hh[i] - ll[i]
        out[i] = -50.0 if rng == 0 else -100.0 * (hh[i] - close[i]) / rng
    return out


def roc(values: Sequence[float], period: int = 10) -> list[float]:
    n = len(values)
    out = [NAN] * n
    for i in range(period, n):
        prev = values[i - period]
        if prev:
            out[i] = 100.0 * (values[i] - prev) / prev
    return out


def momentum(values: Sequence[float], period: int = 10) -> list[float]:
    n = len(values)
    out = [NAN] * n
    for i in range(period, n):
        out[i] = values[i] - values[i - period]
    return out


def awesome_oscillator(high: Sequence[float], low: Sequence[float], fast: int = 5, slow: int = 34) -> list[float]:
    mid = [(h + lo) / 2.0 for h, lo in zip(high, low)]
    f = sma(mid, fast)
    s = sma(mid, slow)
    return [NAN if is_nan(a) or is_nan(b) else a - b for a, b in zip(f, s)]


# ---------------------------------------------------------------------------
# trend filters
# ---------------------------------------------------------------------------


def adx(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14) -> dict[str, list[float]]:
    """Average Directional Index with +DI / -DI (Wilder)."""
    n = len(close)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
    tr_smooth = rma(true_range(high, low, close), period)
    pdm_s = rma(plus_dm, period)
    mdm_s = rma(minus_dm, period)
    plus_di = [NAN] * n
    minus_di = [NAN] * n
    dx = [NAN] * n
    for i in range(n):
        if is_nan(tr_smooth[i]) or tr_smooth[i] == 0:
            continue
        plus_di[i] = 100.0 * pdm_s[i] / tr_smooth[i]
        minus_di[i] = 100.0 * mdm_s[i] / tr_smooth[i]
        total = plus_di[i] + minus_di[i]
        dx[i] = 0.0 if total == 0 else 100.0 * abs(plus_di[i] - minus_di[i]) / total
    adx_series = rma(dx, period)
    return {
        "adx": adx_series,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "di_diff": [
            NAN if is_nan(a) or is_nan(b) else a - b
            for a, b in zip(plus_di, minus_di)
        ],
        "tr": tr_smooth,
    }


def supertrend(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 10,
    mult: float = 3.0,
) -> dict[str, list[float] | list[int]]:
    """SuperTrend with the standard band-ratchet logic; ``direction`` is 1/-1."""
    n = len(close)
    atr_series = atr(high, low, close, period)
    upper_basic = [NAN] * n
    lower_basic = [NAN] * n
    for i in range(n):
        if is_nan(atr_series[i]):
            continue
        hl2 = (high[i] + low[i]) / 2.0
        upper_basic[i] = hl2 + mult * atr_series[i]
        lower_basic[i] = hl2 - mult * atr_series[i]

    upper_final = [NAN] * n
    lower_final = [NAN] * n
    direction = [0] * n
    st = [NAN] * n
    trend = 0
    for i in range(n):
        if is_nan(upper_basic[i]):
            continue
        bootstrapping = i == 0 or is_nan(upper_final[i - 1])
        if bootstrapping:
            upper_final[i] = upper_basic[i]
            lower_final[i] = lower_basic[i]
        else:
            prev_close = close[i - 1]
            if upper_basic[i] < upper_final[i - 1] or prev_close > upper_final[i - 1]:
                upper_final[i] = upper_basic[i]
            else:
                upper_final[i] = upper_final[i - 1]
            if lower_basic[i] > lower_final[i - 1] or prev_close < lower_final[i - 1]:
                lower_final[i] = lower_basic[i]
            else:
                lower_final[i] = lower_final[i - 1]
        if trend == 0:
            trend = 1 if close[i] > upper_final[i] else (-1 if close[i] < lower_final[i] else 1)
        elif trend == 1 and close[i] < lower_final[i]:
            trend = -1
        elif trend == -1 and close[i] > upper_final[i]:
            trend = 1
        direction[i] = trend
        st[i] = lower_final[i] if trend == 1 else upper_final[i]
    return {"line": st, "direction": direction, "upper": upper_final, "lower": lower_final}


def parabolic_sar(
    high: Sequence[float],
    low: Sequence[float],
    step: float = 0.02,
    af_max: float = 0.2,
) -> dict[str, list[float] | list[int]]:
    """Wilder's Parabolic SAR."""
    n = len(high)
    sar = [NAN] * n
    trend = [0] * n
    if n < 2:
        return {"sar": sar, "trend": trend}
    bullish = high[1] + low[1] >= high[0] + low[0]
    af = step
    ep = high[0] if bullish else low[0]
    current = low[0] if bullish else high[0]
    sar[0] = current
    trend[0] = 1 if bullish else -1
    for i in range(1, n):
        prev = sar[i - 1] + af * (ep - sar[i - 1])
        if bullish:
            prev = min(prev, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < prev:
                bullish = False
                prev = ep
                ep = low[i]
                af = step
            elif high[i] > ep:
                ep = high[i]
                af = min(af + step, af_max)
        else:
            prev = max(prev, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > prev:
                bullish = True
                prev = ep
                ep = high[i]
                af = step
            elif low[i] < ep:
                ep = low[i]
                af = min(af + step, af_max)
        sar[i] = prev
        trend[i] = 1 if bullish else -1
    return {"sar": sar, "trend": trend}


def ichimoku(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
    displacement: int = 26,
) -> dict[str, list[float]]:
    """Ichimoku Kinko Hyo.  Cloud values are shifted forward by ``displacement`` bars."""
    n = len(close)
    hh_t = rolling_max(high, tenkan)
    ll_t = rolling_min(low, tenkan)
    hh_k = rolling_max(high, kijun)
    ll_k = rolling_min(low, kijun)
    hh_s = rolling_max(high, senkou_b)
    ll_s = rolling_min(low, senkou_b)
    conv = [NAN if is_nan(a) or is_nan(b) else (a + b) / 2.0 for a, b in zip(hh_t, ll_t)]
    base = [NAN if is_nan(a) or is_nan(b) else (a + b) / 2.0 for a, b in zip(hh_k, ll_k)]

    def shift_forward(series: list[float], by: int, filler: float = NAN) -> list[float]:
        out = [filler] * n
        for i in range(n):
            j = i + by
            if j < n:
                out[j] = series[i]
        return out

    span_a = [
        NAN if is_nan(a) or is_nan(b) else (a + b) / 2.0
        for a, b in zip(conv, base)
    ]
    span_b = [NAN if is_nan(a) or is_nan(b) else (a + b) / 2.0 for a, b in zip(hh_s, ll_s)]
    chikou = [close[i - displacement] if i - displacement >= 0 else NAN for i in range(n)]
    return {
        "tenkan": conv,
        "kijun": base,
        "senkou_a": shift_forward(span_a, displacement),
        "senkou_b": shift_forward(span_b, displacement),
        "chikou": chikou,
        "cloud_top": [
            NAN if is_nan(a) or is_nan(b) else max(a, b)
            for a, b in zip(shift_forward(span_a, displacement), shift_forward(span_b, displacement))
        ],
        "cloud_bottom": [
            NAN if is_nan(a) or is_nan(b) else min(a, b)
            for a, b in zip(shift_forward(span_a, displacement), shift_forward(span_b, displacement))
        ],
    }


# ---------------------------------------------------------------------------
# volume
# ---------------------------------------------------------------------------


def obv(close: Sequence[float], volume: Sequence[float]) -> list[float]:
    out = [0.0] * len(close)
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            out[i] = out[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            out[i] = out[i - 1] - volume[i]
        else:
            out[i] = out[i - 1]
    return out


def accumulation_distribution(high: Sequence[float], low: Sequence[float], close: Sequence[float], volume: Sequence[float]) -> list[float]:
    out = [0.0] * len(close)
    for i in range(len(close)):
        rng = high[i] - low[i]
        mfm = 0.0 if rng == 0 else ((close[i] - low[i]) - (high[i] - close[i])) / rng
        mfv = mfm * volume[i]
        out[i] = (out[i - 1] if i else 0.0) + mfv
    return out


def chaikin_money_flow(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    volume: Sequence[float],
    period: int = 20,
) -> list[float]:
    n = len(close)
    mfv = [0.0] * n
    vol_sum = rolling_sum(volume, period)
    out = [NAN] * n
    for i in range(n):
        rng = high[i] - low[i]
        mfm = 0.0 if rng == 0 else ((close[i] - low[i]) - (high[i] - close[i])) / rng
        mfv[i] = mfm * volume[i]
        if i >= period - 1 and vol_sum[i] and not is_nan(vol_sum[i]):
            out[i] = sum(mfv[i - period + 1 : i + 1]) / vol_sum[i]
    return out


def mfi(high: Sequence[float], low: Sequence[float], close: Sequence[float], volume: Sequence[float], period: int = 14) -> list[float]:
    n = len(close)
    tp = [(h + lo + c) / 3.0 for h, lo, c in zip(high, low, close)]
    raw = [t * v for t, v in zip(tp, volume)]
    pos = [0.0] * n
    neg = [0.0] * n
    for i in range(1, n):
        if tp[i] > tp[i - 1]:
            pos[i] = raw[i]
        elif tp[i] < tp[i - 1]:
            neg[i] = raw[i]
    pos_sum = rolling_sum(pos, period)
    neg_sum = rolling_sum(neg, period)
    out = [NAN] * n
    for i in range(n):
        if is_nan(pos_sum[i]) or is_nan(neg_sum[i]):
            continue
        if neg_sum[i] == 0:
            out[i] = 100.0
            continue
        out[i] = 100.0 - 100.0 / (1.0 + pos_sum[i] / neg_sum[i])
    return out


def vwap(candles: Sequence[Candle], anchor: str = "session", period: int | None = None) -> list[float]:
    """Volume weighted average price.

    ``anchor="session"`` resets every calendar day (correct for intraday bars on
    the TSE); ``anchor="rolling"`` uses a trailing window, which is what makes
    sense on daily bars.
    """
    out = [NAN] * len(candles)
    if not candles:
        return out
    if anchor == "rolling" and period:
        pv = [c.typical * c.volume for c in candles]
        pv_sum = rolling_sum(pv, period)
        v_sum = rolling_sum([c.volume for c in candles], period)
        for i in range(len(candles)):
            if not is_nan(pv_sum[i]) and not is_nan(v_sum[i]) and v_sum[i] > 0:
                out[i] = pv_sum[i] / v_sum[i]
        return out

    day = None
    cum_pv = 0.0
    cum_v = 0.0
    for i, c in enumerate(candles):
        key = c.dt.date()
        if key != day:
            day = key
            cum_pv = 0.0
            cum_v = 0.0
        cum_pv += c.typical * c.volume
        cum_v += c.volume
        out[i] = cum_pv / cum_v if cum_v > 0 else c.typical
    return out


def relative_volume(volume: Sequence[float], period: int = 20) -> list[float]:
    avg = sma(volume, period)
    return [NAN if is_nan(a) or not a else v / a for v, a in zip(volume, avg)]


def volume_profile(candles: Sequence[Candle], bins: int = 60, lookback: int | None = None) -> dict:
    """Price-by-volume histogram with POC / VAH / VAL (70% value area)."""
    data = list(candles[-lookback:]) if lookback else list(candles)
    if not data:
        return {"poc": NAN, "vah": NAN, "val": NAN, "bins": []}
    lo = min(c.low for c in data)
    hi = max(c.high for c in data)
    if hi <= lo:
        return {"poc": lo, "vah": hi, "val": lo, "bins": [{"price": lo, "volume": sum(c.volume for c in data)}]}
    width = (hi - lo) / bins
    counts = [0.0] * bins
    for c in data:
        idx = int(min(max((c.typical - lo) / width, 0.0), bins - 0.0001))
        counts[idx] += c.volume
    poc_idx = max(range(bins), key=lambda i: counts[i])
    total = sum(counts) or 1.0
    # Expand outwards from the POC until 70% of volume is captured.
    low_i = high_i = poc_idx
    captured = counts[poc_idx]
    while captured / total < 0.70 and (low_i > 0 or high_i < bins - 1):
        take_low = counts[low_i - 1] if low_i > 0 else -1.0
        take_high = counts[high_i + 1] if high_i < bins - 1 else -1.0
        if take_low >= take_high:
            low_i -= 1
            captured += counts[low_i]
        else:
            high_i += 1
            captured += counts[high_i]
    price_of = lambda i: lo + (i + 0.5) * width  # noqa: E731
    return {
        "poc": price_of(poc_idx),
        "vah": price_of(high_i),
        "val": price_of(low_i),
        "low": lo,
        "high": hi,
        "bins": [{"price": price_of(i), "volume": counts[i]} for i in range(bins)],
    }


# ---------------------------------------------------------------------------
# structure helpers
# ---------------------------------------------------------------------------


def zigzag(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    threshold_pct: float = 5.0,
    use_close: bool = False,
) -> list[dict]:
    """Percentage ZigZag.

    Returns alternating pivots ``{"index", "price", "kind": "high"|"low"}``.  A
    pivot is only *confirmed* once price moves ``threshold_pct`` away from it, so
    the trailing pivot is marked ``provisional=True`` and callers must not treat
    it as history (it can still be overwritten by the next bar).
    """
    n = len(close)
    if n < 3 or threshold_pct <= 0:
        return []
    thr = threshold_pct / 100.0

    def hi(i: int) -> float:
        return close[i] if use_close else high[i]

    def lo(i: int) -> float:
        return close[i] if use_close else low[i]

    pivots: list[dict] = []
    trend = 0  # 1 up, -1 down, 0 undecided
    max_i, max_p = 0, hi(0)
    min_i, min_p = 0, lo(0)

    def reset_leg(kind: str, pivot_index: int, last: int) -> tuple[int, float]:
        """First extremum of the leg that starts *strictly after* ``pivot_index``.

        A wide-range bar can hold both the high and the low that trigger a flip.
        Searching the new leg from the pivot bar itself therefore re-finds the
        very same bar and ping-pongs forever, emitting a pivot pair on every
        later bar.  Starting after the pivot keeps the indices strictly
        increasing; when nothing is left in the window the sentinel value keeps
        the leg unconfirmed until a later bar provides one.
        """
        index = pivot_index + 1
        value = UNSET if kind == "low" else 0.0
        for j in range(pivot_index + 1, last + 1):
            candidate = lo(j) if kind == "low" else hi(j)
            if (candidate < value) if kind == "low" else (candidate > value):
                index, value = j, candidate
        return index, value

    for i in range(n):
        if trend == 0:
            if hi(i) > max_p:
                max_i, max_p = i, hi(i)
            if lo(i) < min_p:
                min_i, min_p = i, lo(i)
            if min_p > 0 and max_p >= min_p * (1.0 + thr) and max_i > min_i:
                pivots.append({"index": min_i, "price": min_p, "kind": "low"})
                trend = 1
                max_i, max_p = reset_leg("high", min_i, i)
            elif max_p > 0 and min_p <= max_p * (1.0 - thr) and min_i > max_i:
                pivots.append({"index": max_i, "price": max_p, "kind": "high"})
                trend = -1
                min_i, min_p = reset_leg("low", max_i, i)
        elif trend == 1:
            if hi(i) > max_p:
                max_i, max_p = i, hi(i)
            confirmed = pivots[-1]["index"] if pivots else -1
            if max_p > 0 and max_i > confirmed and lo(i) <= max_p * (1.0 - thr):
                pivots.append({"index": max_i, "price": max_p, "kind": "high"})
                trend = -1
                min_i, min_p = reset_leg("low", max_i, i)
        else:
            if lo(i) < min_p:
                min_i, min_p = i, lo(i)
            confirmed = pivots[-1]["index"] if pivots else -1
            if 0 < min_p < UNSET and min_i > confirmed and hi(i) >= min_p * (1.0 + thr):
                pivots.append({"index": min_i, "price": min_p, "kind": "low"})
                trend = 1
                max_i, max_p = reset_leg("high", min_i, i)

    if trend == 1 and max_p > 0 and max_i > (pivots[-1]["index"] if pivots else -1):
        pivots.append({"index": max_i, "price": max_p, "kind": "high", "provisional": True})
    elif trend == -1 and 0 < min_p < UNSET and min_i > (pivots[-1]["index"] if pivots else -1):
        pivots.append({"index": min_i, "price": min_p, "kind": "low", "provisional": True})
    return pivots


def fractal_swings(high: Sequence[float], low: Sequence[float], strength: int = 2) -> list[dict]:
    """Williams-fractal swing points with a configurable ``strength`` (bars each side)."""
    n = len(high)
    out: list[dict] = []
    for i in range(strength, n - strength):
        window_high = high[i - strength : i + strength + 1]
        window_low = low[i - strength : i + strength + 1]
        if high[i] == max(window_high) and window_high.count(high[i]) == 1:
            out.append({"index": i, "price": high[i], "kind": "high"})
        if low[i] == min(window_low) and window_low.count(low[i]) == 1:
            out.append({"index": i, "price": low[i], "kind": "low"})
    out.sort(key=lambda p: p["index"])
    return out


def alternate_pivots(pivots: Sequence[dict]) -> list[dict]:
    """Keep strict high/low alternation, keeping the more extreme pivot of a pair."""
    out: list[dict] = []
    for pivot in pivots:
        if not out:
            out.append(dict(pivot))
            continue
        prev = out[-1]
        if prev["kind"] == pivot["kind"]:
            better = pivot["price"] > prev["price"] if pivot["kind"] == "high" else pivot["price"] < prev["price"]
            if better:
                out[-1] = dict(pivot)
            continue
        out.append(dict(pivot))
    return out


def pivot_points(high: Sequence[float], low: Sequence[float], close: Sequence[float], style: str = "classic") -> dict[str, float]:
    """Pivot levels computed from the *previous* completed bar."""
    if not high:
        return {}
    h, lo, c = high[-1], low[-1], close[-1]
    p = (h + lo + c) / 3.0
    rng = h - lo
    if style == "fibonacci":
        return {
            "p": p,
            "r1": p + 0.382 * rng,
            "r2": p + 0.618 * rng,
            "r3": p + 1.0 * rng,
            "s1": p - 0.382 * rng,
            "s2": p - 0.618 * rng,
            "s3": p - 1.0 * rng,
        }
    if style == "woodie":
        p = (h + lo + 2 * c) / 4.0
        return {
            "p": p,
            "r1": 2 * p - lo,
            "r2": p + (h - lo),
            "r3": h + 2 * (p - lo),
            "s1": 2 * p - h,
            "s2": p - (h - lo),
            "s3": lo - 2 * (h - p),
        }
    return {
        "p": p,
        "r1": 2 * p - lo,
        "r2": p + (h - lo),
        "r3": h + 2 * (p - lo),
        "s1": 2 * p - h,
        "s2": p - (h - lo),
        "s3": lo - 2 * (h - p),
    }


def fibonacci_levels(start: float, end: float, direction: str = "up") -> dict[str, float]:
    """Retracement and extension levels between two swing points."""
    rng = end - start
    ratios = {
        "0.0": 0.0,
        "0.236": 0.236,
        "0.382": 0.382,
        "0.5": 0.5,
        "0.618": 0.618,
        "0.705": 0.705,
        "0.786": 0.786,
        "1.0": 1.0,
    }
    ext = {"1.272": 1.272, "1.618": 1.618, "2.0": 2.0, "2.618": 2.618}
    sign = -1.0 if direction == "down" else 1.0
    # abs(rng) keeps every level inside the swing (and extends past the *end* of
    # the move): for an up move the levels climb from the low, for a down move
    # they fall from the high.  Multiplying by the signed range instead pushes
    # the down-move levels out of the range entirely.
    span = sign * abs(rng)
    retracement = {k: round(start + span * v, 6) for k, v in ratios.items()}
    extension = {k: round(start + span * v, 6) for k, v in ext.items()}
    return {"retracement": retracement, "extension": extension, "range": rng, "start": start, "end": end}


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def compute_all(candles: Sequence[Candle], timeframe: str | None = None) -> dict:
    """Compute the full indicator suite for a candle list.

    This is the single entry point used by the dashboard, the CLI and the
    backtester so that every surface reports identical numbers.
    """
    if not candles:
        return {}
    tf = timeframe or candles[0].timeframe
    high = col(candles, "high")
    low = col(candles, "low")
    close = col(candles, "close")
    volume = col(candles, "volume")
    n = len(candles)

    bb = bollinger(close, 20, 2.0)
    kc = keltner(high, low, close, 20, 1.5)
    dc = donchian(high, low, 20)
    atr14 = atr(high, low, close, 14)
    dmi = adx(high, low, close, 14)
    st = supertrend(high, low, close, 10, 3.0)
    ichi = ichimoku(high, low, close)
    sq = squeeze(high, low, close)
    stack = ma_stack(close)
    bpy = 250 if tf in ("D1", "W1", "MN1") else 330
    return {
        "timeframe": tf,
        "bars": n,
        "close": close,
        "high": high,
        "low": low,
        "open": col(candles, "open"),
        "volume": volume,
        "value": col(candles, "value"),
        "dt": [c.dt for c in candles],
        "sma20": sma(close, 20),
        "sma50": sma(close, 50),
        "sma200": sma(close, 200),
        "ema9": stack["ema_9"],
        "ema20": stack["ema_20"],
        "ema21": ema(close, 21),
        "ema50": stack["ema_50"],
        "ema100": stack["ema_100"],
        "ema200": stack["ema_200"],
        "hma9": hma(close, 9),
        "bb": bb,
        "keltner": kc,
        "donchian": dc,
        "atr14": atr14,
        "atr_pct": atr_pct(atr14, close),
        "rsi14": rsi(close, 14),
        "rsi6": rsi(close, 6),
        "macd": macd(close),
        "stoch": stochastic(high, low, close),
        "stoch_rsi": stoch_rsi(close),
        "cci20": cci(high, low, close, 20),
        "williams_r": williams_r(high, low, close),
        "mfi14": mfi(high, low, close, volume),
        "obv": obv(close, volume),
        "obv_ema20": ema(obv(close, volume), 20),
        "adl": accumulation_distribution(high, low, close, volume),
        "cmf20": chaikin_money_flow(high, low, close, volume),
        "vwap": vwap(candles, anchor="rolling" if tf in ("D1", "W1", "MN1") else "session", period=20),
        "rel_volume": relative_volume(volume, 20),
        "roc10": roc(close, 10),
        "ao": awesome_oscillator(high, low),
        "adx": dmi,
        "supertrend": st,
        "psar": parabolic_sar(high, low),
        "ichimoku": ichi,
        "squeeze": sq,
        "hv20": historical_volatility(close, 20, bpy),
        "volume_sma20": sma(volume, 20),
        "close_slope20": linreg(close, 20)["slope"],
        "close_r2_20": linreg(close, 20)["r2"],
    }


def snapshot(ind: dict) -> dict:
    """Latest value + a plain-language state for every indicator (dashboard cards)."""
    if not ind:
        return {}
    close = ind["close"]
    last_close = last_valid(close)
    prev_close = last_valid(close[:-1]) if len(close) > 1 else NAN

    def cur(key: str) -> float:
        return last_valid(ind[key])

    rsi14 = cur("rsi14")
    adx_val = last_valid(ind["adx"]["adx"])
    di_diff = last_valid(ind["adx"]["di_diff"])
    st_dir = ind["supertrend"]["direction"][-1] if ind["supertrend"]["direction"] else 0
    macd_hist = last_valid(ind["macd"]["hist"])
    macd_hist_prev = last_valid(ind["macd"]["hist"][:-1])
    ema9, ema20, ema50, ema200 = cur("ema9"), cur("ema20"), cur("ema50"), cur("ema200")
    bb_pct = last_valid(ind["bb"]["percent_b"])
    trend_score = 0
    if not is_nan(ema9) and not is_nan(ema20):
        trend_score += 1 if ema9 > ema20 else -1
    if not is_nan(ema20) and not is_nan(ema50):
        trend_score += 1 if ema20 > ema50 else -1
    if not is_nan(ema50) and not is_nan(ema200):
        trend_score += 1 if ema50 > ema200 else -1
    if not is_nan(last_close) and not is_nan(ema200):
        trend_score += 1 if last_close > ema200 else -1
    if not is_nan(adx_val):
        trend_score += 1 if di_diff > 0 else -1

    rsi_state = "neutral"
    if not is_nan(rsi14):
        if rsi14 >= 70:
            rsi_state = "overbought"
        elif rsi14 <= 30:
            rsi_state = "oversold"
        elif rsi14 >= 55:
            rsi_state = "bullish"
        elif rsi14 <= 45:
            rsi_state = "bearish"

    trend_label = "صعودی" if trend_score >= 3 else ("نزولی" if trend_score <= -3 else "خنثی / رنج")
    return {
        "price": {"close": last_close, "prev_close": prev_close, "change_pct": 100.0 * (last_close - prev_close) / prev_close if prev_close else None},
        "trend": {
            "label": trend_label,
            "score": trend_score,
            "adx": adx_val,
            "adx_state": "قوی" if (not is_nan(adx_val) and adx_val >= 25) else ("متوسط" if (not is_nan(adx_val) and adx_val >= 20) else "ضعیف / رنج"),
            "plus_di": last_valid(ind["adx"]["plus_di"]),
            "minus_di": last_valid(ind["adx"]["minus_di"]),
            "ema_stack_bullish": bool(not is_nan(ema9) and not is_nan(ema20) and not is_nan(ema50) and ema9 > ema20 > ema50),
            "ema_stack_bearish": bool(not is_nan(ema9) and not is_nan(ema20) and not is_nan(ema50) and ema9 < ema20 < ema50),
            "above_ema200": bool(not is_nan(ema200) and last_close > ema200),
            "supertrend_direction": st_dir,
            "psar_direction": ind["psar"]["trend"][-1] if ind["psar"]["trend"] else 0,
        },
        "momentum": {
            "rsi14": rsi14,
            "rsi_state": rsi_state,
            "rsi6": cur("rsi6"),
            "stoch_rsi_k": last_valid(ind["stoch_rsi"]["k"]),
            "stoch_k": last_valid(ind["stoch"]["k"]),
            "stoch_d": last_valid(ind["stoch"]["d"]),
            "macd_line": last_valid(ind["macd"]["line"]),
            "macd_signal": last_valid(ind["macd"]["signal"]),
            "macd_hist": macd_hist,
            "macd_hist_rising": bool(not is_nan(macd_hist) and not is_nan(macd_hist_prev) and macd_hist > macd_hist_prev),
            "cci20": cur("cci20"),
            "williams_r": cur("williams_r"),
            "roc10": cur("roc10"),
            "ao": cur("ao"),
        },
        "volatility": {
            "atr14": cur("atr14"),
            "atr_pct": cur("atr_pct"),
            "bb_bandwidth": last_valid(ind["bb"]["bandwidth"]),
            "bb_percent_b": bb_pct,
            "squeeze_on": bool(ind["squeeze"]["on"][-1]) if ind["squeeze"]["on"] else False,
            "squeeze_momentum": last_valid(ind["squeeze"]["momentum"]),
            "hv20": cur("hv20"),
            "donchian_upper": last_valid(ind["donchian"]["upper"]),
            "donchian_lower": last_valid(ind["donchian"]["lower"]),
        },
        "volume": {
            "volume": ind["volume"][-1] if ind.get("volume") else None,
            "volume_sma20": cur("volume_sma20"),
            "relative_volume": cur("rel_volume"),
            "mfi14": cur("mfi14"),
            "cmf20": cur("cmf20"),
            "obv": cur("obv"),
            "vwap": cur("vwap"),
        },
        "levels": {
            "ema9": ema9,
            "ema20": ema20,
            "ema50": ema50,
            "ema100": cur("ema100"),
            "ema200": ema200,
            "bb_upper": last_valid(ind["bb"]["upper"]),
            "bb_mid": last_valid(ind["bb"]["mid"]),
            "bb_lower": last_valid(ind["bb"]["lower"]),
            "ichimoku_tenkan": last_valid(ind["ichimoku"]["tenkan"]),
            "ichimoku_kijun": last_valid(ind["ichimoku"]["kijun"]),
            "ichimoku_cloud_top": last_valid(ind["ichimoku"]["cloud_top"]),
            "ichimoku_cloud_bottom": last_valid(ind["ichimoku"]["cloud_bottom"]),
            "supertrend": cur("supertrend"),
            "psar": cur("psar"),
        },
    }


__all__ = [
    "accumulation_distribution",
    "adx",
    "atr",
    "atr_pct",
    "alternate_pivots",
    "awesome_oscillator",
    "bollinger",
    "cci",
    "chaikin_money_flow",
    "compute_all",
    "dema",
    "donchian",
    "ema",
    "fibonacci_levels",
    "fractal_swings",
    "hma",
    "historical_volatility",
    "ichimoku",
    "keltner",
    "linreg",
    "ma_stack",
    "macd",
    "mfi",
    "momentum",
    "obv",
    "parabolic_sar",
    "pivot_points",
    "relative_volume",
    "rma",
    "roc",
    "rsi",
    "sma",
    "snapshot",
    "squeeze",
    "stoch_rsi",
    "stochastic",
    "supertrend",
    "tema",
    "true_range",
    "vwap",
    "volume_profile",
    "williams_r",
    "wma",
    "zigzag",
]
