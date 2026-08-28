"""Candlestick patterns and market-structure events.

Every pattern returns a *strength* in ``0..1`` derived from the candle geometry
relative to the recent average true range, not a fixed 1/0 flag: a hammer whose
lower shadow is 1.2x ATR is not the same signal as one with a 4x ATR shadow, and
the confluence scorer downstream needs that difference.
"""

from __future__ import annotations

from collections.abc import Sequence

from .core import NAN, Candle, col, is_nan
from .indicators import atr

#: direction: +1 bullish, -1 bearish
PATTERNS: dict[str, dict[str, object]] = {
    "hammer": {"fa": "چکش", "direction": 1, "reversal": True},
    "inverted_hammer": {"fa": "چکش معکوس", "direction": 1, "reversal": True},
    "bullish_engulfing": {"fa": "پوشای صعودی", "direction": 1, "reversal": True},
    "bearish_engulfing": {"fa": "پوشای نزولی", "direction": -1, "reversal": True},
    "piercing_line": {"fa": "خط نفوذی", "direction": 1, "reversal": True},
    "dark_cloud_cover": {"fa": "ابر سیاه پوششی", "direction": -1, "reversal": True},
    "morning_star": {"fa": "ستاره صبحگاهی", "direction": 1, "reversal": True},
    "evening_star": {"fa": "ستاره شامگاهی", "direction": -1, "reversal": True},
    "three_white_soldiers": {"fa": "سه سرباز سفید", "direction": 1, "reversal": False},
    "three_black_crows": {"fa": "سه کلاغ سیاه", "direction": -1, "reversal": False},
    "bullish_harami": {"fa": "هارامی صعودی", "direction": 1, "reversal": True},
    "bearish_harami": {"fa": "هارامی نزولی", "direction": -1, "reversal": True},
    "tweezer_bottom": {"fa": "انبرک کف", "direction": 1, "reversal": True},
    "tweezer_top": {"fa": "انبرک سقف", "direction": -1, "reversal": True},
    "doji": {"fa": "دوجی", "direction": 0, "reversal": True},
    "marubozu_bull": {"fa": "ماروبوزو صعودی", "direction": 1, "reversal": False},
    "marubozu_bear": {"fa": "ماروبوزو نزولی", "direction": -1, "reversal": False},
    "inside_bar": {"fa": "کندل داخلی", "direction": 0, "reversal": False},
    "outside_bar": {"fa": "کندل بیرونی", "direction": 0, "reversal": False},
}


def detect_patterns(candles: Sequence[Candle], atr_period: int = 14) -> list[dict]:
    """All candlestick patterns found in the series (one record per bar/pattern)."""
    if len(candles) < 4:
        return []
    high = col(candles, "high")
    low = col(candles, "low")
    close = col(candles, "close")
    atr_series = atr(high, low, close, atr_period)
    found: list[dict] = []

    def add(index: int, name: str, strength: float) -> None:
        meta = PATTERNS[name]
        found.append(
            {
                "index": index,
                "dt": candles[index].dt.isoformat(),
                "pattern": name,
                "label": meta["fa"],
                "direction": meta["direction"],
                "reversal": bool(meta["reversal"]),
                "strength": round(min(max(strength, 0.0), 1.0), 3),
                "price": close[index],
            }
        )

    for i in range(2, len(candles)):
        c = candles[i]
        prev = candles[i - 1]
        prev2 = candles[i - 2]
        unit = atr_series[i] if not is_nan(atr_series[i]) else max(c.range, 1e-9)
        if unit <= 0:
            unit = max(c.range, 1e-9)
        body = c.body
        rng = c.range or 1e-9

        # --- single candle ---
        if c.lower_shadow >= 2.0 * body and c.lower_shadow >= 0.6 * rng and c.upper_shadow <= 0.25 * rng:
            add(i, "hammer", min(1.0, c.lower_shadow / (2.0 * unit)))
        if c.upper_shadow >= 2.0 * body and c.upper_shadow >= 0.6 * rng and c.lower_shadow <= 0.25 * rng:
            add(i, "inverted_hammer", min(1.0, c.upper_shadow / (2.0 * unit)))
        if body <= 0.1 * rng and rng > 0:
            add(i, "doji", min(1.0, 0.5 + (0.1 - body / rng)))
        if c.is_bullish and body >= 0.92 * rng:
            add(i, "marubozu_bull", min(1.0, body / unit))
        if c.is_bearish and body >= 0.92 * rng:
            add(i, "marubozu_bear", min(1.0, body / unit))
        if c.high < prev.high and c.low > prev.low:
            add(i, "inside_bar", 0.5)
        if c.high > prev.high and c.low < prev.low:
            add(i, "outside_bar", 0.6)

        # --- two candle ---
        if prev.is_bearish and c.is_bullish and c.close >= prev.open and c.open <= prev.close:
            overlap = (c.close - c.open) / max(prev.body, 1e-9)
            add(i, "bullish_engulfing", min(1.0, 0.5 + overlap / 3.0))
        if prev.is_bullish and c.is_bearish and c.open >= prev.close and c.close <= prev.open:
            overlap = (c.open - c.close) / max(prev.body, 1e-9)
            add(i, "bearish_engulfing", min(1.0, 0.5 + overlap / 3.0))
        if prev.is_bearish and c.is_bullish and c.open < prev.close and c.close > (prev.open + prev.close) / 2 and c.close < prev.open:
            add(i, "piercing_line", 0.6)
        if prev.is_bullish and c.is_bearish and c.open > prev.close and c.close < (prev.open + prev.close) / 2 and c.close > prev.open:
            add(i, "dark_cloud_cover", 0.6)
        if prev.is_bearish and c.is_bullish and prev.body > 0 and c.body < 0.5 * prev.body and c.high < prev.open and c.low > prev.close:
            add(i, "bullish_harami", 0.55)
        if prev.is_bullish and c.is_bearish and prev.body > 0 and c.body < 0.5 * prev.body and c.low > prev.open and c.high < prev.close:
            add(i, "bearish_harami", 0.55)
        tolerance = 0.15 * unit
        if abs(c.low - prev.low) <= tolerance and c.close > c.open:
            add(i, "tweezer_bottom", 0.6)
        if abs(c.high - prev.high) <= tolerance and c.close < c.open:
            add(i, "tweezer_top", 0.6)

        # --- three candle ---
        if (
            prev2.is_bearish
            and prev2.body > 0.5 * (prev2.range or 1e-9)
            and prev.body < 0.4 * prev2.body
            and c.is_bullish
            and c.close > (prev2.open + prev2.close) / 2
        ):
            add(i, "morning_star", 0.75)
        if (
            prev2.is_bullish
            and prev2.body > 0.5 * (prev2.range or 1e-9)
            and prev.body < 0.4 * prev2.body
            and c.is_bearish
            and c.close < (prev2.open + prev2.close) / 2
        ):
            add(i, "evening_star", 0.75)
        if i >= 2 and all(candles[i - k].is_bullish for k in range(3)):
            trio = [candles[i - k] for k in range(2, -1, -1)]
            if trio[0].body < trio[1].body < trio[2].body and trio[2].close > trio[1].close > trio[0].close:
                add(i, "three_white_soldiers", 0.7)
        if i >= 2 and all(candles[i - k].is_bearish for k in range(3)):
            trio = [candles[i - k] for k in range(2, -1, -1)]
            if trio[0].body < trio[1].body < trio[2].body and trio[2].close < trio[1].close < trio[0].close:
                add(i, "three_black_crows", 0.7)
    return found


def patterns_at(candles: Sequence[Candle], index: int | None = None, lookback: int = 2, **kwargs) -> list[dict]:
    """Patterns detected on the last ``lookback`` bars (signal-time view)."""
    target = len(candles) - 1 if index is None else index
    return [p for p in detect_patterns(candles[: target + 1], **kwargs) if p["index"] > target - lookback]


# ---------------------------------------------------------------------------
# market structure (used by RTM and ICT)
# ---------------------------------------------------------------------------


def swing_points(high: Sequence[float], low: Sequence[float], strength: int = 2) -> list[dict]:
    """Confirmed fractal swing points with their kind and index."""
    from .indicators import fractal_swings

    return fractal_swings(high, low, strength)


def market_structure(candles: Sequence[Candle], strength: int = 2, atr_period: int = 14) -> dict:
    """Break-of-structure / change-of-character events.

    * **BOS** (شکست ساختار): price closes beyond the last swing in the direction
      of the current trend - trend continuation.
    * **CHoCH** (تغییر ماهیت): price closes beyond the last swing *against* the
      current trend - the first sign of reversal.
    """
    high = col(candles, "high")
    low = col(candles, "low")
    close = col(candles, "close")
    swings = swing_points(high, low, strength)
    events: list[dict] = []
    trend = 0
    last_high: dict | None = None
    last_low: dict | None = None
    for swing in swings:
        if swing["kind"] == "high":
            last_high = swing
        else:
            last_low = swing
    # replay chronologically
    last_high = None
    last_low = None
    for swing in swings:
        index = swing["index"]
        for i in range(index + 1, min(index + 12, len(candles))):
            if last_high and close[i] > last_high["price"] and trend >= 0:
                kind = "BOS" if trend == 1 else "CHoCH"
                events.append(
                    {
                        "index": i,
                        "dt": candles[i].dt.isoformat(),
                        "type": kind,
                        "direction": 1,
                        "level": last_high["price"],
                        "swing_index": last_high["index"],
                        "close": close[i],
                    }
                )
                trend = 1
                last_high = None
                break
            if last_low and close[i] < last_low["price"] and trend <= 0:
                kind = "BOS" if trend == -1 else "CHoCH"
                events.append(
                    {
                        "index": i,
                        "dt": candles[i].dt.isoformat(),
                        "type": kind,
                        "direction": -1,
                        "level": last_low["price"],
                        "swing_index": last_low["index"],
                        "close": close[i],
                    }
                )
                trend = -1
                last_low = None
                break
        if swing["kind"] == "high":
            last_high = swing
        else:
            last_low = swing
    highs = [s for s in swings if s["kind"] == "high"]
    lows = [s for s in swings if s["kind"] == "low"]
    structure = "flat"
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1]["price"] > highs[-2]["price"]
        hl = lows[-1]["price"] > lows[-2]["price"]
        lh = highs[-1]["price"] < highs[-2]["price"]
        ll = lows[-1]["price"] < lows[-2]["price"]
        if hh and hl:
            structure = "uptrend (HH/HL)"
        elif lh and ll:
            structure = "downtrend (LH/LL)"
        elif hh and ll:
            structure = "expanding range"
        elif lh and hl:
            structure = "contracting range"
    return {
        "trend": trend,
        "structure": structure,
        "events": events,
        "swings": swings,
        "last_bos": next((e for e in reversed(events) if e["type"] == "BOS"), None),
        "last_choch": next((e for e in reversed(events) if e["type"] == "CHoCH"), None),
    }


def support_resistance(candles: Sequence[Candle], strength: int = 2, cluster_pct: float = 1.0) -> list[dict]:
    """Swing-based S/R levels clustered by proximity, ranked by touches."""
    high = col(candles, "high")
    low = col(candles, "low")
    close = col(candles, "close")
    swings = swing_points(high, low, strength)
    prices = sorted(s["price"] for s in swings)
    if not prices:
        return []
    clusters: list[dict] = []
    for price in prices:
        placed = False
        for cluster in clusters:
            if abs(cluster["price"] - price) / max(price, 1e-9) * 100.0 <= cluster_pct:
                cluster["prices"].append(price)
                cluster["price"] = sum(cluster["prices"]) / len(cluster["prices"])
                cluster["touches"] = len(cluster["prices"])
                placed = True
                break
        if not placed:
            clusters.append({"price": price, "prices": [price], "touches": 1})
    last_close = close[-1]
    levels = []
    for cluster in clusters:
        levels.append(
            {
                "price": round(cluster["price"], 4),
                "touches": cluster["touches"],
                "kind": "resistance" if cluster["price"] > last_close else "support",
                "distance_pct": round(100.0 * (cluster["price"] - last_close) / last_close, 3) if last_close else None,
            }
        )
    levels.sort(key=lambda lv: (-lv["touches"], abs(lv["distance_pct"] or 0)))
    return levels[:12]


__all__ = ["NAN", "PATTERNS", "detect_patterns", "market_structure", "patterns_at", "support_resistance", "swing_points"]
