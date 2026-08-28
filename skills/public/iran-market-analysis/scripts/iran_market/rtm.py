"""RTM (Read The Market) engine: supply/demand zones, FTR, quarts, MPL, compression.

Implemented rule set (the parts of RTM that are mechanical enough to code and
back-test; the discretionary parts are *not* faked):

* **Swings** - fractal highs/lows with a configurable strength.
* **Zone types** - the four base formations, classified by what happened before
  and after the base:

  =========  =================================  =========
  Type       Sequence                            Meaning
  =========  =================================  =========
  ``RBR``    Rally -> Base -> Rally              demand (continuation)
  ``DBR``    Drop  -> Base -> Rally              demand (reversal)
  ``DBD``    Drop  -> Base -> Drop               supply (continuation)
  ``RBD``    Rally -> Base -> Drop               supply (reversal)
  =========  =================================  =========

* **Base** - the tight candle(s) immediately before the impulsive departure.
  A base whose range is a small fraction of ATR is a *stronger* zone.
* **FTR** (Failure To Return) - the departure broke the previous swing, i.e.
  price left and did not come back; the flag zone is the strongest kind.
* **Quarts / EQ** - the zone is split into Q1..Q4 with the equilibrium at 50%;
  the *golden zone* is the 0.705 retracement of the zone depth.
* **MPL** (Maximum Pain Level) - the extreme of the base: the level where the
  trapped traders feel the most pain, and the natural stop reference.
* **Compression** - a run of shrinking candles into the zone (energy storage).
* **Mitigation** - a zone is touched when price re-enters it and broken when a
  close passes through it; broken zones are excluded from new signals.
* **Signals** - price within the proximity band of a live zone, entry at the
  golden zone, stop beyond the MPL, target at the next opposing zone or swing,
  with the risk/reward computed and rejected when it is below the threshold.
"""

from __future__ import annotations

from collections.abc import Sequence

from .core import NAN, Candle, col, is_nan, last_valid
from .indicators import atr
from .patterns import swing_points

ZONE_TYPES = {
    "RBR": "رالی-بیس-رالی (تقاضای ادامه‌دار)",
    "DBR": "افت-بیس-رالی (تقاضای برگشتی)",
    "DBD": "افت-بیس-افت (عرضه ادامه‌دار)",
    "RBD": "رالی-بیس-افت (عرضه برگشتی)",
}


def _base_candles(candles: Sequence[Candle], anchor: int, direction_out: int, atr_values: Sequence[float], max_base: int = 4, tightness: float = 1.1) -> list[int]:
    """Indices of the base candles just before the departure at ``anchor``.

    Walking backwards from the anchor, a candle belongs to the base while its
    range stays under ``tightness`` x ATR.  At least one candle is always taken.
    """
    indices = [anchor]
    unit = atr_values[anchor] if not is_nan(atr_values[anchor]) else NAN
    if is_nan(unit) or unit <= 0:
        return indices
    for i in range(anchor - 1, max(anchor - max_base, -1), -1):
        candle = candles[i]
        if candle.range <= tightness * unit:
            indices.append(i)
        else:
            break
    return sorted(indices)


def _compression(candles: Sequence[Candle], start: int, end: int, lookback: int = 4) -> bool:
    """Shrinking ranges into the base (RTM compression)."""
    first = max(0, start - lookback)
    window = candles[first : end + 1]
    if len(window) < 3:
        return False
    ranges = [c.range for c in window]
    shrink = sum(1 for i in range(1, len(ranges)) if ranges[i] <= ranges[i - 1])
    return shrink >= len(ranges) - 1 and ranges[-1] < max(ranges) * 0.75


def _classify(prev_direction: int, zone_kind: str) -> str:
    """Map (leg into the base, zone kind) onto RBR/DBR/DBD/RBD."""
    if zone_kind == "demand":
        return "RBR" if prev_direction > 0 else "DBR"
    return "DBD" if prev_direction < 0 else "RBD"


def build_zones(
    candles: Sequence[Candle],
    swings: list[dict] | None = None,
    strength: int = 2,
    atr_period: int = 14,
    max_base: int = 4,
    departure_min_atr: float = 1.2,
) -> list[dict]:
    """All supply/demand zones found in the series, oldest first."""
    if len(candles) < 2 * strength + 6:
        return []
    high = col(candles, "high")
    low = col(candles, "low")
    close = col(candles, "close")
    atr_values = atr(high, low, close, atr_period)
    swings = swings if swings is not None else swing_points(high, low, strength)

    zones: list[dict] = []
    for position, swing in enumerate(swings):
        index = swing["index"]
        kind = "supply" if swing["kind"] == "high" else "demand"
        # the base sits on the far side of the swing: before a high, after a low
        base = _base_candles(candles, index, -1 if kind == "supply" else 1, atr_values, max_base)
        if not base:
            continue
        base_start, base_end = base[0], base[-1]
        zone_high = max(candles[i].high for i in base)
        zone_low = min(candles[i].low for i in base)
        # widen to the origin candle's wick (RTM draws the flag from the origin)
        origin = base_start - 1
        if origin >= 0:
            zone_high = max(zone_high, candles[origin].high)
            zone_low = min(zone_low, candles[origin].low)
        depth = zone_high - zone_low
        if depth <= 0:
            continue

        prev_direction = 0
        if position > 0:
            previous = swings[position - 1]
            prev_direction = 1 if swing["price"] > previous["price"] else -1
        zone_type = _classify(prev_direction, kind)

        # --- departure: how violently price left the zone ---
        departure = 0.0
        departure_bars = 0
        extreme_after = swing["price"]
        for i in range(base_end + 1, min(base_end + 12, len(candles))):
            if kind == "supply":
                move = zone_low - low[i]
                extreme_after = min(extreme_after, low[i])
            else:
                move = high[i] - zone_high
                extreme_after = max(extreme_after, high[i])
            if move > departure:
                departure = move
                departure_bars = i - base_end
        unit = atr_values[base_end] if not is_nan(atr_values[base_end]) else depth
        if unit and departure < departure_min_atr * unit:
            continue  # not an impulsive departure -> not a tradeable zone

        # --- FTR: did the departure break the previous swing? ---
        ftr = False
        if position > 0:
            previous = swings[position - 1]
            if kind == "supply":
                ftr = extreme_after < previous["price"]
            else:
                ftr = extreme_after > previous["price"]

        # --- mitigation after creation ---
        # A zone is *touched* when price re-enters it and *broken* only when a
        # close passes through the far edge: a demand zone dies below its low, a
        # supply zone dies above its high.  Getting this backwards marks every
        # zone as used up the moment the departure leg runs.
        touches = 0
        mitigated = False
        mitigation_index = None
        for i in range(base_end + 1, len(candles)):
            candle = candles[i]
            if kind == "supply":
                if candle.high >= zone_low:
                    touches += 1
                if candle.close > zone_high:
                    mitigated, mitigation_index = True, i
                    break
            else:
                if candle.low <= zone_high:
                    touches += 1
                if candle.close < zone_low:
                    mitigated, mitigation_index = True, i
                    break

        eq = (zone_high + zone_low) / 2.0
        quart = depth / 4.0
        quarts = (
            {
                "Q1": zone_high - quart,
                "Q2": eq,
                "Q3": zone_high - 3 * quart,
                "Q4": zone_low,
            }
            if kind == "supply"
            else {
                "Q1": zone_low + quart,
                "Q2": eq,
                "Q3": zone_low + 3 * quart,
                "Q4": zone_high,
            }
        )
        mpl = zone_high if kind == "supply" else zone_low
        golden = zone_high - 0.705 * depth if kind == "supply" else zone_low + 0.705 * depth

        tightness = depth / unit if unit else 1.0
        volume_in_base = sum(candles[i].volume for i in base) / max(len(base), 1)
        avg_volume = sum(c.volume for c in candles[max(0, base_start - 20) : base_start]) / 20.0 if base_start >= 20 else volume_in_base
        strength_score = 0.0
        strength_score += 0.30 * min(1.0, departure / (3.0 * unit)) if unit else 0.0
        strength_score += 0.20 * max(0.0, 1.0 - tightness)
        strength_score += 0.20 if ftr else 0.0
        strength_score += 0.15 * max(0.0, 1.0 - touches / 3.0)
        strength_score += 0.15 * (1.0 if _compression(candles, base_start, base_end) else 0.35)

        zones.append(
            {
                "id": f"{kind}-{base_start}",
                "kind": kind,
                "kind_fa": "عرضه" if kind == "supply" else "تقاضا",
                "type": zone_type,
                "type_fa": ZONE_TYPES[zone_type],
                "ftr": ftr,
                "compression": _compression(candles, base_start, base_end),
                "origin_index": max(origin, 0),
                "start_index": base_start,
                "end_index": base_end,
                "created_dt": candles[base_end].dt.isoformat(),
                "high": round(zone_high, 6),
                "low": round(zone_low, 6),
                "depth": round(depth, 6),
                "eq": round(eq, 6),
                "quarts": {k: round(v, 6) for k, v in quarts.items()},
                "mpl": round(mpl, 6),
                "golden": round(golden, 6),
                "departure": round(departure, 6),
                "departure_atr": round(departure / unit, 2) if unit else None,
                "departure_bars": departure_bars,
                "target_at_creation": round(extreme_after, 6),
                "touches": touches,
                "mitigated": mitigated,
                "mitigation_index": mitigation_index,
                "status": "mitigated" if mitigated else ("touched" if touches else "fresh"),
                "status_fa": "شکسته‌شده" if mitigated else ("برخورد کرده" if touches else "دست‌نخورده"),
                "strength": round(min(max(strength_score, 0.0), 1.0), 3),
                "volume_ratio": round(volume_in_base / avg_volume, 2) if avg_volume else None,
                "age_bars": len(candles) - 1 - base_end,
            }
        )
    return zones


def active_zones(zones: Sequence[dict], kinds: Sequence[str] = ("supply", "demand"), max_age: int | None = None) -> list[dict]:
    """Zones that are still tradeable: not broken, and optionally not too old."""
    out = []
    for zone in zones:
        if zone["mitigated"] or zone["kind"] not in kinds:
            continue
        if max_age is not None and zone["age_bars"] > max_age:
            continue
        out.append(zone)
    return sorted(out, key=lambda z: (-z["strength"], z["age_bars"]))


def nearest_zone(zones: Sequence[dict], price: float, kind: str) -> dict | None:
    """Closest live zone of ``kind`` relative to ``price`` (demand below, supply above)."""
    candidates = [z for z in zones if z["kind"] == kind and not z["mitigated"]]
    if kind == "demand":
        candidates = [z for z in candidates if z["high"] <= price * 1.02]
        candidates.sort(key=lambda z: price - z["high"])
    else:
        candidates = [z for z in candidates if z["low"] >= price * 0.98]
        candidates.sort(key=lambda z: z["low"] - price)
    return candidates[0] if candidates else None


#: RTM bases are meant to be tight.  A "zone" ten ATRs deep is a range, not a
#: zone, and an entry 70% inside it is unreachable within any sane horizon.
MAX_ZONE_DEPTH_ATR = 6.0
MAX_ENTRY_DISTANCE_ATR = 3.0


def _zone_plan(zone: dict, price: float, atr_value: float, zones: Sequence[dict], min_rr: float) -> dict | None:
    """Entry / stop / target / R:R for one zone at one price (shared by both scanners)."""
    direction = 1 if zone["kind"] == "demand" else -1
    if atr_value and zone["depth"] > MAX_ZONE_DEPTH_ATR * atr_value:
        return None
    entry = zone["golden"]
    if atr_value and abs(entry - price) > MAX_ENTRY_DISTANCE_ATR * atr_value:
        return None
    if direction > 0:
        stop = zone["mpl"] - 0.5 * atr_value
        if stop >= entry:
            stop = entry - max(zone["depth"], atr_value)
    else:
        stop = zone["mpl"] + 0.5 * atr_value
        if stop <= entry:
            stop = entry + max(zone["depth"], atr_value)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    opposing = nearest_zone(zones, price, "supply" if direction > 0 else "demand")
    target = opposing["low"] if (opposing and direction > 0) else (opposing["high"] if opposing else None)
    if target is None:
        target = zone["target_at_creation"]
    if target is None:
        return None
    rr = abs(target - entry) / risk
    if rr < min_rr:
        return None
    return {"direction": direction, "entry": entry, "stop": stop, "target": target, "rr": rr, "risk": risk}


def _zone_reasons(zone: dict) -> list[str]:
    reasons = [
        f"قیمت در محدوده ناحیه {zone['kind_fa']} {zone['type']} قرار دارد (قدرت {zone['strength']:.2f})",
        f"حرکت خروجی از ناحیه {zone['departure_atr']:.1f} برابر ATR بوده است",
    ]
    if zone["ftr"]:
        reasons.append("الگوی FTR تأیید شده (شکست سوئینگ قبلی و عدم بازگشت)")
    if zone["compression"]:
        reasons.append("فشردگی کندل‌ها قبل از ناحیه دیده می‌شود")
    if zone["touches"] == 0:
        reasons.append("ناحیه هنوز دست‌نخورده است")
    if zone["volume_ratio"] is not None:
        reasons.append(f"نسبت حجم داخل بیس به میانگین: {zone['volume_ratio']:.2f}")
    return reasons


def zone_signals(
    candles: Sequence[Candle],
    zones: Sequence[dict],
    proximity_atr: float = 1.5,
    atr_period: int = 14,
    min_strength: float = 0.35,
    min_rr: float = 1.2,
    allow_short: bool = False,
    max_per_kind: int = 2,
) -> list[dict]:
    """All RTM buy/sell signals on the last bar.

    A signal is emitted when price is inside or within ``proximity_atr`` x ATR of
    a live zone whose strength clears ``min_strength``; entry sits in the golden
    zone, the stop is beyond the MPL, and the target is the nearest opposing zone
    (or the zone's original departure target).  Signals whose risk/reward is
    below ``min_rr`` are dropped - RTM's own rule is that a zone without room to
    the next zone is not a trade.
    """
    if not candles:
        return []
    last = candles[-1]
    price = last.close
    high = col(candles, "high")
    low = col(candles, "low")
    close = col(candles, "close")
    atr_value = last_valid(atr(high, low, close, atr_period))
    if is_nan(atr_value) or atr_value <= 0:
        atr_value = max(price * 0.01, 1e-9)

    signals: list[dict] = []
    live = active_zones(zones)

    for zone in live:
        if zone["strength"] < min_strength:
            continue
        if zone["kind"] == "demand" and zone["high"] > price + proximity_atr * atr_value:
            continue
        if zone["kind"] == "supply" and zone["low"] < price - proximity_atr * atr_value:
            continue
        if zone["kind"] == "supply" and not allow_short:
            continue

        plan = _zone_plan(zone, price, atr_value, live, min_rr)
        if plan is None:
            continue
        reasons = _zone_reasons(zone)
        signals.append(
            {
                "engine": "rtm",
                "type": f"rtm_{zone['kind']}",
                "setup": f"rtm_{zone['type'].lower()}",
                "setup_name": f"RTM {zone['type']}",
                "direction": plan["direction"],
                "title": f"{'خرید' if plan['direction'] > 0 else 'فروش'} در ناحیه {zone['type']}",
                "reason": "؛ ".join(reasons),
                "reasons": reasons,
                "confidence": round(min(0.95, 0.45 + 0.5 * zone["strength"]), 3),
                "entry": round(plan["entry"], 6),
                "entry_zone": [round(min(plan["entry"], zone["eq"]), 6), round(max(plan["entry"], zone["eq"]), 6)],
                "stop": round(plan["stop"], 6),
                "target": round(plan["target"], 6),
                "rr": round(plan["rr"], 2),
                "risk": round(plan["risk"], 6),
                "entry_kind": "limit",
                "zone_id": zone["id"],
                "inside_zone": zone["low"] <= price <= zone["high"],
                "invalidation": round(plan["stop"], 6),
                "index": len(candles) - 1,
                "dt": last.dt.isoformat(),
            }
        )
    signals.sort(key=lambda s: (-s["confidence"], -s["rr"]))
    # the dashboard shows the best candidates per side, not every zone in range
    best: list[dict] = []
    per_kind: dict[str, int] = {}
    for signal in signals:
        kind = signal["type"]
        if per_kind.get(kind, 0) >= max_per_kind:
            continue
        per_kind[kind] = per_kind.get(kind, 0) + 1
        best.append(signal)
    return best


def historical_zone_signals(
    candles: Sequence[Candle],
    zones: Sequence[dict],
    proximity_atr: float = 1.5,
    atr_period: int = 14,
    min_strength: float = 0.35,
    min_rr: float = 1.2,
    allow_short: bool = False,
    cooldown_bars: int = 12,
    ind: dict | None = None,
) -> list[dict]:
    """RTM signals over the whole history, so the win rate can be measured.

    Causal by construction: at bar ``i`` only zones created before ``i`` and not
    yet broken at ``i`` are considered, and the plan is built from the zone's own
    creation data plus the price at ``i``.  The bar loop is outer and the zone
    list is built once per bar, so the scan is O(bars x zones).
    """
    if not candles:
        return []
    atr_values = (ind or {}).get("atr14") or atr(col(candles, "high"), col(candles, "low"), col(candles, "close"), atr_period)
    close = (ind or {}).get("close") or col(candles, "close")
    candidates = [z for z in zones if z["strength"] >= min_strength and (allow_short or z["kind"] == "demand")]
    signals: list[dict] = []
    last_signal: dict[str, int] = {}
    for i in range(1, len(candles)):
        unit = atr_values[i] if i < len(atr_values) else NAN
        if is_nan(unit) or unit <= 0:
            continue
        price = close[i]
        live_then = [
            z for z in candidates if z["end_index"] < i and not (z["mitigated"] and (z["mitigation_index"] or 0) <= i)
        ]
        if not live_then:
            continue
        for zone in live_then:
            if i - last_signal.get(zone["id"], -10**9) < cooldown_bars:
                continue
            if zone["kind"] == "demand" and zone["high"] > price + proximity_atr * unit:
                continue
            if zone["kind"] == "supply" and zone["low"] < price - proximity_atr * unit:
                continue
            plan = _zone_plan(zone, price, unit, live_then, min_rr)
            if plan is None:
                continue
            reasons = _zone_reasons(zone)
            signals.append(
                {
                    "engine": "rtm",
                    "type": f"rtm_{zone['kind']}",
                    "setup": f"rtm_{zone['type'].lower()}",
                    "setup_name": f"RTM {zone['type']}",
                    "direction": plan["direction"],
                    "title": f"{'خرید' if plan['direction'] > 0 else 'فروش'} در ناحیه {zone['type']}",
                    "reason": "؛ ".join(reasons),
                    "reasons": reasons,
                    "confidence": round(min(0.95, 0.45 + 0.5 * zone["strength"]), 3),
                    "entry": round(plan["entry"], 6),
                    "stop": round(plan["stop"], 6),
                    "target": round(plan["target"], 6),
                    "rr": round(plan["rr"], 2),
                    "risk": round(plan["risk"], 6),
                    "entry_kind": "limit",
                    "zone_id": zone["id"],
                    "invalidation": round(plan["stop"], 6),
                    "index": i,
                    "dt": candles[i].dt.isoformat(),
                }
            )
            last_signal[zone["id"]] = i
    return signals


def zone_overlay(zones: Sequence[dict], candles: Sequence[Candle], only_active: bool = True) -> list[dict]:
    """Chart-ready rectangles (Plotly shapes) for the dashboard."""
    if not candles:
        return []
    end_x = candles[-1].dt.isoformat()
    out = []
    for zone in zones:
        if only_active and zone["mitigated"]:
            continue
        start_x = candles[min(zone["start_index"], len(candles) - 1)].dt.isoformat()
        out.append(
            {
                "x0": start_x,
                "x1": end_x,
                "y0": zone["low"],
                "y1": zone["high"],
                "kind": zone["kind"],
                "type": zone["type"],
                "strength": zone["strength"],
                "eq": zone["eq"],
                "mpl": zone["mpl"],
                "golden": zone["golden"],
                "quarts": zone["quarts"],
                "status": zone["status"],
                "ftr": zone["ftr"],
            }
        )
    return out


def rtm_summary(zones: Sequence[dict], price: float) -> dict:
    """Compact state block: where the nearest live zones sit relative to price."""
    live = active_zones(zones)
    demand = nearest_zone(live, price, "demand")
    supply = nearest_zone(live, price, "supply")
    return {
        "zones_total": len(zones),
        "zones_live": len(live),
        "zones_mitigated": sum(1 for z in zones if z["mitigated"]),
        "nearest_demand": {"id": demand["id"], "high": demand["high"], "low": demand["low"], "strength": demand["strength"], "distance_pct": round(100.0 * (price - demand["high"]) / price, 2) if price else None} if demand else None,
        "nearest_supply": {"id": supply["id"], "high": supply["high"], "low": supply["low"], "strength": supply["strength"], "distance_pct": round(100.0 * (supply["low"] - price) / price, 2) if price else None} if supply else None,
    }


__all__ = [
    "ZONE_TYPES",
    "active_zones",
    "build_zones",
    "historical_zone_signals",
    "nearest_zone",
    "rtm_summary",
    "zone_overlay",
    "zone_signals",
]
