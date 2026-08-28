"""Trend-following setup library.

Every setup is a pure function of ``(indicator dict, bar index)`` using only
causal series, so the exact same code produces:

* the **historical signal list** the backtester measures the win rate on, and
* the **current-bar signals** the dashboard highlights.

There is no look-ahead: a signal at bar *i* reads nothing after *i*.  The
backtester then fills at the open of bar *i+1* (market setups) or at the limit
price if the market trades through it (RTM/ACT retest setups).

Shorts are produced only for venues that allow selling first; the TSE equity
market is long-only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .core import Candle, is_nan

SETUP_LIBRARY: dict[str, dict] = {
    "trend_pullback_ema20": {
        "name": "پولبک به EMA20 در روند صعودی",
        "family": "trend_pullback",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["روند", "میانگین متحرک", "پولبک"],
        "logic": "چیدمان صعودی EMA9>EMA20>EMA50 + ADX بالای ۲۲ + لمس EMA20 + کندل برگشتی. ورود با تأیید، استاپ زیر کف نوسان، هدف ۲ برابر ریسک.",
    },
    "supertrend_flip": {
        "name": "چرخش سوپرتند",
        "family": "trend_follow",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["روند", "سوپرتند"],
        "logic": "تغییر جهت سوپرتند به صعودی در حالی که قیمت بالای EMA50 و ADX بالای ۲۰ است. استاپ روی خط سوپرتند.",
    },
    "donchian_breakout": {
        "name": "شکست کانال دانچیان (لاک‌پشتی)",
        "family": "breakout",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["شکست", "کانال", "حجم"],
        "logic": "بسته‌شدن بالای سقف ۲۰ کندلی با حجم نسبی بالاتر از ۱.۲ و ADX بالای ۲۰. استاپ زیر کف ۱۰ کندلی.",
    },
    "macd_trend_pullback": {
        "name": "بازگشت هیستوگرام MACD در روند",
        "family": "momentum",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["مکدی", "مومنتوم", "روند"],
        "logic": "صعودی‌شدن هیستوگرام MACD از منفی به مثبت، در حالی که قیمت بالای EMA200 است. استاپ زیر کف اخیر.",
    },
    "bollinger_squeeze_breakout": {
        "name": "شکست فشردگی بولینگر",
        "family": "breakout",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["فشردگی", "نوسان", "شکست"],
        "logic": "آزادسازی انرژی فشردگی (بولینگر داخل کلتنر) همراه با بسته‌شدن بالای باند بالا و حجم نسبی بالای ۱.۳.",
    },
    "rsi_midline_cross": {
        "name": "عبور RSI از خط میانی در روند",
        "family": "momentum",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["RSI", "مومنتوم"],
        "logic": "عبور RSI(14) از ۵۰ به بالا وقتی قیمت بالای EMA50 و ADX بالای ۲۰ است.",
    },
    "ichimoku_kumo_breakout": {
        "name": "شکست ابر ایچیموکو",
        "family": "trend_follow",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["ایچیموکو", "روند"],
        "logic": "عبور قیمت از سقف ابر + تقاطع صعودی تنکان/کیجون + قرارگیری چیکو بالای قیمت. استاپ زیر کیجون.",
    },
    "ma_crossover": {
        "name": "تقاطع EMA9/EMA21 در جهت روند",
        "family": "trend_follow",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["میانگین متحرک", "تقاطع"],
        "logic": "تقاطع صعودی EMA9 و EMA21 وقتی قیمت بالای EMA50 و ADX بالای ۲۰ است.",
    },
    "psar_flip": {
        "name": "چرخش پارابولیک SAR",
        "family": "trend_follow",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["SAR", "روند"],
        "logic": "قرارگرفتن SAR زیر قیمت (چرخش صعودی) با تأیید EMA50 و ADX بالای ۲۰. استاپ روی نقطه SAR.",
    },
    "vwap_reclaim": {
        "name": "بازپس‌گیری VWAP با حجم",
        "family": "intraday",
        "direction": 1,
        "entry_kind": "market",
        "tags": ["VWAP", "حجم", "کوتاه‌مدت"],
        "logic": "بسته‌شدن بالای VWAP با حجم نسبی بالای ۱.۸ و قیمت بالای EMA20 - مناسب تایم‌فریم‌های کوتاه.",
    },
}

#: Short-side setups.  They are *not* mirrored longs: each one is its own rule
#: set (breakdown / bearish flip / bearish cross) so the win rate that gets
#: measured is the win rate of the rule that actually fires.
SHORT_SETUPS = ("supertrend_flip_short", "donchian_breakdown_short", "ma_crossover_short")

SETUP_LIBRARY.update(
    {
        "supertrend_flip_short": {
            "name": "چرخش نزولی سوپرتند (فروش)",
            "family": "trend_follow",
            "direction": -1,
            "entry_kind": "market",
            "tags": ["روند", "سوپرتند", "فروش"],
            "logic": "چرخش سوپرتند به نزولی وقتی قیمت زیر EMA50 است. فقط در بازارهایی که فروش استقراضی دارند.",
        },
        "donchian_breakdown_short": {
            "name": "شکست کف کانال دانچیان (فروش)",
            "family": "breakout",
            "direction": -1,
            "entry_kind": "market",
            "tags": ["شکست", "کانال", "فروش"],
            "logic": "بسته‌شدن زیر کف ۲۰ کندلی با حجم نسبی بالا و ADX بالای ۲۰.",
        },
        "ma_crossover_short": {
            "name": "تقاطع نزولی EMA9/EMA21 (فروش)",
            "family": "trend_follow",
            "direction": -1,
            "entry_kind": "market",
            "tags": ["میانگین متحرک", "تقاطع", "فروش"],
            "logic": "تقاطع نزولی EMA9 و EMA21 وقتی قیمت زیر EMA50 است.",
        },
    }
)


def _series(ind: dict, key: str) -> list[float]:
    return ind.get(key) or []


def _crossed_above(series: Sequence[float], level: float, i: int) -> bool:
    if i < 1 or is_nan(series[i]) or is_nan(series[i - 1]):
        return False
    return series[i - 1] <= level < series[i]


def _crossed_below(series: Sequence[float], level: float, i: int) -> bool:
    if i < 1 or is_nan(series[i]) or is_nan(series[i - 1]):
        return False
    return series[i - 1] >= level > series[i]


def _signal(setup_id: str, candles: Sequence[Candle], i: int, direction: int, entry: float, stop: float, target: float, reasons: list[str], extra: dict | None = None, entry_kind: str = "market") -> dict | None:
    if is_nan(entry) or is_nan(stop) or is_nan(target):
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    meta = SETUP_LIBRARY[setup_id]
    return {
        "engine": "setup",
        "setup": setup_id,
        "setup_name": meta["name"],
        "type": setup_id,
        "direction": direction,
        "entry": round(entry, 6),
        "stop": round(stop, 6),
        "target": round(target, 6),
        "rr": round(abs(target - entry) / risk, 2),
        "risk": round(risk, 6),
        "reasons": reasons,
        "reason": "؛ ".join(reasons),
        "entry_kind": entry_kind,
        "index": i,
        "dt": candles[i].dt.isoformat(),
        **(extra or {}),
    }


# ---------------------------------------------------------------------------
# detectors: (indicators, index, candles) -> signal | None
# ---------------------------------------------------------------------------


def detect_trend_pullback_ema20(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 60:
        return None
    ema9, ema20, ema50 = ind["ema9"], ind["ema20"], ind["ema50"]
    adx_series = ind["adx"]["adx"]
    if any(is_nan(ind[k][i]) for k in ("ema9", "ema20", "ema50")) or is_nan(adx_series[i]):
        return None
    stacked = ema9[i] > ema20[i] > ema50[i]
    trend_ok = adx_series[i] >= 22.0
    touched = candles[i].low <= ema20[i] <= candles[i].high or abs(candles[i].low - ema20[i]) <= 0.35 * ind["atr14"][i]
    reversal = candles[i].is_bullish and (candles[i].close > candles[i - 1].high or candles[i].lower_shadow >= candles[i].body)
    if not (stacked and trend_ok and touched and reversal):
        return None
    stop = min(min(c.low for c in candles[max(0, i - 5) : i + 1]), ema50[i]) - 0.5 * ind["atr14"][i]
    entry = candles[i].close
    return _signal(
        "trend_pullback_ema20",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 2.0 * (entry - stop),
        [
            "چیدمان صعودی EMA9 > EMA20 > EMA50",
            f"ADX = {adx_series[i]:.1f} (روند فعال)",
            "قیمت به EMA20 پولبک زده است",
            "کندل برگشتی صعودی تأیید داده است",
        ],
        {"adx": round(adx_series[i], 2)},
    )


def detect_supertrend_flip(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 55:
        return None
    direction_series = ind["supertrend"]["direction"]
    line = ind["supertrend"]["line"]
    if direction_series[i] != 1 or direction_series[i - 1] != -1:
        return None
    if is_nan(ind["ema50"][i]) or is_nan(ind["adx"]["adx"][i]):
        return None
    if candles[i].close <= ind["ema50"][i] or ind["adx"]["adx"][i] < 20.0:
        return None
    entry = candles[i].close
    stop = line[i] if not is_nan(line[i]) else entry - 2 * ind["atr14"][i]
    return _signal(
        "supertrend_flip",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 2.5 * (entry - stop),
        [
            "سوپرتند از نزولی به صعودی چرخیده است",
            "قیمت بالای EMA50 قرار دارد",
            f"ADX = {ind['adx']['adx'][i]:.1f}",
        ],
    )


def detect_donchian_breakout(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 40:
        return None
    upper = ind["donchian"]["upper"]
    if is_nan(upper[i - 1]) or is_nan(ind["adx"]["adx"][i]):
        return None
    if candles[i].close <= upper[i - 1]:
        return None
    if ind["adx"]["adx"][i] < 20.0:
        return None
    rel_volume = ind["rel_volume"][i]
    if not is_nan(rel_volume) and rel_volume < 1.2:
        return None
    entry = candles[i].close
    stop = min(c.low for c in candles[max(0, i - 10) : i + 1]) - 0.25 * ind["atr14"][i]
    return _signal(
        "donchian_breakout",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 3.0 * (entry - stop),
        [
            f"شکست سقف ۲۰ کندلی ({upper[i - 1]:,.0f})",
            f"حجم نسبی = {rel_volume:.2f}" if not is_nan(rel_volume) else "حجم تأیید نشده",
            f"ADX = {ind['adx']['adx'][i]:.1f}",
        ],
    )


def detect_macd_trend_pullback(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 60:
        return None
    hist = ind["macd"]["hist"]
    ema200 = ind["ema200"]
    if is_nan(hist[i]) or is_nan(hist[i - 1]) or is_nan(ema200[i]):
        return None
    if not (hist[i - 1] < 0 <= hist[i] and candles[i].close > ema200[i]):
        return None
    entry = candles[i].close
    stop = min(c.low for c in candles[max(0, i - 8) : i + 1]) - 0.5 * ind["atr14"][i]
    return _signal(
        "macd_trend_pullback",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 2.0 * (entry - stop),
        [
            "هیستوگرام MACD از منفی به مثبت رسیده است",
            "قیمت بالای EMA200 است (روند بلندمدت صعودی)",
        ],
    )


def detect_bollinger_squeeze_breakout(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 60:
        return None
    on = ind["squeeze"]["on"]
    fired = ind["squeeze"]["fired"]
    upper = ind["bb"]["upper"]
    if not (fired[i] or (on[i - 1] and not on[i])):
        return None
    if is_nan(upper[i]) or candles[i].close <= upper[i]:
        return None
    rel_volume = ind["rel_volume"][i]
    if not is_nan(rel_volume) and rel_volume < 1.3:
        return None
    entry = candles[i].close
    stop = ind["bb"]["mid"][i] if not is_nan(ind["bb"]["mid"][i]) else entry - 2 * ind["atr14"][i]
    direction = 1 if candles[i].close > candles[i].open else -1
    if direction < 0:
        stop = ind["bb"]["mid"][i] if not is_nan(ind["bb"]["mid"][i]) else entry + 2 * ind["atr14"][i]
    return _signal(
        "bollinger_squeeze_breakout",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 2.0 * (entry - stop),
        [
            "فشردگی بولینگر آزاد شده است",
            "قیمت بالای باند بالای بولینگر بسته شده است",
            f"حجم نسبی = {rel_volume:.2f}" if not is_nan(rel_volume) else "حجم تأیید نشده",
        ],
    )


def detect_rsi_midline_cross(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 55:
        return None
    rsi_series = ind["rsi14"]
    if is_nan(rsi_series[i]) or is_nan(ind["ema50"][i]) or is_nan(ind["adx"]["adx"][i]):
        return None
    if not _crossed_above(rsi_series, 50.0, i):
        return None
    if candles[i].close <= ind["ema50"][i] or ind["adx"]["adx"][i] < 20.0:
        return None
    entry = candles[i].close
    stop = entry - 2.0 * ind["atr14"][i]
    return _signal(
        "rsi_midline_cross",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 2.0 * (entry - stop),
        [
            f"RSI از ۵۰ عبور کرده ({rsi_series[i]:.1f})",
            "قیمت بالای EMA50 است",
            f"ADX = {ind['adx']['adx'][i]:.1f}",
        ],
    )


def detect_ichimoku_kumo_breakout(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 80:
        return None
    ichi = ind["ichimoku"]
    cloud_top, tenkan, kijun, chikou = ichi["cloud_top"], ichi["tenkan"], ichi["kijun"], ichi["chikou"]
    if is_nan(cloud_top[i]) or is_nan(cloud_top[i - 1]) or is_nan(tenkan[i]) or is_nan(kijun[i]):
        return None
    if not (candles[i - 1].close <= cloud_top[i - 1] and candles[i].close > cloud_top[i]):
        return None
    if tenkan[i] <= kijun[i]:
        return None
    if i >= 26 and not is_nan(chikou[i]) and chikou[i] < candles[i - 26].close:
        return None
    entry = candles[i].close
    stop = kijun[i] - 0.25 * ind["atr14"][i]
    return _signal(
        "ichimoku_kumo_breakout",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 2.5 * (entry - stop),
        [
            "قیمت از سقف ابر ایچیموکو عبور کرده است",
            "تنکان‌سن بالای کیجون‌سن است",
            "چیکو اسپن بالای قیمت قرار دارد",
        ],
    )


def detect_ma_crossover(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 55:
        return None
    ema9, ema21, ema50 = ind["ema9"], ind["ema21"], ind["ema50"]
    if any(is_nan(x[i]) for x in (ema9, ema21, ema50)) or is_nan(ind["adx"]["adx"][i]):
        return None
    if not (ema9[i - 1] <= ema21[i - 1] and ema9[i] > ema21[i]):
        return None
    if candles[i].close <= ema50[i] or ind["adx"]["adx"][i] < 20.0:
        return None
    entry = candles[i].close
    stop = min(c.low for c in candles[max(0, i - 6) : i + 1]) - 0.5 * ind["atr14"][i]
    return _signal(
        "ma_crossover",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 2.0 * (entry - stop),
        [
            "تقاطع صعودی EMA9 و EMA21",
            "قیمت بالای EMA50 است",
            f"ADX = {ind['adx']['adx'][i]:.1f}",
        ],
    )


def detect_psar_flip(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 55:
        return None
    trend = ind["psar"]["trend"]
    sar = ind["psar"]["sar"]
    if trend[i] != 1 or trend[i - 1] != -1:
        return None
    if is_nan(ind["ema50"][i]) or candles[i].close <= ind["ema50"][i] or ind["adx"]["adx"][i] < 20.0:
        return None
    entry = candles[i].close
    stop = sar[i] if not is_nan(sar[i]) else entry - 2 * ind["atr14"][i]
    return _signal(
        "psar_flip",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 2.0 * (entry - stop),
        ["SAR زیر قیمت قرار گرفته (چرخش صعودی)", "قیمت بالای EMA50 است", f"ADX = {ind['adx']['adx'][i]:.1f}"],
    )


def detect_vwap_reclaim(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 25:
        return None
    vwap_series, ema20 = ind["vwap"], ind["ema20"]
    rel_volume = ind["rel_volume"][i]
    if is_nan(vwap_series[i]) or is_nan(ema20[i]) or is_nan(rel_volume):
        return None
    if not (candles[i - 1].close <= vwap_series[i - 1] and candles[i].close > vwap_series[i]):
        return None
    if rel_volume < 1.8 or candles[i].close <= ema20[i]:
        return None
    entry = candles[i].close
    stop = min(vwap_series[i], candles[i].low) - 0.5 * ind["atr14"][i]
    return _signal(
        "vwap_reclaim",
        candles,
        i,
        1,
        entry,
        stop,
        entry + 1.8 * (entry - stop),
        [
            "قیمت VWAP را به سمت بالا شکسته است",
            f"حجم نسبی = {rel_volume:.2f} برابر میانگین",
            "قیمت بالای EMA20 است",
        ],
    )




def detect_supertrend_flip_short(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 55:
        return None
    direction_series = ind["supertrend"]["direction"]
    line = ind["supertrend"]["line"]
    if direction_series[i] != -1 or direction_series[i - 1] != 1:
        return None
    if is_nan(ind["ema50"][i]) or is_nan(ind["adx"]["adx"][i]):
        return None
    if candles[i].close >= ind["ema50"][i] or ind["adx"]["adx"][i] < 20.0:
        return None
    entry = candles[i].close
    stop = line[i] if not is_nan(line[i]) else entry + 2 * ind["atr14"][i]
    return _signal(
        "supertrend_flip_short",
        candles,
        i,
        -1,
        entry,
        stop,
        entry - 2.5 * (stop - entry),
        ["سوپرتند به نزولی چرخیده است", "قیمت زیر EMA50 است", f"ADX = {ind['adx']['adx'][i]:.1f}"],
    )


def detect_donchian_breakdown_short(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 40:
        return None
    lower = ind["donchian"]["lower"]
    if is_nan(lower[i - 1]) or is_nan(ind["adx"]["adx"][i]):
        return None
    if candles[i].close >= lower[i - 1] or ind["adx"]["adx"][i] < 20.0:
        return None
    rel_volume = ind["rel_volume"][i]
    if not is_nan(rel_volume) and rel_volume < 1.2:
        return None
    entry = candles[i].close
    stop = max(c.high for c in candles[max(0, i - 10) : i + 1]) + 0.25 * ind["atr14"][i]
    return _signal(
        "donchian_breakdown_short",
        candles,
        i,
        -1,
        entry,
        stop,
        entry - 3.0 * (stop - entry),
        [
            f"شکست کف ۲۰ کندلی ({lower[i - 1]:,.0f})",
            f"حجم نسبی = {rel_volume:.2f}" if not is_nan(rel_volume) else "حجم تأیید نشده",
            f"ADX = {ind['adx']['adx'][i]:.1f}",
        ],
    )


def detect_ma_crossover_short(ind: dict, i: int, candles: Sequence[Candle]) -> dict | None:
    if i < 55:
        return None
    ema9, ema21, ema50 = ind["ema9"], ind["ema21"], ind["ema50"]
    if any(is_nan(x[i]) for x in (ema9, ema21, ema50)) or is_nan(ind["adx"]["adx"][i]):
        return None
    if not (ema9[i - 1] >= ema21[i - 1] and ema9[i] < ema21[i]):
        return None
    if candles[i].close >= ema50[i] or ind["adx"]["adx"][i] < 20.0:
        return None
    entry = candles[i].close
    stop = max(c.high for c in candles[max(0, i - 6) : i + 1]) + 0.5 * ind["atr14"][i]
    return _signal(
        "ma_crossover_short",
        candles,
        i,
        -1,
        entry,
        stop,
        entry - 2.0 * (stop - entry),
        ["تقاطع نزولی EMA9 و EMA21", "قیمت زیر EMA50 است", f"ADX = {ind['adx']['adx'][i]:.1f}"],
    )


DETECTORS: dict[str, Callable[[dict, int, Sequence[Candle]], dict | None]] = {
    "trend_pullback_ema20": detect_trend_pullback_ema20,
    "supertrend_flip": detect_supertrend_flip,
    "donchian_breakout": detect_donchian_breakout,
    "macd_trend_pullback": detect_macd_trend_pullback,
    "bollinger_squeeze_breakout": detect_bollinger_squeeze_breakout,
    "rsi_midline_cross": detect_rsi_midline_cross,
    "ichimoku_kumo_breakout": detect_ichimoku_kumo_breakout,
    "ma_crossover": detect_ma_crossover,
    "psar_flip": detect_psar_flip,
    "vwap_reclaim": detect_vwap_reclaim,
    "supertrend_flip_short": detect_supertrend_flip_short,
    "donchian_breakdown_short": detect_donchian_breakdown_short,
    "ma_crossover_short": detect_ma_crossover_short,
}


def scan_setups(ind: dict, candles: Sequence[Candle], allow_short: bool = False, only_last: bool = False) -> list[dict]:
    """Run every setup detector over the whole series (or just the last bar)."""
    signals: list[dict] = []
    indices = range(len(candles) - 1, len(candles)) if only_last else range(20, len(candles))
    for i in indices:
        for setup_id, detector in DETECTORS.items():
            if not allow_short and setup_id in SHORT_SETUPS:
                continue
            try:
                signal = detector(ind, i, candles)
            except IndexError:
                signal = None
            if signal is not None:
                signals.append(signal)
    return signals


#: names for setups produced by the strategy engines rather than the indicator
#: library, so the ranking table reads the same everywhere.
EXTRA_SETUP_NAMES = {
    "act_breakout": "ACT - شکست رنج انباشت (خرید)",
    "act_breakdown": "ACT - شکست رنج توزیع (فروش)",
    "rtm_rbr": "RTM - ناحیه تقاضای RBR",
    "rtm_dbr": "RTM - ناحیه تقاضای DBR",
    "rtm_dbd": "RTM - ناحیه عرضه DBD",
    "rtm_rbd": "RTM - ناحیه عرضه RBD",
    "elliott_wave": "الیوت - شمارش موج",
}


def setup_name(setup_id: str) -> str:
    return SETUP_LIBRARY.get(setup_id, {}).get("name") or EXTRA_SETUP_NAMES.get(setup_id) or setup_id


def setup_ranking(stats: dict[str, dict], min_trades: int = 20) -> list[dict]:
    """Rank setups by measured win rate, refusing to rank on tiny samples."""
    ranked = []
    for setup_id, metric in stats.items():
        trades = metric.get("trades", 0)
        ranked.append(
            {
                "setup": setup_id,
                "name": setup_name(setup_id),
                "trades": trades,
                "win_rate": metric.get("win_rate"),
                "expectancy_r": metric.get("expectancy_r"),
                "profit_factor": metric.get("profit_factor"),
                "max_drawdown_r": metric.get("max_drawdown_r"),
                "avg_bars": metric.get("avg_bars"),
                "sample_ok": trades >= min_trades,
                "grade": _grade(metric, trades, min_trades),
            }
        )
    ranked.sort(key=lambda r: (-(r["win_rate"] or 0), -(r["expectancy_r"] or -99)))
    return ranked


def _grade(metric: dict, trades: int, min_trades: int) -> str:
    if trades < min_trades:
        return "نمونه کم"
    win_rate = metric.get("win_rate") or 0.0
    expectancy = metric.get("expectancy_r") or 0.0
    if win_rate >= 0.7 and expectancy >= 0.4:
        return "A"
    if win_rate >= 0.6 and expectancy >= 0.2:
        return "B"
    if win_rate >= 0.5 and expectancy >= 0.0:
        return "C"
    return "D"


__all__ = [
    "DETECTORS",
    "EXTRA_SETUP_NAMES",
    "SETUP_LIBRARY",
    "SHORT_SETUPS",
    "scan_setups",
    "setup_name",
    "setup_ranking",
]
