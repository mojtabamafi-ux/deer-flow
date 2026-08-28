"""Analysis orchestrator: one call turns a symbol into a complete analysis.

Everything the dashboard, the CLI and the DeerFlow agent consume comes from
:func:`analyze`, so all surfaces report identical numbers:

``candles -> indicators -> snapshot -> structure/patterns -> Elliott -> RTM ->
ICT -> ACT -> setups -> backtest -> confluence -> recommendation``

The result is JSON-safe (NaN removed, datetimes as ISO strings) and always
carries ``meta.data_mode`` plus the standing disclaimer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from . import act as act_engine
from . import elliott as elliott_engine
from . import ict as ict_engine
from . import rtm as rtm_engine
from .backtest import BacktestConfig, run_backtest
from .core import Candle, col, describe_candles, iso_list, to_jsonable
from .indicators import compute_all, snapshot
from .patterns import detect_patterns, market_structure, support_resistance
from .setups import SETUP_LIBRARY, scan_setups, setup_ranking
from .signals import DISCLAIMER, build_recommendation


def analyze(
    symbol: str,
    registry,
    timeframe: str = "D1",
    limit: int = 400,
    mode: str | None = None,
    allow_short: bool | None = None,
    backtest_config: BacktestConfig | None = None,
    with_backtest: bool = True,
    backtest_limit: int = 1200,
) -> dict[str, Any]:
    """Full analysis for one symbol.  ``registry`` is a :class:`DataSourceRegistry`.

    ``limit`` is the visible chart window; ``backtest_limit`` is how much history
    is loaded for the win-rate measurement.  Indicators are causal, so analysing
    the long series and slicing the chart afterwards gives exactly the same
    numbers as analysing the short series - but with far more trades to measure.
    """
    meta = registry.resolve(symbol)
    code = str(meta.get("code", symbol))
    if allow_short is None:
        allow_short = registry.allow_short(code)

    total = max(limit, backtest_limit) if with_backtest else limit
    result = registry.candles(code, timeframe, total, mode=mode)
    candles: list[Candle] = list(result.data or [])
    if len(candles) < 30:
        return {
            "meta": {
                "symbol": code,
                "name": meta.get("name") or meta.get("symbol") or code,
                "market": meta.get("market"),
                "source": result.source,
                "data_mode": result.mode,
                "synthetic": True,
                "error": result.error or "داده کافی نیست",
                "timeframe": timeframe,
                "allow_short": allow_short,
            },
            "signals": [],
            "recommendation": {"action": "wait", "action_fa": "داده کافی نیست", "reasons": [], "risks": [result.error or "کمتر از ۳۰ کندل"], "disclaimer": DISCLAIMER},
            "disclaimer": DISCLAIMER,
        }

    last_index = len(candles) - 1
    visible_start = max(0, last_index - limit + 1)
    first_visible_dt = candles[visible_start].dt.isoformat()

    ind = compute_all(candles, timeframe)
    snap = snapshot(ind)
    structure = market_structure(candles)
    patterns = detect_patterns(candles)
    sr = support_resistance(candles)
    waves = elliott_engine.detect_waves(candles, allow_short=allow_short)
    zones = rtm_engine.build_zones(candles)
    ict_overlay = ict_engine.ict_overlay(candles)
    act_state = act_engine.act_analysis(candles, allow_short=allow_short, ind=ind)

    # every engine is scanned over the whole series once; the dashboard shows the
    # signals that fire on the latest bar, the backtester measures all of them.
    all_rtm = rtm_engine.zone_signals(candles, zones, allow_short=allow_short)
    all_ict = ict_engine.ict_signals(candles, allow_short=allow_short)
    all_act = act_engine.act_signals(candles, allow_short=allow_short, ind=ind)
    all_elliott = elliott_engine.elliott_signals(
        waves.get("waves") or [], waves.get("current"), (waves.get("current") or {}).get("direction", 1), candles
    )
    history_signals = scan_setups(ind, candles, allow_short=allow_short)
    history_signals.extend(rtm_engine.historical_zone_signals(candles, zones, allow_short=allow_short, ind=ind))
    history_signals.extend(act_engine.historical_act_signals(candles, allow_short=allow_short, ind=ind))

    current_signals: list[dict] = []
    current_signals.extend(all_rtm)
    current_signals.extend(all_ict)
    current_signals.extend(all_act)
    current_signals.extend(all_elliott)
    current_signals.extend([s for s in history_signals if s["index"] == last_index])
    backtest = {}
    setup_stats: dict[str, dict] = {}
    ranking: list[dict] = []
    if with_backtest:
        config = backtest_config or BacktestConfig(allow_short=allow_short)
        backtest = run_backtest(candles, history_signals, config)
        setup_stats = backtest.get("by_setup", {})
        ranking = setup_ranking(setup_stats)

    # annotate current signals with the measured statistics of their setup
    for signal in current_signals:
        stats = setup_stats.get(signal.get("setup") or signal.get("type") or "", {})
        signal["measured"] = {
            "trades": stats.get("trades", 0),
            "win_rate": stats.get("win_rate"),
            "expectancy_r": stats.get("expectancy_r"),
            "profit_factor": stats.get("profit_factor"),
            "sample_ok": stats.get("sample_ok", False),
        }
        signal["rank"] = _rank_signal(signal)

    if not allow_short:
        # belt and braces: no engine may hand a long-only market a short to act on
        suppressed = [s for s in current_signals if s.get("direction", 1) < 0]
        current_signals = [s for s in current_signals if s.get("direction", 1) > 0]
    else:
        suppressed = []
    current_signals.sort(key=lambda s: (-s.get("rank", 0), -(s.get("confidence") or 0)))

    analysis: dict[str, Any] = {
        "meta": {
            "symbol": code,
            "name": meta.get("name") or meta.get("symbol") or code,
            "market": meta.get("market"),
            "sector": meta.get("sector"),
            "source": result.source,
            "data_mode": result.mode,
            "synthetic": bool(candles[0].synthetic),
            "live_error": result.error,
            "timeframe": timeframe,
            "allow_short": allow_short,
            "candles": describe_candles(candles[visible_start:]),
            "history_bars": len(candles),
            "chart_bars": len(candles) - visible_start,
            "short_signals_suppressed": len(suppressed),
        },
        "chart": _chart_payload(candles, ind, visible_start),
        "snapshot": snap,
        "structure": {
            "trend": structure["trend"],
            "structure": structure["structure"],
            "events": structure["events"][-8:],
            "last_bos": structure["last_bos"],
            "last_choch": structure["last_choch"],
            "support_resistance": sr,
        },
        "patterns": patterns[-12:][::-1],
        "elliott": {
            "waves": waves.get("waves") or [],
            "pivots": waves.get("pivots") or [],
            "current": waves.get("current"),
            "patterns": waves.get("patterns") or [],
            "summary": waves.get("summary"),
            "overlay": _clamp_overlay(elliott_engine.elliott_overlay(waves, candles), first_visible_dt),
            "threshold_pct": waves.get("threshold_pct"),
        },
        "rtm": {
            "zones": zones,
            "active": rtm_engine.active_zones(zones),
            "signals": [s for s in current_signals if s.get("engine") == "rtm"],
            "summary": rtm_engine.rtm_summary(zones, candles[-1].close),
            "overlay": _clamp_overlay(rtm_engine.zone_overlay(zones, candles), first_visible_dt),
        },
        "ict": {
            "overlay": ict_overlay,
            "order_blocks": ict_overlay.get("order_blocks", []),
            "fvg": ict_overlay.get("fvg", []),
            "sweeps": ict_overlay.get("sweeps", []),
            "dealing_range": ict_overlay.get("dealing_range", {}),
            "signals": [s for s in current_signals if s.get("engine") == "ict"],
        },
        "act": {
            "long": act_state["long"],
            "short": act_state["short"],
            "active": act_state["active"],
            "overlay": _clamp_overlay(act_engine.act_overlay(act_state, candles), first_visible_dt),
            "signals": [s for s in current_signals if s.get("engine") == "act"],
        },
        "setups": {
            "library": SETUP_LIBRARY,
            "signals": [s for s in current_signals if s.get("engine") == "setup"],
            "stats": setup_stats,
            "ranking": ranking,
        },
        "signals": current_signals,
        "backtest": {k: v for k, v in backtest.items() if k != "trades"} | {"trades_tail": backtest.get("trades", [])[-20:]},
        "disclaimer": DISCLAIMER,
    }
    analysis["recommendation"] = build_recommendation(analysis, allow_short=allow_short)
    analysis["setup_stats"] = setup_stats
    return to_jsonable(analysis)


def _rank_signal(signal: dict) -> float:
    """Ordering key: confidence first, then measured win rate, then R:R."""
    confidence = signal.get("confidence") or 0.0
    measured = signal.get("measured") or {}
    win_rate = measured.get("win_rate")
    rr = signal.get("rr") or 0.0
    return round(confidence * 100 + (win_rate or 0.5) * 40 + min(rr, 5.0) * 4, 3)


def _clamp_overlay(payload: Any, first_dt: str) -> Any:
    """Clamp overlay x-coordinates into the visible chart window.

    Zones and Elliott waves often start before the visible range; Plotly would
    otherwise widen the x-axis to fit them.
    """
    if isinstance(payload, dict):
        for key in ("lines", "annotations"):
            if key in payload:
                payload[key] = _clamp_overlay(payload[key], first_dt)
        return payload
    if isinstance(payload, list):
        out = []
        for item in payload:
            if isinstance(item, dict):
                item = dict(item)
                if "x0" in item and str(item["x0"]) < first_dt:
                    item["x0"] = first_dt
                if "x" in item and str(item["x"]) < first_dt:
                    item["x"] = first_dt
            out.append(item)
        return out
    return payload


def _chart_payload(candles: Sequence[Candle], ind: dict, start: int = 0) -> dict[str, Any]:
    """Series the dashboard needs, kept separate from the analytic payload."""

    def clean(values) -> list[float]:
        # NaN != NaN is the cheapest NaN test; Plotly wants explicit nulls.
        return [None if v != v else round(float(v), 6) for v in (list(values or [])[start:])]

    def series(key: str) -> list[float]:
        values = ind.get(key) or []
        if values and not isinstance(values[0], (int, float)):
            raise TypeError(f"chart series {key!r} is not numeric: {type(values[0])}")
        return clean(values)

    def nested(block: str, key: str) -> list[float]:
        return clean((ind.get(block) or {}).get(key))

    visible = candles[start:]
    return {
        "dt": iso_list(visible),
        "open": col(visible, "open"),
        "high": col(visible, "high"),
        "low": col(visible, "low"),
        "close": col(visible, "close"),
        "volume": col(visible, "volume"),
        "ema9": series("ema9"),
        "ema20": series("ema20"),
        "ema50": series("ema50"),
        "ema100": series("ema100"),
        "ema200": series("ema200"),
        "bb_upper": nested("bb", "upper"),
        "bb_mid": nested("bb", "mid"),
        "bb_lower": nested("bb", "lower"),
        "kc_upper": nested("keltner", "upper"),
        "kc_lower": nested("keltner", "lower"),
        "dc_upper": nested("donchian", "upper"),
        "dc_lower": nested("donchian", "lower"),
        "rsi14": series("rsi14"),
        "macd_line": nested("macd", "line"),
        "macd_signal": nested("macd", "signal"),
        "macd_hist": nested("macd", "hist"),
        "adx": nested("adx", "adx"),
        "plus_di": nested("adx", "plus_di"),
        "minus_di": nested("adx", "minus_di"),
        "atr14": series("atr14"),
        "supertrend": nested("supertrend", "line"),
        "supertrend_dir": list(ind.get("supertrend", {}).get("direction", [])[start:]),
        "psar": nested("psar", "sar"),
        "obv": series("obv"),
        "vwap": series("vwap"),
        "tenkan": nested("ichimoku", "tenkan"),
        "kijun": nested("ichimoku", "kijun"),
        "senkou_a": nested("ichimoku", "senkou_a"),
        "senkou_b": nested("ichimoku", "senkou_b"),
    }


def scan_market(registry, market: str | None = None, timeframe: str = "D1", limit: int = 300, mode: str | None = None, with_backtest: bool = False) -> list[dict[str, Any]]:
    """Quick multi-symbol scan for the watchlist (no backtest by default).

    Rows come back ranked by confluence score - strongest bias first - with the
    symbols that failed to load pushed to the bottom.
    """
    rows = []
    for entry in registry.symbols(market=market):
        code = str(entry["code"])
        try:
            result = analyze(code, registry, timeframe=timeframe, limit=limit, mode=mode, with_backtest=with_backtest)
        except Exception as exc:  # one bad symbol must not kill the scan
            rows.append({"symbol": entry.get("symbol"), "code": code, "error": f"{type(exc).__name__}: {exc}"})
            continue
        rec = result.get("recommendation") or {}
        rows.append(
            {
                "symbol": entry.get("symbol"),
                "code": code,
                "name": entry.get("name"),
                "market": entry.get("market"),
                "data_mode": (result.get("meta") or {}).get("data_mode"),
                "price": (result.get("snapshot") or {}).get("price", {}).get("close"),
                "change_pct": (result.get("snapshot") or {}).get("price", {}).get("change_pct"),
                "trend": (result.get("snapshot") or {}).get("trend", {}).get("label"),
                "score": rec.get("score"),
                "action": rec.get("action"),
                "action_fa": rec.get("action_fa"),
                "confidence": rec.get("confidence"),
                "signals": len(result.get("signals") or []),
                "elliott": ((result.get("elliott") or {}).get("current") or {}).get("last_label"),
            }
        )
    # ranked by the *magnitude* of the confluence score (a strong bearish read is
    # just as actionable as a strong bullish one); symbols that failed to load go last
    rows.sort(key=lambda r: (1 if r.get("error") else 0, -abs(r.get("score") or 0.0)))
    return rows


__all__ = ["DISCLAIMER", "analyze", "scan_market"]
