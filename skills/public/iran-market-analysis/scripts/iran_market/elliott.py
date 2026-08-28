"""Elliott Wave engine: automatic wave labelling, pattern typing and targets.

Method
------
1.  **Pivots** - a percentage ZigZag whose threshold adapts to the symbol's own
    ATR, then strict high/low alternation.  A fixed percentage would find waves
    in a low-volatility fund and miss them in a small-cap stock.
2.  **Candidates** - every window of 6 alternating pivots (5 legs) is scored
    against the classical impulse rules.
3.  **Rules** (hard - a violation invalidates the count):

    * wave 2 never retraces 100% of wave 1;
    * wave 3 is never the shortest of 1, 3, 5;
    * wave 4 never overlaps wave 1's price territory;
    * wave 3 must exceed the end of wave 1.

4.  **Guidelines** (soft - they move the confidence score, they never veto):
    alternation between waves 2 and 4, wave 3 ≈ 1.618 × wave 1, wave 5 ≈ wave 1
    or 0.618 × (1+3), time symmetry.
5.  **Corrections** - the 3 legs after an impulse are typed as zigzag (5-3-5),
    flat (3-3-5) or expanded flat by the depth of B relative to A.
6.  **Nesting** - an impulse detected inside wave 3 or wave 5 is labelled at the
    minor degree (i-v) under the larger count.

Everything is returned as data: the dashboard draws the labels, the CLI renders
them as text, and the signal engine turns "wave 5 complete" / "wave 3 in
progress" into a directional bias with a named invalidation level.
"""

from __future__ import annotations

from collections.abc import Sequence

from .core import Candle, col, is_nan, last_valid
from .indicators import alternate_pivots, atr, zigzag

#: Fibonacci projections used for wave targets
W3_TARGETS = (1.0, 1.272, 1.618, 2.0, 2.618)
W5_TARGETS = (0.618, 1.0)

IMPULSE_LABELS = ("1", "2", "3", "4", "5")
CORRECTION_LABELS = ("A", "B", "C")

RULE_NAMES = {
    "wave2_no_full_retrace": "موج ۲ نباید ۱۰۰٪ موج ۱ را بازگشت کند",
    "wave3_not_shortest": "موج ۳ نباید کوتاه‌ترین موج باشد",
    "wave4_no_overlap": "موج ۴ نباید وارد قلمرو موج ۱ شود",
    "wave3_beyond_wave1": "موج ۳ باید از انتهای موج ۱ عبور کند",
}


def adaptive_threshold(candles: Sequence[Candle], floor_pct: float = 2.0, ceiling_pct: float = 15.0, mult: float = 2.2) -> float:
    """ZigZag threshold as a percentage, derived from the symbol's own volatility."""
    if len(candles) < 20:
        return floor_pct
    high = col(candles, "high")
    low = col(candles, "low")
    close = col(candles, "close")
    atr_value = last_valid(atr(high, low, close, 14))
    price = close[-1]
    if is_nan(atr_value) or not price:
        return floor_pct
    pct = 100.0 * atr_value * mult / price
    return max(floor_pct, min(ceiling_pct, pct))


def _separate(pivots: list[dict], min_bars: int) -> list[dict]:
    """Drop pivots that are closer than ``min_bars`` to the previously kept one.

    Without this, a single huge bar (a limit-up day on the TSE) creates two
    adjacent pivots and the count produces a zero-length wave, which breaks the
    ratio maths downstream.
    """
    if min_bars <= 1:
        return pivots
    kept: list[dict] = []
    for pivot in pivots:
        if not kept:
            kept.append(pivot)
            continue
        prev = kept[-1]
        if pivot["index"] - prev["index"] >= min_bars:
            kept.append(pivot)
            continue
        # same kind -> keep the more extreme; different kind -> keep the more
        # extreme relative to the pivot before it, so alternation survives.
        if pivot["kind"] == prev["kind"]:
            better = pivot["price"] > prev["price"] if pivot["kind"] == "high" else pivot["price"] < prev["price"]
            if better:
                kept[-1] = pivot
        else:
            swing = abs(pivot["price"] - prev["price"]) / max(prev["price"], 1e-9)
            earlier_swing = abs(prev["price"] - kept[-2]["price"]) / max(kept[-2]["price"], 1e-9) if len(kept) >= 2 else 0.0
            if swing > earlier_swing:
                kept[-1] = pivot
    return kept


def find_pivots(candles: Sequence[Candle], threshold_pct: float | None = None, use_close: bool = False, min_bars: int = 2) -> list[dict]:
    """Alternating ZigZag pivots, tagged with price/time for the chart layer."""
    high = col(candles, "high")
    low = col(candles, "low")
    close = col(candles, "close")
    thr = threshold_pct if threshold_pct is not None else adaptive_threshold(candles)
    pivots = alternate_pivots(zigzag(high, low, close, thr, use_close=use_close))
    pivots = _separate(pivots, min_bars)
    for pivot in pivots:
        pivot["dt"] = candles[pivot["index"]].dt.isoformat()
        pivot["provisional"] = bool(pivot.get("provisional"))
    return pivots


def _legs(pivots: Sequence[dict]) -> list[dict]:
    """Convert pivots into directed legs."""
    legs = []
    for i in range(len(pivots) - 1):
        start, end = pivots[i], pivots[i + 1]
        legs.append(
            {
                "index": i + 1,
                "start_index": start["index"],
                "end_index": end["index"],
                "start_price": start["price"],
                "end_price": end["price"],
                "direction": 1 if end["price"] > start["price"] else -1,
                "length": abs(end["price"] - start["price"]),
                "bars": end["index"] - start["index"],
            }
        )
    return legs


def score_impulse(pivots: Sequence[dict], direction: int) -> dict:
    """Score one 6-pivot window as a 5-wave impulse.  Returns rules, score, confidence."""
    p = list(pivots[:6])
    legs = _legs(p)
    if len(legs) < 5:
        return {"valid": False, "score": 0.0, "rules": {}, "reason": "کمتر از ۵ موج"}
    if any(leg["direction"] != (direction if leg["index"] in (1, 3, 5) else -direction) for leg in legs):
        return {"valid": False, "score": 0.0, "rules": {}, "reason": "الگوی جهت موج‌ها نامعتبر"}

    w1, w2, w3, w4, w5 = legs
    start_price, end_price = p[0]["price"], p[5]["price"]

    if direction == 1:
        rules = {
            "wave2_no_full_retrace": p[2]["price"] > p[0]["price"] - 1e-9,
            "wave3_not_shortest": w3["length"] >= min(w1["length"], w5["length"]) - 1e-9,
            "wave4_no_overlap": p[4]["price"] > p[1]["price"] - 1e-9,
            "wave3_beyond_wave1": p[3]["price"] > p[1]["price"] + 1e-9,
        }
    else:
        rules = {
            "wave2_no_full_retrace": p[2]["price"] < p[0]["price"] + 1e-9,
            "wave3_not_shortest": w3["length"] >= min(w1["length"], w5["length"]) - 1e-9,
            "wave4_no_overlap": p[4]["price"] < p[1]["price"] + 1e-9,
            "wave3_beyond_wave1": p[3]["price"] < p[1]["price"] - 1e-9,
        }
    hard_ok = all(rules.values())

    # --- guidelines (soft) ---
    r2 = w2["length"] / w1["length"] if w1["length"] else 0.0
    r4 = w4["length"] / w3["length"] if w3["length"] else 0.0
    w3_ratio = w3["length"] / w1["length"] if w1["length"] else 0.0
    impulse_len = abs(end_price - start_price)
    w5_ratio = w5["length"] / impulse_len if impulse_len else 0.0
    alternation = abs(r2 - r4) > 0.12 or abs(w2["bars"] - w4["bars"]) > 2
    w3_ideal = 1.0 - min(1.0, abs(w3_ratio - 1.618) / 1.618)
    w5_ideal = max(
        1.0 - min(1.0, abs(w5["length"] - w1["length"]) / max(w1["length"], 1e-9)),
        1.0 - min(1.0, abs(w5["length"] - 0.618 * impulse_len) / max(0.618 * impulse_len, 1e-9)),
    )
    truncated = (p[5]["price"] < p[3]["price"]) if direction == 1 else (p[5]["price"] > p[3]["price"])

    score = 0.0
    score += 40.0 if hard_ok else 0.0
    score += 12.0 * w3_ideal
    score += 10.0 * w5_ideal
    score += 8.0 if alternation else 0.0
    score += 6.0 if 0.236 <= r2 <= 0.786 else 0.0
    score += 6.0 if 0.15 <= r4 <= 0.618 else 0.0
    score -= 10.0 if truncated else 0.0
    score = max(0.0, min(100.0, score))

    return {
        "valid": hard_ok,
        "score": round(score, 1),
        "confidence": round(score / 100.0, 3),
        "rules": rules,
        "guidelines": {
            "alternation": alternation,
            "wave2_retrace": round(r2, 3),
            "wave4_retrace": round(r4, 3),
            "wave3_to_wave1": round(w3_ratio, 3),
            "wave5_to_impulse": round(w5_ratio, 3),
            "truncated_fifth": truncated,
        },
        "reason": "" if hard_ok else "، ".join(RULE_NAMES[k] for k, ok in rules.items() if not ok),
    }


def label_impulse(pivots: Sequence[dict], direction: int, degree: str, score_info: dict, start: int) -> list[dict]:
    """Turn 6 pivots into labelled waves."""
    p = list(pivots[:6])
    legs = _legs(p)
    waves = []
    impulse_length = abs(p[5]["price"] - p[0]["price"])
    for leg in legs:
        label = IMPULSE_LABELS[leg["index"] - 1]
        prev_leg = legs[leg["index"] - 2] if leg["index"] > 1 else None
        waves.append(
            {
                "label": label,
                "degree": degree,
                "direction": leg["direction"],
                "start_index": leg["start_index"],
                "end_index": leg["end_index"],
                "start_dt": p[leg["index"] - 1]["dt"],
                "end_dt": p[leg["index"]]["dt"],
                "start_price": leg["start_price"],
                "end_price": leg["end_price"],
                "length": round(leg["length"], 6),
                "bars": leg["bars"],
                "retrace_of_previous": round(leg["length"] / prev_leg["length"], 3) if prev_leg and prev_leg["length"] else None,
                "pct_of_impulse": round(100.0 * leg["length"] / impulse_length, 2) if impulse_length else None,
                "provisional": bool(p[leg["index"]].get("provisional")),
                "confirmed": leg["index"] < 5 or not p[5].get("provisional"),
            }
        )
    waves[-1]["invalidation"] = _impulse_invalidation(p, direction)
    return waves


def _impulse_invalidation(pivots: Sequence[dict], direction: int) -> float:
    """Level that invalidates the count: the extreme of wave 2 / wave 4 origin."""
    if direction == 1:
        return min(pivots[2]["price"], pivots[4]["price"])
    return max(pivots[2]["price"], pivots[4]["price"])


def score_correction(pivots: Sequence[dict], direction: int, impulse_length: float = 0.0) -> dict:
    """Type the 3 legs following an impulse: zigzag / flat / expanded flat."""
    if len(pivots) < 4:
        return {"valid": False, "score": 0.0, "reason": "کمتر از ۳ موج اصلاحی"}
    p = list(pivots[:4])
    legs = _legs(p)
    a, b, c = legs[0], legs[1], legs[2]
    if a["direction"] != -direction or b["direction"] != direction or c["direction"] != -direction:
        return {"valid": False, "score": 0.0, "reason": "الگوی جهت A-B-C نامعتبر"}
    a_retrace = a["length"] / impulse_length if impulse_length else None
    b_retrace = b["length"] / a["length"] if a["length"] else 0.0
    c_extension = c["length"] / a["length"] if a["length"] else 0.0

    if b_retrace < 0.5:
        kind, kind_fa, score = "zigzag", "زیگزاگ (۵-۳-۵)", 55.0
    elif b_retrace < 0.95:
        kind, kind_fa, score = "flat", "پهنه (۳-۳-۵)", 50.0
    else:
        kind, kind_fa, score = "expanded_flat", "پهنه گسترش‌یافته", 45.0

    if a_retrace is not None:
        score += 15.0 if 0.236 <= a_retrace <= 0.886 else -10.0
    score += 15.0 if 0.618 <= c_extension <= 1.618 else 0.0
    score += 10.0 if b_retrace <= 0.99 else -15.0
    score = max(0.0, min(100.0, score))
    return {
        "valid": True,
        "kind": kind,
        "kind_fa": kind_fa,
        "score": round(score, 1),
        "confidence": round(score / 100.0, 3),
        "metrics": {
            "a_retrace": round(a_retrace, 3) if a_retrace is not None else None,
            "b_retrace_of_a": round(b_retrace, 3),
            "c_extension_of_a": round(c_extension, 3),
        },
    }


def label_correction(pivots: Sequence[dict], degree: str) -> list[dict]:
    p = list(pivots[:4])
    legs = _legs(p)
    waves = []
    for leg, label in zip(legs, CORRECTION_LABELS):
        waves.append(
            {
                "label": label,
                "degree": degree,
                "direction": leg["direction"],
                "start_index": leg["start_index"],
                "end_index": leg["end_index"],
                "start_dt": p[leg["index"] - 1]["dt"],
                "end_dt": p[leg["index"]]["dt"],
                "start_price": leg["start_price"],
                "end_price": leg["end_price"],
                "length": round(leg["length"], 6),
                "bars": leg["bars"],
                "provisional": bool(p[leg["index"]].get("provisional")),
                "confirmed": label != "C" or not p[3].get("provisional"),
            }
        )
    return waves


def detect_waves(candles: Sequence[Candle], pivots: list[dict] | None = None, threshold_pct: float | None = None, allow_short: bool = True) -> dict:
    """Main entry: labelled waves, current count, targets and invalidation."""
    if len(candles) < 20:
        return {"waves": [], "pivots": [], "current": None, "patterns": [], "signals": [], "summary": "داده کافی نیست"}
    pivots = pivots if pivots is not None else find_pivots(candles, threshold_pct)
    if len(pivots) < 6:
        return {
            "waves": [],
            "pivots": pivots,
            "current": None,
            "patterns": [],
            "signals": [],
            "summary": "ساختار موجی کافی پیدا نشد (پیوت‌های کمتر از ۶)",
        }

    best: dict | None = None
    for start in range(0, max(1, len(pivots) - 5)):
        window = pivots[start : start + 6]
        direction = 1 if window[1]["price"] > window[0]["price"] else -1
        info = score_impulse(window, direction)
        if not info["valid"]:
            continue
        # prefer the most recent count; break ties by score
        rank = (start, info["score"])
        if best is None or rank > (best["start"], best["score"]):
            best = {
                "start": start,
                "score": info["score"],
                "info": info,
                "direction": direction,
                "window": window,
            }

    if best is None:
        return {
            "waves": [],
            "pivots": pivots,
            "current": _current_count(pivots, None),
            "patterns": [],
            "signals": [],
            "summary": "هیچ شمارش پنج‌موجی معتبری با قواعد کلاسیک پیدا نشد",
        }

    waves = label_impulse(best["window"], best["direction"], "intermediate", best["info"], best["start"])

    # --- correction that follows the impulse ---
    correction: list[dict] = []
    correction_info: dict = {}
    tail = pivots[best["start"] + 5 :]
    if len(tail) >= 4:
        impulse_len = abs(best["window"][5]["price"] - best["window"][0]["price"])
        correction_info = score_correction(tail, best["direction"], impulse_len)
        if correction_info.get("valid"):
            correction = label_correction(tail, "intermediate")

    # --- nested minor-degree count inside wave 3 ---
    nested: list[dict] = []
    w3 = waves[2]
    inner = [p for p in pivots if w3["start_index"] <= p["index"] <= w3["end_index"]]
    if len(inner) >= 6:
        inner_dir = 1 if inner[1]["price"] > inner[0]["price"] else -1
        inner_info = score_impulse(inner[:6], inner_dir)
        if inner_info["valid"] and inner_info["score"] >= 45:
            nested = label_impulse(inner[:6], inner_dir, "minor", inner_info, 0)
            for wave in nested:
                wave["parent"] = "3"

    all_waves = waves + correction + nested
    current = _current_count(pivots, waves, correction, best["direction"])
    patterns = [
        {
            "type": "impulse_5_wave",
            "label": "پنج موج انگیزشی",
            "start_index": waves[0]["start_index"],
            "end_index": waves[-1]["end_index"] if not correction else correction[-1]["end_index"],
            "direction": best["direction"],
            "confidence": best["info"]["confidence"],
            "rules": best["info"]["rules"],
            "guidelines": best["info"]["guidelines"],
        }
    ]
    if correction_info.get("valid"):
        patterns.append(
            {
                "type": correction_info["kind"],
                "label": correction_info["kind_fa"],
                "start_index": correction[0]["start_index"],
                "end_index": correction[-1]["end_index"],
                "direction": -best["direction"],
                "confidence": correction_info["confidence"],
                "metrics": correction_info["metrics"],
            }
        )
    if nested:
        patterns.append(
            {
                "type": "nested_impulse",
                "label": "پنج موج فرعی داخل موج ۳",
                "start_index": nested[0]["start_index"],
                "end_index": nested[-1]["end_index"],
                "direction": nested[0]["direction"],
                "confidence": round(inner_info["score"] / 100.0, 3),
            }
        )

    return {
        "waves": all_waves,
        "pivots": pivots,
        "impulse": waves,
        "correction": correction,
        "nested": nested,
        "current": current,
        "patterns": patterns,
        "signals": elliott_signals(all_waves, current, best["direction"], candles, allow_short=allow_short),
        "summary": summarize(all_waves, current, best["direction"], best["info"]),
        "threshold_pct": adaptive_threshold(candles) if threshold_pct is None else threshold_pct,
    }


def _current_count(pivots: Sequence[dict], waves: list[dict] | None, correction: list[dict] | None = None, direction: int = 1) -> dict | None:
    """Where price is *now* inside the count, plus the projected targets."""
    if not waves:
        return None
    last_wave = (correction or waves)[-1]
    label = last_wave["label"]
    confirmed = last_wave["confirmed"]
    in_progress = not confirmed
    sequence = [w["label"] for w in (correction or waves)]
    next_label = None
    if correction:
        if label == "C":
            next_label = "شروع شمارش جدید"
    else:
        order = IMPULSE_LABELS
        if label in order:
            idx = order.index(label)
            next_label = order[idx + 1] if idx + 1 < len(order) else "پایان انگیزشی / شروع اصلاح"
    targets: list[dict] = []
    if len(waves) >= 3:
        w1_len = waves[0]["length"]
        w1_end = waves[0]["end_price"]
        for ratio in W3_TARGETS:
            targets.append(
                {
                    "for": "3",
                    "price": round(w1_end + direction * ratio * w1_len, 6),
                    "ratio": ratio,
                    "label": f"هدف موج ۳ = {ratio}× موج ۱",
                }
            )
    if len(waves) >= 5:
        impulse_len = abs(waves[4]["end_price"] - waves[0]["start_price"])
        base = (correction[-1]["end_price"] if correction else waves[4]["end_price"])
        for ratio in W5_TARGETS:
            targets.append(
                {
                    "for": "5",
                    "price": round(base + direction * ratio * impulse_len, 6),
                    "ratio": ratio,
                    "label": f"هدف موج ۵ = {ratio}× طول انگیزشی",
                }
            )
    return {
        "last_label": label,
        "sequence": sequence,
        "in_progress": in_progress,
        "next_label": next_label,
        "direction": direction,
        "targets": sorted(targets, key=lambda t: t["price"], reverse=direction < 0),
        "invalidation": waves[-1].get("invalidation") or waves[0]["start_price"],
        "last_wave": last_wave,
    }


def elliott_signals(waves: list[dict], current: dict | None, direction: int, candles: Sequence[Candle], allow_short: bool = True) -> list[dict]:
    """Directional bias derived from the count (consumed by the signal engine).

    ``allow_short=False`` drops counter-trend sell signals: on the TSE equities
    short selling is not possible, so a "wave 5 is ending" short would be advice
    the user cannot act on.  The information still reaches them as a risk line
    in the recommendation.
    """
    signals: list[dict] = []
    if not current:
        return signals
    label = current["last_label"]
    price = candles[-1].close
    invalidation = current.get("invalidation") or price
    risk = max(abs(price - invalidation), 1e-9)

    targets = {t["for"]: t["price"] for t in current.get("targets", [])}

    def make(direction_: int, title: str, reason: str, confidence: float, target: float | None) -> dict:
        lookback = candles[-20:]
        buffer = 0.5 * max(abs(price - invalidation), price * 0.005)
        if direction_ > 0:
            stop = min(c.low for c in lookback) - buffer
            stop = min(stop, price - 0.5 * risk)
        else:
            stop = max(c.high for c in lookback) + buffer
            stop = max(stop, price + 0.5 * risk)
        return {
            "engine": "elliott",
            "type": "elliott_wave",
            "direction": direction_,
            "title": title,
            "reason": reason,
            "confidence": round(min(max(confidence, 0.0), 1.0), 3),
            "entry": round(price, 6),
            "stop": round(stop, 6),
            "target": round(target, 6) if target else None,
            "invalidation": round(invalidation, 6),
            "index": len(candles) - 1,
            "dt": candles[-1].dt.isoformat(),
        }

    if label == "2" and direction == 1:
        signals.append(make(1, "پایان موج ۲ - شروع موج ۳", "موج ۲ اصلاح خود را تمام کرده و موج ۳ (معمولاً قوی‌ترین موج) آغاز می‌شود.", 0.68, targets.get("3")))
    if label == "3" and direction == 1:
        signals.append(make(1, "ادامه موج ۳", "قیمت در موج ۳ انگیزشی قرار دارد؛ اصلاح‌های کوچک معمولاً فرصت خرید هستند.", 0.6, targets.get("3")))
    if label == "4" and direction == 1:
        signals.append(make(1, "اصلاح موج ۴ - کمین خرید", "موج ۴ نمی‌تواند وارد قلمرو موج ۱ شود؛ سطح بی‌اعتباری، کف موج ۱ است.", 0.62, targets.get("5")))
    if label == "5" and direction == 1:
        signals.append(make(-1, "هشدار پایان موج ۵", "موج ۵ معمولاً آخرین موج انگیزشی است؛ احتمال شروع اصلاح A-B-C بالا می‌رود.", 0.5, None))
    if label == "C" and direction == 1:
        signals.append(make(1, "پایان موج C - احتمال صعود", "اصلاح سه‌موجی کامل شده و شرایط شروع یک شمارش صعودی جدید فراهم است.", 0.58, targets.get("3")))
    if label == "5" and direction == -1:
        signals.append(make(-1, "ادامه موج ۵ نزولی", "قیمت در موج ۵ نزولی است؛ فشار فروش همچنان غالب است.", 0.55, None))
    if label == "C" and direction == -1:
        signals.append(make(1, "پایان موج C نزولی", "پایان اصلاح نزولی می‌تواند نقطه چرخش به سمت بالا باشد.", 0.55, None))
    if not allow_short:
        signals = [s for s in signals if s["direction"] > 0]
    return signals


def summarize(waves: list[dict], current: dict | None, direction: int, info: dict) -> str:
    if not current:
        return "شمارش موجی معتبری پیدا نشد"
    seq = " → ".join(f"{w['label']} ({w['end_price']:,.0f})" for w in waves if w.get("degree") == "intermediate")
    trend_fa = "صعودی" if direction == 1 else "نزولی"
    state = "در حال تکمیل" if current["in_progress"] else "تکمیل‌شده"
    return (
        f"یک ساختار پنج‌موجی {trend_fa} شناسایی شد: {seq}. "
        f"موج جاری: {current['last_label']} ({state}). "
        f"اعتبار شمارش: {info['score']:.0f}/۱۰۰. "
        f"سطح بی‌اعتباری: {current['invalidation']:,.0f}."
    )


def elliott_overlay(result: dict, candles: Sequence[Candle]) -> dict:
    """Chart-ready payload: line segments + annotations for the dashboard."""
    waves = result.get("waves") or []
    lines = [
        {
            "x0": candles[min(w["start_index"], len(candles) - 1)].dt.isoformat(),
            "y0": w["start_price"],
            "x1": candles[min(w["end_index"], len(candles) - 1)].dt.isoformat(),
            "y1": w["end_price"],
            "label": w["label"],
            "degree": w.get("degree"),
            "direction": w["direction"],
        }
        for w in waves
    ]
    annotations = [
        {
            "x": candles[min(w["end_index"], len(candles) - 1)].dt.isoformat(),
            "y": w["end_price"],
            "text": w["label"] if w.get("degree") != "minor" else f"({w['label'].lower()})",
            "direction": w["direction"],
        }
        for w in waves
    ]
    return {"lines": lines, "annotations": annotations}


__all__ = [
    "CORRECTION_LABELS",
    "IMPULSE_LABELS",
    "RULE_NAMES",
    "W3_TARGETS",
    "W5_TARGETS",
    "adaptive_threshold",
    "detect_waves",
    "elliott_overlay",
    "elliott_signals",
    "find_pivots",
    "label_correction",
    "label_impulse",
    "score_correction",
    "score_impulse",
]
