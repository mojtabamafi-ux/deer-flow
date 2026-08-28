"""Strategy-engine tests: Elliott, RTM, ICT, ACT, market structure.

Each engine is exercised on a purpose-built synthetic series whose structure is
known by construction, plus rule-violation cases that must be *rejected* - a
wave counter that accepts an invalid count is worse than none.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest
from iran_market import act as act_engine
from iran_market import elliott as elliott_engine
from iran_market import ict as ict_engine
from iran_market import rtm as rtm_engine
from iran_market.core import Candle
from iran_market.data.sample import synthetic_candles
from iran_market.indicators import compute_all
from iran_market.patterns import detect_patterns, market_structure, support_resistance
from iran_market.setups import scan_setups

START = datetime(2024, 1, 1, 12, 30)


def make_candles(closes: list[float], symbol: str = "TEST") -> list[Candle]:
    """Candles from a close path with a tight deterministic range (±0.5%)."""
    candles = []
    previous = closes[0]
    for i, close in enumerate(closes):
        candles.append(
            Candle(
                dt=START + timedelta(days=i),
                open=previous,
                high=max(previous, close) * 1.005,
                low=min(previous, close) * 0.995,
                close=close,
                volume=1_000_000.0,
                value=1_000_000.0 * close,
                symbol=symbol,
                source="test",
                timeframe="D1",
            )
        )
        previous = close
    return candles


def leg(start: float, end: float, bars: int) -> list[float]:
    step = (end - start) / bars
    return [start + step * i for i in range(1, bars + 1)]


# ---------------------------------------------------------------------------
# Elliott
# ---------------------------------------------------------------------------

#: A textbook up impulse: pivots at 100 -> 110 -> 104 -> 122 -> 114 -> 127
IMPULSE_CLOSES = (
    [100.0]
    + leg(100.0, 110.0, 8)     # wave 1
    + leg(110.0, 104.0, 5)     # wave 2 (60% retrace, stays above the wave-1 origin)
    + leg(104.0, 122.0, 12)    # wave 3 (longest)
    + leg(122.0, 114.0, 6)     # wave 4 (retraces to 114, above the wave-1 top of 110)
    + leg(114.0, 127.0, 9)     # wave 5
)


def test_elliott_labels_a_valid_five_wave_impulse():
    candles = make_candles(IMPULSE_CLOSES)
    result = elliott_engine.detect_waves(candles, threshold_pct=3.0)
    impulse = result["impulse"]
    assert [w["label"] for w in impulse] == ["1", "2", "3", "4", "5"]
    lengths = [w["length"] for w in impulse]
    assert lengths[2] >= min(lengths[0], lengths[4]), "wave 3 must not be the shortest"
    assert impulse[3]["end_price"] > impulse[0]["end_price"], "wave 4 must not overlap wave 1"
    assert impulse[2]["end_price"] > impulse[0]["end_price"], "wave 3 must exceed wave 1"
    assert result["current"]["direction"] == 1
    assert result["current"]["last_label"] == "5"
    assert result["current"]["in_progress"] is True, "the provisional last pivot means wave 5 is not confirmed"
    assert result["threshold_pct"] == 3.0
    assert "پنج\u200cموجی صعودی" in result["summary"]


def test_elliott_wave_targets_are_fibonacci_projections_of_the_count():
    candles = make_candles(IMPULSE_CLOSES)
    result = elliott_engine.detect_waves(candles, threshold_pct=3.0)
    targets = result["current"]["targets"]
    assert targets
    ratios = {t["ratio"] for t in targets}
    assert 1.618 in ratios, "wave-3 target must include the 1.618 projection"
    assert 0.618 in ratios, "wave-5 target must include the 0.618 projection"
    wave3 = [t for t in targets if t["for"] == "3"]
    w1 = result["impulse"][0]
    assert wave3[0]["price"] == pytest.approx(w1["end_price"] + w1["length"], 6)


def test_elliott_overlay_draws_one_segment_per_wave():
    candles = make_candles(IMPULSE_CLOSES)
    result = elliott_engine.detect_waves(candles, threshold_pct=3.0)
    overlay = elliott_engine.elliott_overlay(result, candles)
    assert len(overlay["lines"]) == len(result["waves"])
    assert len(overlay["annotations"]) == len(result["waves"])
    for line in overlay["lines"]:
        assert line["x0"] < line["x1"]
        assert line["label"] in ("1", "2", "3", "4", "5", "A", "B", "C")


def test_elliott_rejects_a_count_where_wave_four_overlaps_wave_one():
    pivots = [
        {"index": 0, "price": 100.0, "kind": "low"},
        {"index": 5, "price": 110.0, "kind": "high"},
        {"index": 9, "price": 104.0, "kind": "low"},
        {"index": 20, "price": 122.0, "kind": "high"},
        {"index": 26, "price": 108.0, "kind": "low"},   # drops into wave 1's territory
        {"index": 33, "price": 127.0, "kind": "high"},
    ]
    result = elliott_engine.score_impulse(pivots, 1)
    assert result["valid"] is False
    assert result["rules"]["wave4_no_overlap"] is False
    assert "موج ۴" in result["reason"]


def test_elliott_rejects_a_shortest_wave_three():
    pivots = [
        {"index": 0, "price": 100.0, "kind": "low"},
        {"index": 5, "price": 110.0, "kind": "high"},   # wave 1 = 10
        {"index": 9, "price": 104.0, "kind": "low"},    # wave 2
        {"index": 14, "price": 112.0, "kind": "high"},  # wave 3 = 8 -> shortest, invalid
        {"index": 18, "price": 107.0, "kind": "low"},   # wave 4
        {"index": 23, "price": 120.0, "kind": "high"},  # wave 5 = 13
    ]
    result = elliott_engine.score_impulse(pivots, 1)
    assert result["valid"] is False
    assert result["rules"]["wave3_not_shortest"] is False
    assert "موج ۳" in result["reason"]


def test_elliott_rejects_a_full_retrace_of_wave_two():
    pivots = [
        {"index": 0, "price": 100.0, "kind": "low"},
        {"index": 5, "price": 110.0, "kind": "high"},
        {"index": 9, "price": 99.0, "kind": "low"},     # below the wave-1 origin
        {"index": 20, "price": 122.0, "kind": "high"},
        {"index": 26, "price": 115.0, "kind": "low"},
        {"index": 33, "price": 127.0, "kind": "high"},
    ]
    result = elliott_engine.score_impulse(pivots, 1)
    assert result["valid"] is False
    assert result["rules"]["wave2_no_full_retrace"] is False


def test_elliott_rejects_a_window_whose_leg_directions_do_not_alternate():
    pivots = [
        {"index": 0, "price": 100.0, "kind": "low"},
        {"index": 5, "price": 110.0, "kind": "high"},
        {"index": 9, "price": 115.0, "kind": "high"},   # not a retracement at all
        {"index": 20, "price": 122.0, "kind": "high"},
        {"index": 26, "price": 115.0, "kind": "low"},
        {"index": 33, "price": 127.0, "kind": "high"},
    ]
    result = elliott_engine.score_impulse(pivots, 1)
    assert result["valid"] is False


def test_elliott_wave_two_signal_fires():
    """Regression test: this signal used to sit after a `return` and could never fire."""
    candles = make_candles(IMPULSE_CLOSES)
    current = {
        "last_label": "2",
        "sequence": ["1", "2"],
        "direction": 1,
        "invalidation": 99.0,
        "targets": [{"for": "3", "price": 126.2, "ratio": 1.618, "label": "هدف موج ۳"}],
    }
    signals = elliott_engine.elliott_signals([], current, 1, candles)
    assert len(signals) == 1
    assert signals[0]["direction"] == 1
    assert "موج ۲" in signals[0]["title"]
    assert signals[0]["stop"] < signals[0]["entry"]
    assert signals[0]["target"] == pytest.approx(126.2)
    assert signals[0]["index"] == len(candles) - 1


def test_elliott_wave_five_emits_a_counter_trend_warning():
    candles = make_candles(IMPULSE_CLOSES)
    current = {"last_label": "5", "sequence": ["1", "2", "3", "4", "5"], "direction": 1, "invalidation": 114.0, "targets": []}
    signals = elliott_engine.elliott_signals([], current, 1, candles)
    assert [s["direction"] for s in signals] == [-1]
    assert "موج ۵" in signals[0]["title"]


def test_elliott_counter_trend_stop_stays_beyond_the_recent_extreme():
    """A counter-trend stop must never come from the impulse invalidation level."""
    candles = make_candles(IMPULSE_CLOSES)
    current = {"last_label": "5", "sequence": ["1", "2", "3", "4", "5"], "direction": 1, "invalidation": 104.0, "targets": []}
    signal = elliott_engine.elliott_signals([], current, 1, candles)[-1]
    recent_high = max(c.high for c in candles[-20:])
    assert signal["stop"] >= recent_high, "a short stop must sit above the recent high, not at the wave-2 origin"


# ---------------------------------------------------------------------------
# RTM
# ---------------------------------------------------------------------------


def test_rtm_zone_types_follow_the_legs_around_the_base():
    candles = synthetic_candles("RTMTEST", "D1", 400)
    zones = rtm_engine.build_zones(candles)
    assert zones
    assert {z["type"] for z in zones} <= {"RBR", "DBR", "DBD", "RBD"}
    for zone in zones:
        assert zone["low"] < zone["eq"] < zone["high"]
        assert zone["quarts"]["Q4"] != zone["quarts"]["Q1"]
        if zone["kind"] == "demand":
            assert zone["type"] in ("RBR", "DBR")
            assert zone["mpl"] == zone["low"], "a demand zone dies below its low"
        else:
            assert zone["type"] in ("DBD", "RBD")
            assert zone["mpl"] == zone["high"], "a supply zone dies above its high"


def test_rtm_mitigation_requires_price_to_break_the_zone():
    """An earlier revision judged mitigation by the departure leg, which is backwards:
    it consumed 33 of 33 demand zones on the first sample symbol."""
    candles = synthetic_candles("46348559193224090", "D1", 600)
    zones = rtm_engine.build_zones(candles)
    assert zones
    broken = live = 0
    for zone in zones:
        after = candles[zone["end_index"] :]
        if zone["mitigated"]:
            broken += 1
            assert zone["mitigation_index"] is not None
            assert zone["mitigation_index"] >= zone["end_index"]
            bar = candles[zone["mitigation_index"]]
            if zone["kind"] == "demand":
                assert bar.close < zone["low"], "demand dies only on a close below its low"
            else:
                assert bar.close > zone["high"], "supply dies only on a close above its high"
        else:
            live += 1
            if zone["kind"] == "demand":
                assert all(c.close >= zone["low"] for c in after), "an unmitigated demand zone must never have closed below it"
            else:
                assert all(c.close <= zone["high"] for c in after), "an unmitigated supply zone must never have closed above it"
    assert broken and live, "a 600-bar series must contain both mitigated and live zones"


def test_rtm_zone_plan_rejects_zones_deeper_than_the_atr_cap():
    candles = synthetic_candles("RTMCAP", "D1", 400)
    zones = rtm_engine.build_zones(candles)
    atr_now = compute_all(candles, "D1")["atr14"][-1]
    deep = [z for z in zones if z["depth"] > rtm_engine.MAX_ZONE_DEPTH_ATR * atr_now]
    assert deep, "the sample series must contain at least one over-deep range to reject"
    price = candles[-1].close
    for zone in deep:
        assert rtm_engine._zone_plan(zone, price, atr_now, zones, 1.2) is None, "a range deeper than 6 ATR is a range, not a zone"
    # ...while a live zone of normal depth, priced at its own equilibrium, still plans
    shallow = [z for z in zones if not z["mitigated"] and z["depth"] <= rtm_engine.MAX_ZONE_DEPTH_ATR * atr_now]
    assert shallow
    assert all(rtm_engine._zone_plan(z, z["eq"], atr_now, zones, 1.2) for z in shallow)


def test_rtm_historical_signals_are_actionable_and_capped():
    candles = synthetic_candles("46348559193224090", "D1", 600)
    zones = rtm_engine.build_zones(candles)
    signals = rtm_engine.historical_zone_signals(candles, zones, ind=compute_all(candles, "D1"))
    assert signals, "the sample series must produce RTM signals"
    for signal in signals:
        assert signal["engine"] == "rtm"
        assert signal["rr"] >= 1.2
        assert signal["zone_id"] in {z["id"] for z in zones}
        if signal["direction"] > 0:
            assert signal["stop"] < signal["entry"] < signal["target"]
        else:
            assert signal["target"] < signal["entry"] < signal["stop"]
    per_bar: dict[int, int] = {}
    for signal in signals:
        per_bar[signal["index"]] = per_bar.get(signal["index"], 0) + 1
    assert max(per_bar.values()) <= 4, "overlapping zones must not flood a single bar with signals"


def test_rtm_summary_counts_live_and_mitigated_zones():
    candles = synthetic_candles("RTMSUM", "D1", 400)
    zones = rtm_engine.build_zones(candles)
    summary = rtm_engine.rtm_summary(zones, candles[-1].close)
    assert summary["zones_total"] == len(zones)
    assert summary["zones_live"] + summary["zones_mitigated"] == summary["zones_total"]


# ---------------------------------------------------------------------------
# ICT
# ---------------------------------------------------------------------------


def test_fvg_detects_a_three_candle_imbalance():
    closes = [100.0] * 11 + [100.5, 108.0]
    candles = make_candles(closes)
    candles[12] = Candle(dt=candles[12].dt, open=107.5, high=109.0, low=107.0, close=108.0, volume=1000, symbol="T", source="t")
    gaps = [g for g in ict_engine.fair_value_gaps(candles) if g["kind"] == "bullish"]
    assert gaps, "a three-candle imbalance must be detected"
    gap = gaps[-1]
    assert gap["low"] < gap["high"]
    assert gap["low"] == pytest.approx(candles[10].high, 6)
    assert gap["high"] == pytest.approx(candles[12].low, 6)
    assert gap["filled"] is False
    assert gap["live"] is True


def test_fvg_marks_a_gap_as_filled_once_price_trades_back_through_it():
    closes = [100.0] * 11 + [100.5, 108.0, 105.0, 101.0]
    candles = make_candles(closes)
    candles[12] = Candle(dt=candles[12].dt, open=107.5, high=109.0, low=107.0, close=108.0, volume=1000, symbol="T", source="t")
    candles[14].low = 99.0  # trades back below the gap
    gap = [g for g in ict_engine.fair_value_gaps(candles) if g["kind"] == "bullish"][-1]
    assert gap["filled"] is True
    assert gap["filled_index"] == 14
    assert gap["live"] is False


def test_liquidity_sweep_needs_a_close_back_inside_the_range():
    # a clear swing high, then one bar that spikes through it and closes below
    closes = [100.0, 101.0, 105.0, 104.0, 100.0, 101.0, 102.0]
    candles = make_candles(closes)
    candles[2].high = 106.0  # the swing high
    candles[6] = Candle(dt=candles[6].dt, open=102.0, high=107.0, low=101.5, close=103.0, volume=1000, symbol="T", source="t")
    sweeps = [s for s in ict_engine.liquidity_sweeps(candles) if s["kind"] == "buy_side"]
    assert sweeps
    assert sweeps[-1]["direction"] == -1
    assert sweeps[-1]["level"] == pytest.approx(106.0)
    assert sweeps[-1]["wick"] > 0


def test_dealing_range_splits_premium_and_discount_around_equilibrium():
    candles = synthetic_candles("DEALING", "D1", 300)
    dealing = ict_engine.dealing_range(candles)
    assert dealing
    assert dealing["low"] <= dealing["equilibrium"] <= dealing["high"]
    assert dealing["ote_low"] < dealing["ote_high"] <= dealing["high"]
    assert dealing["zone"] in ("premium", "discount")
    assert isinstance(dealing["in_ote"], bool)


def test_order_blocks_are_the_opposite_colour_candle_before_the_move():
    candles = synthetic_candles("OBTEST", "D1", 400)
    blocks = ict_engine.order_blocks(candles)
    assert blocks
    for block in blocks:
        candle = candles[block["index"]]
        if block["kind"] == "bullish":
            assert candle.is_bearish
        else:
            assert candle.is_bullish
        assert block["low"] < block["high"]
        assert isinstance(block["live"], bool)


def test_ict_overlay_only_ships_live_structures():
    candles = synthetic_candles("ICTOVERLAY", "D1", 400)
    overlay = ict_engine.ict_overlay(candles)
    assert set(overlay) >= {"order_blocks", "fvg", "sweeps", "dealing_range"}
    assert all(b["live"] for b in overlay["order_blocks"])
    assert all(g["live"] for g in overlay["fvg"])


# ---------------------------------------------------------------------------
# ACT
# ---------------------------------------------------------------------------


def test_act_state_describes_the_range_it_is_measuring():
    # downtrend -> tight range with thin volume -> breakout on volume
    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] * 0.995)
    base = closes[-1]
    for i in range(40):
        closes.append(base * (1 + 0.002 * ((-1) ** i)))
    for _ in range(12):
        closes.append(closes[-1] * 1.02)
    candles = make_candles(closes)
    for i, candle in enumerate(candles):
        if 60 <= i < 100:
            candle.volume = 200_000.0
        elif i >= 100:
            candle.volume = 3_000_000.0
    state = act_engine.act_analysis(candles, ind=compute_all(candles, "D1"))
    assert state["long"]["phase"] in act_engine.PHASE_FA
    assert state["long"]["range_high"] > state["long"]["range_low"]
    assert state["long"]["range_bars"] > 0
    assert state["long"]["phase_fa"]
    assert {"long", "short", "active", "params"} <= set(state)


def test_act_historical_signals_respect_the_noise_floor_and_the_rr_minimum():
    candles = synthetic_candles("ACTRISK", "D1", 600)
    signals = act_engine.historical_act_signals(candles, ind=compute_all(candles, "D1"))
    assert signals
    for signal in signals:
        assert signal["risk"] > 0.0025 * signal["entry"], "a stop tighter than 0.25% of price is noise"
        assert signal["rr"] >= act_engine.DEFAULTS["min_rr"] - 1e-9
        assert signal["phase"] in act_engine.PHASE_FA
        assert signal["reasons"]


def test_act_is_long_only_by_default():
    candles = synthetic_candles("ACTSHORT", "D1", 600)
    ind = compute_all(candles, "D1")
    long_only = act_engine.historical_act_signals(candles, allow_short=False, ind=ind)
    both = act_engine.historical_act_signals(candles, allow_short=True, ind=ind)
    assert all(s["direction"] > 0 for s in long_only)
    assert any(s["direction"] < 0 for s in both)


def test_act_overlay_draws_the_range_box_only_when_a_range_is_active():
    candles = synthetic_candles("ACTOVERLAY", "D1", 400)
    state = act_engine.act_analysis(candles, ind=compute_all(candles, "D1"))
    overlay = act_engine.act_overlay(state, candles)
    if state["active"]:
        assert len(overlay) == 1
        assert overlay[0]["y0"] < overlay[0]["y1"]
        assert overlay[0]["phase"] == state["active"]["phase"]
    else:
        assert overlay == []


# ---------------------------------------------------------------------------
# patterns, structure and the shared signal contract
# ---------------------------------------------------------------------------


def test_pattern_detection_finds_known_shapes():
    candles = make_candles([100.0] * 10)
    candles.append(Candle(dt=candles[-1].dt, open=100.0, high=100.5, low=94.0, close=100.4, volume=1000, symbol="T", source="t"))
    assert "hammer" in {p["pattern"] for p in detect_patterns(candles)}

    candles2 = make_candles([100.0] * 10)
    candles2[-1] = Candle(dt=candles2[-1].dt, open=100.0, high=100.2, low=98.0, close=98.5, volume=1000, symbol="T", source="t")
    candles2.append(Candle(dt=candles2[-1].dt, open=98.0, high=101.0, low=97.5, close=100.8, volume=1000, symbol="T", source="t"))
    assert "bullish_engulfing" in {p["pattern"] for p in detect_patterns(candles2)}


def test_market_structure_labels_the_sequence_and_reports_bos_choch():
    candles = synthetic_candles("STRUCT", "D1", 300)
    structure = market_structure(candles)
    assert structure["structure"]
    assert isinstance(structure["events"], list)
    for event in structure["events"]:
        assert event["type"] in ("BOS", "CHoCH")
        assert event["level"] > 0


def test_support_resistance_is_ranked_by_number_of_touches():
    candles = synthetic_candles("SRLEVELS", "D1", 300)
    levels = support_resistance(candles)
    assert levels
    touches = [lv["touches"] for lv in levels]
    assert touches == sorted(touches, reverse=True)
    for level in levels:
        assert level["kind"] in ("support", "resistance")
        assert level["price"] > 0


def test_every_signal_from_every_engine_is_actionable_and_explained():
    candles = synthetic_candles("CONTRACT", "D1", 600)
    ind = compute_all(candles, "D1")
    zones = rtm_engine.build_zones(candles)
    signals = []
    signals += scan_setups(ind, candles)
    signals += rtm_engine.historical_zone_signals(candles, zones, ind=ind)
    signals += act_engine.historical_act_signals(candles, ind=ind)
    signals += ict_engine.ict_signals(candles)
    assert len(signals) > 20
    engines = {s["engine"] for s in signals}
    assert {"setup", "rtm", "act"} <= engines
    for signal in signals:
        assert signal["entry"] > 0 and signal["stop"] > 0
        assert not math.isnan(signal["entry"]) and not math.isnan(signal["stop"])
        assert signal["reasons"] and all(isinstance(r, str) and r for r in signal["reasons"])
        assert signal["setup"] and signal["setup_name"]
        assert signal["entry_kind"] in ("market", "limit")
        if signal["direction"] > 0:
            assert signal["stop"] < signal["entry"]
            assert signal["target"] is None or signal["target"] > signal["entry"]
        else:
            assert signal["stop"] > signal["entry"]
            assert signal["target"] is None or signal["target"] < signal["entry"]


def test_no_short_signals_are_produced_for_equity():
    candles = synthetic_candles("46348559193224090", "D1", 600)
    ind = compute_all(candles, "D1")
    assert all(s["direction"] > 0 for s in scan_setups(ind, candles, allow_short=False))
    assert any(s["direction"] < 0 for s in scan_setups(ind, candles, allow_short=True))
