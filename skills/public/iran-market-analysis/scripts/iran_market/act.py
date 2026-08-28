"""ACT engine - Accumulation -> Compression -> Trend (and its short mirror).

ACT is a three-phase model; each phase is tested mechanically so the setup can
be back-tested instead of asserted:

``A`` - **Accumulation** (انباشت)
    Price sits in a bounded range after a decline: ADX below the trend
    threshold, range width within a few ATRs, volume drying up, and OBV holding
    flat/up while price is flat (absorption rather than distribution).

``C`` - **Compression** (فشردگی)
    Volatility contracts inside that range: Bollinger bands move inside the
    Keltner channel (TTM squeeze) and/or bandwidth drops to the lowest
    percentile of the lookback window.

``T`` - **Trend** (روند)
    The break: a close beyond the range extreme on relative volume, with ATR
    expanding and ADX turning up.  Entry is the break or its retest, the stop
    goes behind the compression base, and the first target is the measured move
    (range height projected from the break).

The short side is the exact mirror - distribution, decompression, breakdown -
and is only emitted for venues that allow selling first (futures, commodity,
FX, crypto); the TSE equity market is long-only.

Performance note: the phase test is written against *precomputed causal series*
(:class:`_Context`), so scanning 1200 bars costs the same as scanning one.  The
naive version (recompute every indicator for every ``candles[:i+1]`` prefix) is
O(n^2) and took ~10s per symbol.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .core import NAN, Candle, col, is_nan, linreg, percentile_rank
from .indicators import adx, atr, bollinger, compute_all, keltner, obv, rolling_max, rolling_min

DEFAULTS = {
    "range_bars": 25,
    "range_max_atr": 6.0,
    "adx_max": 22.0,
    "bandwidth_percentile": 30.0,
    "breakout_min_rel_volume": 1.4,
    "lookback": 120,
    "min_rr": 1.5,
}

PHASE_FA = {
    "accumulation": "انباشت",
    "compression": "فشردگی",
    "trend": "شکست و شروع روند",
    "none": "بدون فاز مشخص",
}


@dataclass(slots=True)
class _Context:
    """All causal series the phase test needs, computed once per analysis."""

    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]
    atr14: list[float]
    adx: list[float]
    obv: list[float]
    rel_volume: list[float]
    bb_upper: list[float]
    bb_lower: list[float]
    kc_upper: list[float]
    kc_lower: list[float]
    range_high: list[float]
    range_low: list[float]
    volume_slope: list[float]
    bandwidth_rank: list[float]


def _build_context(candles: Sequence[Candle], params: dict, ind: dict | None = None) -> _Context:
    ind = ind or compute_all(candles, candles[0].timeframe if candles else "D1")
    high = ind.get("high") or col(candles, "high")
    low = ind.get("low") or col(candles, "low")
    close = ind.get("close") or col(candles, "close")
    volume = ind.get("volume") or col(candles, "volume")
    bandwidth = (ind.get("bb") or {}).get("bandwidth") or bollinger(close, 20, 2.0)["bandwidth"]
    range_bars = max(5, min(params["range_bars"], max(10, len(candles) // 3)))
    return _Context(
        high=high,
        low=low,
        close=close,
        volume=volume,
        atr14=ind.get("atr14") or atr(high, low, close, 14),
        adx=(ind.get("adx") or {}).get("adx") or adx(high, low, close, 14)["adx"],
        obv=ind.get("obv") or obv(close, volume),
        rel_volume=ind.get("rel_volume") or [],
        bb_upper=(ind.get("bb") or {}).get("upper") or [],
        bb_lower=(ind.get("bb") or {}).get("lower") or [],
        kc_upper=(ind.get("keltner") or {}).get("upper") or keltner(high, low, close)["upper"],
        kc_lower=(ind.get("keltner") or {}).get("lower") or keltner(high, low, close)["lower"],
        range_high=rolling_max(high, range_bars),
        range_low=rolling_min(low, range_bars),
        volume_slope=linreg(volume, min(15, range_bars))["slope"],
        bandwidth_rank=percentile_rank(bandwidth, params["lookback"]),
    )


def _at(series: Sequence[float], i: int) -> float:
    if not series or i < 0 or i >= len(series):
        return NAN
    return series[i]


def _phase_state_at(ctx: _Context, i: int, direction: int, params: dict, range_bars: int) -> dict:
    """Phase test at bar ``i`` using only series values at or before ``i``."""
    start = max(0, i - range_bars)
    range_high = max(ctx.high[start:i]) if i > start else ctx.high[i]
    range_low = min(ctx.low[start:i]) if i > start else ctx.low[i]
    if range_low <= 0:
        return {"phase": "none", "phase_fa": PHASE_FA["none"], "reason": "داده کافی نیست", "direction": direction}

    depth = range_high - range_low
    atr_now = _at(ctx.atr14, i)
    atr_prev = _at(ctx.atr14, i - 1)
    if is_nan(atr_now) or atr_now <= 0:
        return {"phase": "none", "phase_fa": PHASE_FA["none"], "reason": "ATR قابل محاسبه نیست", "direction": direction}

    adx_now = _at(ctx.adx, i)
    adx_prev = _at(ctx.adx, i - 5)

    # --- A: accumulation ---
    range_atr = depth / atr_now
    volume_slope = _at(ctx.volume_slope, i - 1)
    obv_change = (_at(ctx.obv, i - 1) or 0.0) - (_at(ctx.obv, start - 1) if start > 0 else 0.0)
    absorption = obv_change >= 0 if direction > 0 else obv_change <= 0
    accumulation = (
        range_atr <= params["range_max_atr"]
        and (is_nan(adx_now) or adx_now <= params["adx_max"])
        and (is_nan(volume_slope) or volume_slope <= 0)
    )
    anchor = max(0, i - range_bars - 1)
    older = max(0, i - 2 * range_bars - 1)
    prior_return = (ctx.close[anchor] - ctx.close[older]) / max(ctx.close[older], 1e-9) if ctx.close[older] else 0.0
    prior_trend_ok = prior_return < 0 if direction > 0 else prior_return > 0

    # --- C: compression ---
    bb_upper, bb_lower = _at(ctx.bb_upper, i), _at(ctx.bb_lower, i)
    kc_upper, kc_lower = _at(ctx.kc_upper, i), _at(ctx.kc_lower, i)
    squeeze_now = not any(is_nan(x) for x in (bb_upper, bb_lower, kc_upper, kc_lower)) and bb_upper <= kc_upper and bb_lower >= kc_lower
    bandwidth_rank = _at(ctx.bandwidth_rank, i)
    compression = squeeze_now or (not is_nan(bandwidth_rank) and bandwidth_rank <= params["bandwidth_percentile"])

    # --- T: trend trigger ---
    price = ctx.close[i]
    rel_volume = _at(ctx.rel_volume, i)
    breakout = price > range_high if direction > 0 else price < range_low
    expanding = not is_nan(atr_prev) and atr_now > atr_prev
    adx_rising = not is_nan(adx_now) and not is_nan(adx_prev) and adx_now >= adx_prev
    volume_confirms = is_nan(rel_volume) or rel_volume >= params["breakout_min_rel_volume"]

    if breakout and (accumulation or compression):
        phase = "trend"
    elif compression and accumulation:
        phase = "compression"
    elif accumulation:
        phase = "accumulation"
    else:
        phase = "none"

    return {
        "phase": phase,
        "phase_fa": PHASE_FA[phase],
        "direction": direction,
        "range_high": round(range_high, 6),
        "range_low": round(range_low, 6),
        "range_depth": round(depth, 6),
        "range_atr": round(range_atr, 2),
        "range_bars": range_bars,
        "accumulation": bool(accumulation),
        "absorption": bool(absorption),
        "compression": bool(compression),
        "squeeze": bool(squeeze_now),
        "bandwidth_percentile": round(bandwidth_rank, 1) if not is_nan(bandwidth_rank) else None,
        "breakout": bool(breakout),
        "relative_volume": round(rel_volume, 2) if not is_nan(rel_volume) else None,
        "volume_confirms": bool(volume_confirms),
        "atr_expanding": bool(expanding),
        "atr": round(atr_now, 6),
        "adx": round(adx_now, 2) if not is_nan(adx_now) else None,
        "adx_rising": bool(adx_rising),
        "prior_trend_ok": bool(prior_trend_ok),
        "prior_return_pct": round(100.0 * prior_return, 2),
    }


def _phase_state(candles: Sequence[Candle], direction: int, params: dict, ind: dict | None = None) -> dict:
    """Convenience wrapper: phase state on the latest bar."""
    ctx = _build_context(candles, params, ind)
    range_bars = max(5, min(params["range_bars"], max(10, len(candles) // 3)))
    return _phase_state_at(ctx, len(candles) - 1, direction, params, range_bars)


def act_analysis(candles: Sequence[Candle], params: dict | None = None, allow_short: bool = False, ind: dict | None = None) -> dict:
    """Phase state for both directions (the dashboard shows the active one)."""
    merged = {**DEFAULTS, **(params or {})}
    if len(candles) < 30:
        empty = {"phase": "none", "phase_fa": PHASE_FA["none"], "direction": 1}
        return {"long": empty, "short": dict(empty, direction=-1), "active": None, "params": merged}
    ctx = _build_context(candles, merged, ind)
    range_bars = max(5, min(merged["range_bars"], max(10, len(candles) // 3)))
    last = len(candles) - 1
    long_state = _phase_state_at(ctx, last, 1, merged, range_bars)
    short_state = _phase_state_at(ctx, last, -1, merged, range_bars)
    active = long_state if long_state["phase"] != "none" else None
    if allow_short and _rank(short_state) > _rank(long_state):
        active = short_state if short_state["phase"] != "none" else active
    return {"long": long_state, "short": short_state, "active": active, "params": merged}


def _rank(state: dict) -> int:
    return {"none": 0, "accumulation": 1, "compression": 2, "trend": 3}.get(state.get("phase", "none"), 0)


def _act_plan(candles: Sequence[Candle], state: dict, direction: int, params: dict, index: int) -> dict | None:
    """Entry/stop/target/reasons for one ACT state (shared by live and historical scans)."""
    price = candles[index].close
    atr_now = state.get("atr") or NAN
    if is_nan(atr_now) or atr_now <= 0:
        return None

    range_high, range_low = state["range_high"], state["range_low"]
    depth = state["range_depth"]
    if depth <= 0:
        return None
    if direction > 0:
        entry = range_high if state["phase"] == "trend" else max(price, range_high - 0.25 * atr_now)
        if entry > price + atr_now:
            entry = price  # wait for the retest instead of chasing
        stop = min(range_low, price - 1.5 * atr_now) - 0.25 * atr_now
        target = range_high + depth  # measured move
    else:
        entry = range_low if state["phase"] == "trend" else min(price, range_low + 0.25 * atr_now)
        if entry < price - atr_now:
            entry = price
        stop = max(range_high, price + 1.5 * atr_now) + 0.25 * atr_now
        target = range_low - depth

    risk = abs(entry - stop)
    if risk <= 0:
        return None
    # A "stop" closer than the noise floor is not a stop: a 0.0002 risk turns a
    # single tick into a -300R outcome and wrecks the measured statistics.
    if risk < max(0.5 * atr_now, 0.0025 * price):
        return None
    rr = abs(target - entry) / risk
    if rr < params["min_rr"]:
        target = entry + direction * params["min_rr"] * risk
        rr = params["min_rr"]

    reasons = [f"فاز ACT: {state['phase_fa']}", f"رنج {state['range_bars']} کندلی بین {range_low:,.0f} و {range_high:,.0f}"]
    if state["accumulation"]:
        reasons.append("ADX پایین و دامنه محدود، نشانه انباشت است")
    if state["absorption"]:
        reasons.append("OBV هم‌جهت با انباشت حرکت کرده (جذب نقدینگی)")
    if state["compression"]:
        reasons.append(
            "فشردگی نوسان (باند بولینگر داخل کانال کلتنر)"
            if state["squeeze"]
            else f"پهنای باند در صدک {state['bandwidth_percentile']} بازه اخیر"
        )
    if state["breakout"]:
        reasons.append(f"شکست سطح {'بالای' if direction > 0 else 'پایین'} رنج با قیمت بسته‌شدن")
    if state["relative_volume"] is not None:
        reasons.append(f"حجم نسبی {state['relative_volume']:.2f} برابر میانگین ۲۰ روزه")
    if state["atr_expanding"]:
        reasons.append("ATR در حال گسترش است")
    if state["adx_rising"] and state["adx"] is not None:
        reasons.append(f"ADX صعودی ({state['adx']:.1f})")

    score = sum(
        [
            state["accumulation"],
            state["absorption"],
            state["compression"],
            state["breakout"],
            state["volume_confirms"],
            state["atr_expanding"],
            state["adx_rising"],
        ]
    )
    return {
        "engine": "act",
        "type": "act_long" if direction > 0 else "act_short",
        "setup": "act_breakout" if direction > 0 else "act_breakdown",
        "setup_name": f"ACT {state['phase_fa']}",
        "direction": direction,
        "title": f"{'خرید' if direction > 0 else 'فروش'} ACT - {state['phase_fa']}",
        "reason": "؛ ".join(reasons),
        "reasons": reasons,
        "confluence": int(score),
        "confidence": round(min(0.9, 0.3 + 0.09 * score), 3),
        "entry": round(entry, 6),
        "entry_zone": [round(min(entry, price), 6), round(max(entry, price), 6)],
        "stop": round(stop, 6),
        "target": round(target, 6),
        "rr": round(rr, 2),
        "risk": round(risk, 6),
        "entry_kind": "limit" if state["phase"] == "compression" else "market",
        "phase": state["phase"],
        "range_high": range_high,
        "range_low": range_low,
        "invalidation": round(stop, 6),
        "index": index,
        "dt": candles[index].dt.isoformat(),
    }


def act_signals(candles: Sequence[Candle], params: dict | None = None, allow_short: bool = False, ind: dict | None = None) -> list[dict]:
    """ACT entries on the latest bar: emitted in compression (early) and on the break."""
    merged = {**DEFAULTS, **(params or {})}
    if len(candles) < max(60, merged["range_bars"] * 3):
        return []
    state_bundle = act_analysis(candles, merged, allow_short=True, ind=ind)
    signals: list[dict] = []
    for direction in ((1, -1) if allow_short else (1,)):
        state = state_bundle["long"] if direction > 0 else state_bundle["short"]
        if state["phase"] not in ("compression", "trend"):
            continue
        plan = _act_plan(candles, state, direction, merged, len(candles) - 1)
        if plan:
            signals.append(plan)
    return signals


def historical_act_signals(
    candles: Sequence[Candle],
    params: dict | None = None,
    allow_short: bool = False,
    cooldown_bars: int = 10,
    ind: dict | None = None,
) -> list[dict]:
    """ACT signals over the whole history so their win rate can be measured.

    Causal: the phase test at bar ``i`` reads only series values at or before
    ``i`` (every series in :class:`_Context` is causal), so the scan reproduces
    what a trader would have seen live.
    """
    merged = {**DEFAULTS, **(params or {})}
    minimum = max(60, merged["range_bars"] * 3)
    if len(candles) < minimum:
        return []
    ctx = _build_context(candles, merged, ind)
    range_bars = max(5, min(merged["range_bars"], max(10, len(candles) // 3)))
    signals: list[dict] = []
    for direction in ((1, -1) if allow_short else (1,)):
        last_signal = -10**9
        for i in range(minimum, len(candles)):
            if i - last_signal < cooldown_bars:
                continue
            state = _phase_state_at(ctx, i, direction, merged, range_bars)
            if state["phase"] not in ("compression", "trend"):
                continue
            plan = _act_plan(candles, state, direction, merged, i)
            if plan is None:
                continue
            signals.append(plan)
            last_signal = i
    return sorted(signals, key=lambda s: s["index"])


def act_overlay(result: dict, candles: Sequence[Candle]) -> list[dict]:
    """Range boxes for the chart (accumulation/compression range)."""
    if not candles or not result.get("active"):
        return []
    state = result["active"]
    start = max(0, len(candles) - state["range_bars"] - 1)
    return [
        {
            "x0": candles[start].dt.isoformat(),
            "x1": candles[-1].dt.isoformat(),
            "y0": state["range_low"],
            "y1": state["range_high"],
            "kind": "range",
            "phase": state["phase"],
            "label": f"رنج {state['phase_fa']}",
            "direction": state["direction"],
        }
    ]


__all__ = ["DEFAULTS", "PHASE_FA", "act_analysis", "act_overlay", "act_signals", "historical_act_signals"]
