"""ICT (Inner Circle Trader) engine: order blocks, FVG, liquidity sweeps, OTE.

The mechanical, back-testable core of ICT:

* **Order Block (OB)** - the last opposite-colour candle before a displacement
  move that breaks structure.  Its high/low is the re-entry zone.
* **FVG / imbalance** - a three-candle gap where candle *i*'s low is above
  candle *i-2*'s high (bullish) or the mirror (bearish).
* **Liquidity sweep** - a swing high/low taken out intrabar but closed back
  inside: the stop-hunt that precedes many reversals.
* **BOS / CHoCH** - break of structure / change of character from
  :func:`~iran_market.patterns.market_structure`.
* **Premium / Discount** - the dealing range between the last major swing high
  and low; below equilibrium is discount (look for longs), above is premium
  (look for shorts), and the 62-79% retracement is the OTE zone.
"""

from __future__ import annotations

from collections.abc import Sequence

from .core import Candle, col, is_nan, last_valid
from .indicators import atr
from .patterns import market_structure, swing_points


def fair_value_gaps(candles: Sequence[Candle], max_age: int = 40) -> list[dict]:
    """Unfilled fair value gaps (three-candle imbalances)."""
    gaps: list[dict] = []
    n = len(candles)
    for i in range(2, n):
        first, third = candles[i - 2], candles[i]
        if third.low > first.high:
            gaps.append(
                {
                    "kind": "bullish",
                    "kind_fa": "شکاف قیمتی صعودی",
                    "index": i,
                    "dt": candles[i].dt.isoformat(),
                    "high": round(third.low, 6),
                    "low": round(first.high, 6),
                    "size": round(third.low - first.high, 6),
                }
            )
        elif third.high < first.low:
            gaps.append(
                {
                    "kind": "bearish",
                    "kind_fa": "شکاف قیمتی نزولی",
                    "index": i,
                    "dt": candles[i].dt.isoformat(),
                    "high": round(first.low, 6),
                    "low": round(third.high, 6),
                    "size": round(first.low - third.high, 6),
                }
            )
    # mark gaps that price has traded back through
    for gap in gaps:
        filled_index = None
        for i in range(gap["index"] + 1, n):
            if gap["kind"] == "bullish" and candles[i].low <= gap["low"]:
                filled_index = i
                break
            if gap["kind"] == "bearish" and candles[i].high >= gap["high"]:
                filled_index = i
                break
        gap["filled"] = filled_index is not None
        gap["filled_index"] = filled_index
        gap["age_bars"] = n - 1 - gap["index"]
        gap["live"] = not gap["filled"] and gap["age_bars"] <= max_age
    return gaps


def order_blocks(candles: Sequence[Candle], structure: dict | None = None, strength: int = 2, lookback: int = 60) -> list[dict]:
    """Order blocks anchored to structure-breaking displacement moves."""
    if len(candles) < 10:
        return []
    structure = structure or market_structure(candles, strength=strength)
    high = col(candles, "high")
    low = col(candles, "low")
    atr_values = atr(high, low, col(candles, "close"), 14)
    blocks: list[dict] = []
    for event in structure["events"]:
        index = event["index"]
        if index >= len(candles) or event["index"] < 2:
            continue
        direction = event["direction"]
        # displacement = the move that caused the break, measured in ATRs
        start = max(0, index - 4)
        close = col(candles, "close")
        displacement = (close[index] - close[start]) if direction > 0 else (close[start] - close[index])
        unit = last_valid(atr_values[max(0, index - 6) : index + 1])
        if is_nan(unit) or unit <= 0 or displacement < 1.5 * unit:
            continue
        # the OB is the last opposite-colour candle before the displacement
        ob_index = None
        for i in range(index, start - 1, -1):
            candle = candles[i]
            if direction > 0 and candle.is_bearish:
                ob_index = i
                break
            if direction < 0 and candle.is_bullish:
                ob_index = i
                break
        if ob_index is None:
            continue
        ob = candles[ob_index]
        broken = False
        for i in range(ob_index + 1, len(candles)):
            if direction > 0 and candles[i].low <= ob.low:
                broken = True
                break
            if direction < 0 and candles[i].high >= ob.high:
                broken = True
                break
        blocks.append(
            {
                "kind": "bullish" if direction > 0 else "bearish",
                "kind_fa": "اوردر بلاک صعودی" if direction > 0 else "اوردر بلاک نزولی",
                "index": ob_index,
                "dt": ob.dt.isoformat(),
                "high": round(ob.high, 6),
                "low": round(ob.low, 6),
                "eq": round((ob.high + ob.low) / 2.0, 6),
                "event": event["type"],
                "level": event["level"],
                "displacement_atr": round(displacement / unit, 2),
                "mitigated": broken,
                "age_bars": len(candles) - 1 - ob_index,
                "live": not broken and len(candles) - 1 - ob_index <= lookback,
            }
        )
    return blocks


def liquidity_sweeps(candles: Sequence[Candle], strength: int = 2, lookback: int = 40) -> list[dict]:
    """Swing highs/lows taken out intrabar and closed back inside (stop hunts)."""
    if len(candles) < 2 * strength + 3:
        return []
    high = col(candles, "high")
    low = col(candles, "low")
    swings = swing_points(high, low, strength)
    sweeps: list[dict] = []
    for swing in swings:
        index = swing["index"]
        for i in range(index + 1, min(index + 15, len(candles))):
            candle = candles[i]
            if swing["kind"] == "high" and candle.high > swing["price"] and candle.close < swing["price"]:
                sweeps.append(
                    {
                        "kind": "buy_side",
                        "kind_fa": "جمع‌آوری نقدشوندگی بالای بازار",
                        "index": i,
                        "dt": candle.dt.isoformat(),
                        "level": round(swing["price"], 6),
                        "wick": round(candle.high - swing["price"], 6),
                        "swing_index": index,
                        "direction": -1,  # a buy-side sweep usually precedes a drop
                        "age_bars": len(candles) - 1 - i,
                        "recent": len(candles) - 1 - i <= lookback,
                    }
                )
                break
            if swing["kind"] == "low" and candle.low < swing["price"] and candle.close > swing["price"]:
                sweeps.append(
                    {
                        "kind": "sell_side",
                        "kind_fa": "جمع‌آوری نقدشوندگی پایین بازار",
                        "index": i,
                        "dt": candle.dt.isoformat(),
                        "level": round(swing["price"], 6),
                        "wick": round(swing["price"] - candle.low, 6),
                        "swing_index": index,
                        "direction": 1,
                        "age_bars": len(candles) - 1 - i,
                        "recent": len(candles) - 1 - i <= lookback,
                    }
                )
                break
    return sweeps


def dealing_range(candles: Sequence[Candle], swings: list[dict] | None = None, strength: int = 2) -> dict:
    """Premium/discount array of the current dealing range."""
    high = col(candles, "high")
    low = col(candles, "low")
    if swings is None:
        swings = swing_points(high, low, strength)
    highs = [s for s in swings if s["kind"] == "high"]
    lows = [s for s in swings if s["kind"] == "low"]
    if not highs or not lows:
        return {}
    range_high = max(s["price"] for s in highs[-2:])
    range_low = min(s["price"] for s in lows[-2:])
    depth = range_high - range_low
    if depth <= 0:
        return {}
    price = candles[-1].close
    position = (price - range_low) / depth
    return {
        "high": round(range_high, 6),
        "low": round(range_low, 6),
        "equilibrium": round((range_high + range_low) / 2.0, 6),
        "ote_low": round(range_high - 0.79 * depth, 6),
        "ote_high": round(range_high - 0.62 * depth, 6),
        "position": round(position, 3),
        "zone": "discount" if position < 0.5 else "premium",
        "zone_fa": "منطقه تخفیف (مناسب خرید)" if position < 0.5 else "منطقه گران (مناسب فروش)",
        "in_ote": round(range_high - 0.79 * depth, 6) <= price <= round(range_high - 0.62 * depth, 6),
    }


def ict_signals(
    candles: Sequence[Candle],
    strength: int = 2,
    atr_period: int = 14,
    min_rr: float = 1.5,
    allow_short: bool = False,
) -> list[dict]:
    """Confluence-based ICT entries on the latest bar.

    A long needs at least two of: a recent sell-side sweep, a bullish CHoCH/BOS,
    price in discount, and a live bullish OB or FVG underneath.  Shorts mirror it
    and are only produced when the venue allows selling first.
    """
    if len(candles) < 30:
        return []
    structure = market_structure(candles, strength=strength)
    gaps = fair_value_gaps(candles)
    blocks = order_blocks(candles, structure)
    sweeps = liquidity_sweeps(candles, strength=strength)
    dealing = dealing_range(candles, strength=strength)
    close = col(candles, "close")
    high = col(candles, "high")
    low = col(candles, "low")
    atr_value = last_valid(atr(high, low, close, atr_period))
    price = candles[-1].close
    if is_nan(atr_value) or atr_value <= 0:
        atr_value = max(price * 0.01, 1e-9)

    signals: list[dict] = []

    def build(direction: int) -> dict | None:
        if direction > 0:
            live_gaps = [g for g in gaps if g["live"] and g["kind"] == "bullish" and g["high"] <= price]
            live_blocks = [b for b in blocks if b["live"] and b["kind"] == "bullish" and b["high"] <= price * 1.005]
            recent_sweeps = [s for s in sweeps if s["recent"] and s["kind"] == "sell_side"]
            structure_ok = bool(structure["last_bos"] and structure["last_bos"]["direction"] == 1) or bool(
                structure["last_choch"] and structure["last_choch"]["direction"] == 1
            )
            discount = dealing.get("zone") == "discount" if dealing else False
            anchors = live_blocks or live_gaps
            if not anchors:
                return None
            anchor = max(anchors, key=lambda a: a["index"])
            entry = anchor["eq"] if "eq" in anchor else (anchor["high"] + anchor["low"]) / 2.0
            if entry > price:
                entry = price
            stop = anchor["low"] - 0.5 * atr_value
            target = dealing["high"] if dealing else price + 3 * atr_value
        else:
            live_gaps = [g for g in gaps if g["live"] and g["kind"] == "bearish" and g["low"] >= price]
            live_blocks = [b for b in blocks if b["live"] and b["kind"] == "bearish" and b["low"] >= price * 0.995]
            recent_sweeps = [s for s in sweeps if s["recent"] and s["kind"] == "buy_side"]
            structure_ok = bool(structure["last_bos"] and structure["last_bos"]["direction"] == -1) or bool(
                structure["last_choch"] and structure["last_choch"]["direction"] == -1
            )
            discount = dealing.get("zone") == "premium" if dealing else False
            anchors = live_blocks or live_gaps
            if not anchors:
                return None
            anchor = max(anchors, key=lambda a: a["index"])
            entry = anchor["eq"] if "eq" in anchor else (anchor["high"] + anchor["low"]) / 2.0
            if entry < price:
                entry = price
            stop = anchor["high"] + 0.5 * atr_value
            target = dealing["low"] if dealing else price - 3 * atr_value

        risk = abs(entry - stop)
        if risk <= 0:
            return None
        reward = abs(target - entry)
        rr = reward / risk
        confluence = [
            ("جمع‌آوری نقدشوندگی اخیر", bool(recent_sweeps)),
            ("شکست ساختار هم‌جهت", structure_ok),
            ("قیمت در منطقه مناسب (تخفیف/گران)", discount),
            ("اوردر بلاک یا FVG زنده", True),
        ]
        score = sum(1 for _, ok in confluence if ok)
        if score < 2 or rr < min_rr:
            return None
        reasons = [name for name, ok in confluence if ok]
        reasons.append(f"نسبت ریسک به ریوارد {rr:.2f} با ورود در {'اوردر بلاک' if 'eq' in anchor else 'شکاف قیمتی'}")
        return {
            "engine": "ict",
            "type": "ict_long" if direction > 0 else "ict_short",
            "setup": "ICT OB/FVG",
            "direction": direction,
            "title": f"{'خرید' if direction > 0 else 'فروش'} ICT - {'اوردر بلاک' if 'eq' in anchor else 'FVG'}",
            "reason": "؛ ".join(reasons),
            "reasons": reasons,
            "confluence": score,
            "confidence": round(min(0.9, 0.35 + 0.15 * score), 3),
            "entry": round(entry, 6),
            "stop": round(stop, 6),
            "target": round(target, 6),
            "rr": round(rr, 2),
            "risk": round(risk, 6),
            "anchor_index": anchor["index"],
            "invalidation": round(stop, 6),
            "index": len(candles) - 1,
            "dt": candles[-1].dt.isoformat(),
        }

    long_signal = build(1)
    if long_signal:
        signals.append(long_signal)
    if allow_short:
        short_signal = build(-1)
        if short_signal:
            signals.append(short_signal)
    return signals


def ict_overlay(candles: Sequence[Candle], strength: int = 2) -> dict:
    """Chart payload: OB rectangles, FVG rectangles, sweep markers."""
    if not candles:
        return {"order_blocks": [], "fvg": [], "sweeps": []}
    last_x = candles[-1].dt.isoformat()
    blocks = order_blocks(candles, strength=strength)
    gaps = fair_value_gaps(candles)
    sweeps = liquidity_sweeps(candles, strength=strength)
    return {
        "order_blocks": [
            {
                "x0": candles[min(b["index"], len(candles) - 1)].dt.isoformat(),
                "x1": last_x,
                "y0": b["low"],
                "y1": b["high"],
                "kind": b["kind"],
                "live": b["live"],
                "label": b["kind_fa"],
            }
            for b in blocks
            if b["live"]
        ],
        "fvg": [
            {
                "x0": candles[min(g["index"], len(candles) - 1)].dt.isoformat(),
                "x1": last_x,
                "y0": g["low"],
                "y1": g["high"],
                "kind": g["kind"],
                "live": g["live"],
                "label": g["kind_fa"],
            }
            for g in gaps
            if g["live"]
        ],
        "sweeps": [
            {
                "x": candles[min(s["index"], len(candles) - 1)].dt.isoformat(),
                "y": s["level"],
                "kind": s["kind"],
                "direction": s["direction"],
                "label": s["kind_fa"],
            }
            for s in sweeps
            if s["recent"]
        ],
        "dealing_range": dealing_range(candles, strength=strength),
    }


__all__ = ["dealing_range", "fair_value_gaps", "ict_overlay", "ict_signals", "liquidity_sweeps", "order_blocks"]
