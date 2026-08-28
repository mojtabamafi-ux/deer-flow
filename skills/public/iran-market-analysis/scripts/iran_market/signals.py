"""Confluence scoring and the recommendation engine.

The recommendation is a **weighted confluence score**, not a prediction:

* every factor contributes a signed score in ``-1..+1`` and carries the number
  that produced it, so the reader can audit the decision;
* the action thresholds are conservative (a BUY needs +35 and no red flag in
  the trend block);
* the output always includes the invalidation level, the measured win rate of
  the setups behind it (with its sample size), the risk factors and the standing
  disclaimer.  Nothing here promises an outcome, and nothing here trades.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .core import is_nan

BUY_THRESHOLD = 35.0
SELL_THRESHOLD = -35.0

FACTOR_WEIGHTS = {
    "trend": 0.30,
    "momentum": 0.22,
    "volume": 0.14,
    "volatility": 0.12,
    "structure": 0.22,
}


def _score(value: float, low: float, high: float) -> float:
    """Map ``value`` linearly onto -1..+1 between ``low`` and ``high``."""
    if is_nan(value) or value is None:
        return 0.0
    if value <= low:
        return -1.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low) * 2.0 - 1.0


def build_factors(analysis: dict) -> list[dict]:
    """Every input to the confluence score, with the number behind it."""
    snap = analysis.get("snapshot") or {}
    trend = snap.get("trend") or {}
    momentum = snap.get("momentum") or {}
    volatility = snap.get("volatility") or {}
    volume = snap.get("volume") or {}
    structure = analysis.get("structure") or {}
    elliott = analysis.get("elliott") or {}
    rtm = analysis.get("rtm") or {}
    act = analysis.get("act") or {}
    ict = analysis.get("ict") or {}

    factors: list[dict] = []

    def add(block: str, name: str, score: float, value: Any, note: str) -> None:
        factors.append({"block": block, "name": name, "score": round(max(-1.0, min(1.0, score)), 3), "value": value, "note": note})

    # --- trend ---
    adx = trend.get("adx")
    di_diff = (trend.get("plus_di") or 0) - (trend.get("minus_di") or 0)
    add("trend", "چیدمان میانگین‌های متحرک", 1.0 if trend.get("ema_stack_bullish") else (-1.0 if trend.get("ema_stack_bearish") else 0.0),
        "EMA9>EMA20>EMA50" if trend.get("ema_stack_bullish") else ("EMA9<EMA20<EMA50" if trend.get("ema_stack_bearish") else "بدون چیدمان"),
        "ترتیب میانگین‌ها جهت غالب روند را نشان می‌دهد")
    add("trend", "ADX / DI", _score(adx if not is_nan(adx) else 0, 15, 35) * (1.0 if di_diff > 0 else -1.0),
        round(adx, 1) if not is_nan(adx) else None, f"قدرت روند {trend.get('adx_state')}؛ اختلاف DI به نفع {'خریداران' if di_diff > 0 else 'فروشندگان'}")
    add("trend", "سوپرتند", float(trend.get("supertrend_direction") or 0), trend.get("supertrend_direction"),
        "سوپرتند صعودی است" if trend.get("supertrend_direction") == 1 else "سوپرتند نزولی است")
    add("trend", "پارابولیک SAR", float(trend.get("psar_direction") or 0), trend.get("psar_direction"),
        "SAR زیر قیمت (صعودی)" if trend.get("psar_direction") == 1 else "SAR بالای قیمت (نزولی)")
    add("trend", "موقعیت نسبت به EMA200", 1.0 if trend.get("above_ema200") else -1.0, trend.get("above_ema200"),
        "قیمت بالای EMA200 است" if trend.get("above_ema200") else "قیمت زیر EMA200 است")

    # --- momentum ---
    rsi = momentum.get("rsi14")
    rsi_score = _score(rsi if not is_nan(rsi) else 50, 30, 70)
    if not is_nan(rsi) and rsi >= 75:
        rsi_score = -0.4  # extreme overbought is a warning, not a buy signal
    if not is_nan(rsi) and rsi <= 25:
        rsi_score = 0.4
    add("momentum", "RSI(14)", rsi_score, round(rsi, 1) if not is_nan(rsi) else None, f"وضعیت: {momentum.get('rsi_state')}")
    hist = momentum.get("macd_hist")
    add("momentum", "هیستوگرام MACD", 0.6 if (hist or 0) > 0 else -0.6, round(hist, 4) if hist is not None and not is_nan(hist) else None,
        "مومنتوم مثبت" if (hist or 0) > 0 else "مومنتوم منفی")
    add("momentum", "شیب هیستوگرام MACD", 0.4 if momentum.get("macd_hist_rising") else -0.4, momentum.get("macd_hist_rising"),
        "هیستوگرام در حال رشد است" if momentum.get("macd_hist_rising") else "هیستوگرام در حال کاهش است")
    stoch_k = momentum.get("stoch_k")
    add("momentum", "استوکاستیک %K", _score(stoch_k if not is_nan(stoch_k) else 50, 20, 80), round(stoch_k, 1) if not is_nan(stoch_k) else None,
        "منطقه اشباع" if (not is_nan(stoch_k) and (stoch_k > 80 or stoch_k < 20)) else "منطقه میانی")
    cci = momentum.get("cci20")
    add("momentum", "CCI(20)", _score(cci if not is_nan(cci) else 0, -100, 100), round(cci, 1) if not is_nan(cci) else None,
        "بالای ۱۰۰: روند قوی" if (not is_nan(cci) and cci > 100) else ("زیر -۱۰۰: ضعف" if (not is_nan(cci) and cci < -100) else "خنثی"))

    # --- volume ---
    rel_volume = volume.get("relative_volume")
    price_up = (snap.get("price") or {}).get("change_pct") or 0
    volume_score = 0.0
    if rel_volume is not None and not is_nan(rel_volume) and rel_volume > 1.0:
        # above-average volume only confirms the direction price is already going
        volume_score = (0.8 if price_up > 0 else -0.8) * min(1.0, rel_volume - 1.0)
    add("volume", "حجم نسبی", volume_score, round(rel_volume, 2) if rel_volume is not None and not is_nan(rel_volume) else None,
        "حجم بیشتر از میانگین در جهت حرکت تأییدیه است")
    mfi = volume.get("mfi14")
    add("volume", "MFI(14)", _score(mfi if not is_nan(mfi) else 50, 20, 80), round(mfi, 1) if not is_nan(mfi) else None,
        "جریان پول وزنی-حجمی")
    cmf = volume.get("cmf20")
    add("volume", "CMF(20)", _score(cmf if not is_nan(cmf) else 0, -0.15, 0.15), round(cmf, 3) if not is_nan(cmf) else None,
        "جریان پول چایکین")

    # --- volatility ---
    add("volatility", "فشردگی نوسان", 0.0 if not volatility.get("squeeze_on") else 0.3, volatility.get("squeeze_on"),
        "فشردگی فعال است (انرژی ذخیره‌شده)" if volatility.get("squeeze_on") else "فشردگی فعال نیست")
    percent_b = volatility.get("bb_percent_b")
    add("volatility", "موقعیت در باند بولینگر (%B)", _score(percent_b if not is_nan(percent_b) else 0.5, 0.05, 0.95),
        round(percent_b, 3) if not is_nan(percent_b) else None, "نزدیکی قیمت به باندهای بالا/پایین")

    # --- structure ---
    structure_label = structure.get("structure") or ""
    add("structure", "ساختار بازار (BOS/CHoCH)", 0.8 if "uptrend" in structure_label else (-0.8 if "downtrend" in structure_label else 0.0),
        structure_label, "روند سوئینگ‌ها: HH/HL یا LH/LL")
    current = elliott.get("current")
    if current:
        label = current.get("last_label")
        elliott_score = {"2": 0.8, "3": 0.6, "4": 0.5, "5": -0.3, "A": -0.2, "B": -0.4, "C": 0.5}.get(label, 0.0)
        if current.get("direction") == -1:
            elliott_score = -elliott_score
        add("structure", f"شمارش الیوت (موج {label})", elliott_score, label, elliott.get("summary", ""))
    summary = rtm.get("summary") or {}
    demand = summary.get("nearest_demand")
    supply = summary.get("nearest_supply")
    if demand and demand.get("distance_pct") is not None and abs(demand["distance_pct"]) < 6:
        add("structure", "نزدیکی به ناحیه تقاضا", 0.7 * min(1.0, demand.get("strength", 0.5)), f"{demand['distance_pct']}%",
            f"ناحیه تقاضای نزدیک با قدرت {demand['strength']}")
    if supply and supply.get("distance_pct") is not None and abs(supply["distance_pct"]) < 6:
        add("structure", "نزدیکی به ناحیه عرضه", -0.7 * min(1.0, supply.get("strength", 0.5)), f"{supply['distance_pct']}%",
            f"ناحیه عرضه نزدیک با قدرت {supply['strength']}")
    dealing = (ict.get("overlay") or {}).get("dealing_range") or {}
    if dealing:
        add("structure", "منطقه تخفیف/گران (ICT)", 0.6 if dealing.get("zone") == "discount" else -0.6, dealing.get("position"),
            dealing.get("zone_fa", ""))
    phase = (act.get("active") or {}).get("phase")
    if phase and phase != "none":
        direction = (act.get("active") or {}).get("direction", 1)
        add("structure", f"فاز ACT: {phase}", (0.7 if phase == "trend" else 0.45) * direction, phase,
            (act.get("active") or {}).get("phase_fa", ""))
    return factors


def confluence_score(factors: Sequence[dict]) -> dict[str, Any]:
    """Weighted score in -100..+100 with the per-block breakdown."""
    blocks: dict[str, list[float]] = {}
    for factor in factors:
        blocks.setdefault(factor["block"], []).append(factor["score"])
    breakdown = {}
    total = 0.0
    for block, scores in blocks.items():
        if not scores:
            continue
        block_score = sum(scores) / len(scores) * 100.0
        weight = FACTOR_WEIGHTS.get(block, 0.1)
        breakdown[block] = {"score": round(block_score, 1), "weight": weight, "factors": len(scores), "contribution": round(block_score * weight, 2)}
        total += block_score * weight
    used_weight = sum(FACTOR_WEIGHTS.get(b, 0.1) for b in blocks) or 1.0
    return {
        "score": round(total / used_weight, 1),
        "breakdown": breakdown,
        "bullish_factors": sum(1 for f in factors if f["score"] > 0.2),
        "bearish_factors": sum(1 for f in factors if f["score"] < -0.2),
        "neutral_factors": sum(1 for f in factors if abs(f["score"]) <= 0.2),
    }


def build_recommendation(analysis: dict, allow_short: bool = False, min_rr: float = 1.5) -> dict[str, Any]:
    """The highlighted buy/sell/wait card, with reasons, risks and disclaimer."""
    factors = build_factors(analysis)
    score = confluence_score(factors)
    value = score["score"]
    signals: list[dict] = analysis.get("signals") or []
    price = (analysis.get("snapshot") or {}).get("price", {}).get("close")
    atr_value = (analysis.get("snapshot") or {}).get("volatility", {}).get("atr14")

    long_signals = [s for s in signals if s.get("direction", 1) > 0]
    short_signals = [s for s in signals if s.get("direction", 1) < 0 and allow_short]

    action = "wait"
    action_fa = "انتظار / بدون اقدام"
    confidence = abs(value) / 100.0
    reasons: list[str] = []
    risks: list[str] = []
    entry = stop = target = target2 = None
    rr = None
    supporting: list[dict] = []

    def _reachable(signal: dict) -> float:
        """Rank key that prefers entries close to the current price."""
        if not price or signal.get("entry") is None:
            return signal.get("confidence", 0.0)
        distance = abs(signal["entry"] - price) / (atr_value if (atr_value and not is_nan(atr_value)) else max(price * 0.01, 1e-9))
        penalty = max(0.0, distance - 1.5) * 0.25
        return (signal.get("confidence", 0.0) or 0.0) + min(signal.get("rr", 0.0) or 0.0, 5.0) * 0.05 - penalty

    if value >= BUY_THRESHOLD and long_signals:
        action, action_fa = "buy", "پیشنهاد خرید (تحلیلی)"
        best = max(long_signals, key=_reachable)
        entry, stop, target = best["entry"], best["stop"], best.get("target")
        if entry and stop and target:
            risk = abs(entry - stop)
            target2 = round(entry + 2.0 * risk, 6) if risk else None
            rr = round(abs(target - entry) / risk, 2) if risk else None
        reasons = list(best.get("reasons") or [best.get("reason", "")])
        supporting = [s for s in long_signals if s is not best][:3]
        confidence = min(0.95, 0.4 + 0.4 * (value / 100.0) + 0.15 * best.get("confidence", 0.5))
    elif value <= SELL_THRESHOLD and short_signals:
        action, action_fa = "sell", "پیشنهاد فروش (تحلیلی)"
        best = max(short_signals, key=_reachable)
        entry, stop, target = best["entry"], best["stop"], best.get("target")
        if entry and stop and target:
            risk = abs(stop - entry)
            target2 = round(entry - 2.0 * risk, 6) if risk else None
            rr = round(abs(entry - target) / risk, 2) if risk else None
        reasons = list(best.get("reasons") or [best.get("reason", "")])
        supporting = [s for s in short_signals if s is not best][:3]
        confidence = min(0.95, 0.4 + 0.4 * (abs(value) / 100.0) + 0.15 * best.get("confidence", 0.5))
    elif value >= BUY_THRESHOLD and not allow_short:
        action_fa = "تمایل صعودی بدون سیگنال ورود معتبر"
    elif value <= SELL_THRESHOLD and not allow_short:
        action_fa = "تمایل نزولی - در بازار سهام امکان فروش استقراضی نیست"
        risks.append("فروش استقراضی در بورس اوراق بهادار تهران ممکن نیست؛ این تمایل صرفاً هشدار ریسک است.")
    else:
        action_fa = "بازار در وضعیت خنثی است - صبر برای ستاپ بهتر"

    if action != "wait" and atr_value and not is_nan(atr_value):
        risks.append(f"نوسان متوسط روزانه (ATR) حدود {atr_value:,.0f} است؛ اندازه پوزیشن باید بر همین اساس تنظیم شود.")
    if action != "wait" and entry is not None and price and atr_value and not is_nan(atr_value):
        distance = abs(entry - price) / atr_value
        if distance > 1.5:
            risks.append(
                f"نقطه ورود پیشنهادی {distance:.1f} برابر ATR با قیمت فعلی فاصله دارد؛ این یک سفارش محدود است و ممکن است هرگز فعال نشود."
            )
    if (analysis.get("structure") or {}).get("last_choch"):
        risks.append("تغییر ماهیت (CHoCH) در ساختار ثبت شده است؛ احتمال بازگشت روند وجود دارد.")
    suppressed = (analysis.get("meta") or {}).get("short_signals_suppressed", 0)
    if suppressed and not allow_short:
        risks.append(
            f"{suppressed} سیگنال فروش/نزولی شناسایی شد اما در این بازار امکان فروش استقراضی وجود ندارد؛ "
            "این سیگنال‌ها فقط به‌عنوان هشدار ریسک برای دارندگان سهم معنا دارند."
        )
    if (analysis.get("snapshot") or {}).get("volatility", {}).get("squeeze_on"):
        risks.append("فشردگی نوسان فعال است؛ شکست می‌تواند در هر دو جهت باشد.")
    from .setups import setup_name

    stats = analysis.get("setup_stats", {}) or {}
    low_sample = sorted(
        {setup_name(s["setup"]) for s in (long_signals + short_signals) if s.get("setup") and stats.get(s["setup"], {}).get("trades", 0) < 20}
    )
    if low_sample:
        risks.append(f"نمونه معاملات تاریخی برای {', '.join(low_sample)} کمتر از ۲۰ مورد است؛ نرخ پیروزی آن‌ها از نظر آماری قابل اتکا نیست.")

    top_factors = sorted(factors, key=lambda f: -abs(f["score"]))[:6]
    return {
        "action": action,
        "action_fa": action_fa,
        "score": value,
        "confidence": round(confidence, 3),
        "entry": entry,
        "stop": stop,
        "target": target,
        "target2": target2,
        "rr": rr,
        "reasons": [r for r in reasons if r],
        "risks": risks,
        "supporting_signals": [
            {"engine": s.get("engine"), "setup": s.get("setup"), "title": s.get("title") or s.get("setup_name"), "confidence": s.get("confidence"), "rr": s.get("rr")}
            for s in supporting
        ],
        "key_factors": top_factors,
        "confluence": score,
        "price": price,
        "disclaimer": (
            "این خروجی صرفاً نتیجه تحلیل کمّی روی داده‌های بارگذاری‌شده است؛ نه توصیه سرمایه‌گذاری شخصی‌شده است، "
            "نه تضمین سود یا بازگشت سرمایه، و هیچ معامله‌ای به‌صورت خودکار انجام نمی‌شود. نرخ‌های پیروزی ذکرشده "
            "نتیجه گذشته همان داده‌ها هستند و رفتار آینده بازار را تضمین نمی‌کنند."
        ),
    }


DISCLAIMER = (
    "تحلیل‌های این داشبورد صرفاً جنبه اطلاع‌رسانی دارند؛ تضمین سود یا بازگشت سرمایه داده نمی‌شود، "
    "توصیه شخصی‌شده مبتنی بر شرایط مالی فردی ارائه نمی‌گردد و هیچ معامله‌ای بدون تأیید کاربر اجرا نمی‌شود."
)


__all__ = ["BUY_THRESHOLD", "DISCLAIMER", "FACTOR_WEIGHTS", "SELL_THRESHOLD", "build_factors", "build_recommendation", "confluence_score"]
