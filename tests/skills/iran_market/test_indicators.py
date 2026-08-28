"""Indicator correctness tests.

These pin the *definitions*, not just the shapes: a wrong smoothing constant or
a wrong warm-up convention silently changes every trading decision, so the
reference values are derived independently of the implementation (hand-computed
EMA, the canonical Wilder RSI dataset, ``statistics.fmean`` for SMA).
"""

from __future__ import annotations

import math
import statistics

import pytest
from iran_market import indicators as ta
from iran_market.core import Candle, resample
from iran_market.data.sample import synthetic_candles

#: Canonical Wilder dataset (the StockCharts RSI worked example).  RSI(14) at
#: the 15th value is 70.46 - the number every reference implementation reproduces.
WILDER_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
    46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
    43.42, 42.66, 43.13,
]


def test_ema_is_sma_seeded_and_matches_hand_computation():
    values = [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    result = ta.ema(values, 3)
    # warm-up is NaN, the seed is the SMA of the first 3 values, alpha = 2/(3+1)
    assert math.isnan(result[0]) and math.isnan(result[1])
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(3.0)
    assert result[9] == pytest.approx(9.0)


def test_ema_seeds_after_a_nan_prefix():
    """A MACD line is NaN until the slow EMA starts; its signal line must seed after it."""
    macd_line = [float("nan")] * 5 + [1.0, 2, 3, 4, 5, 6, 7, 8]
    signal = ta.ema(macd_line, 3)
    assert all(math.isnan(v) for v in signal[:7])
    assert signal[7] == pytest.approx(2.0)


def test_rma_uses_wilder_smoothing():
    result = ta.rma([10.0] * 5 + [20.0], 5)
    assert result[4] == pytest.approx(10.0)
    assert result[5] == pytest.approx(10.0 + (20.0 - 10.0) / 5)


def test_rsi_matches_the_wilder_reference_dataset():
    result = ta.rsi(WILDER_CLOSES, 14)
    assert all(math.isnan(v) for v in result[:14])
    assert result[14] == pytest.approx(70.46, abs=0.02)
    assert result[15] == pytest.approx(66.25, abs=0.05)


def test_rsi_stays_inside_0_100():
    result = ta.rsi([float(i) for i in range(100)], 14)
    assert all(math.isnan(v) or 0.0 <= v <= 100.0 for v in result)


def test_sma_matches_statistics_fmean():
    result = ta.sma(WILDER_CLOSES, 5)
    assert result[4] == pytest.approx(statistics.fmean(WILDER_CLOSES[:5]))
    assert result[-1] == pytest.approx(statistics.fmean(WILDER_CLOSES[-5:]))
    assert all(math.isnan(v) for v in result[:4])


def test_macd_line_is_the_ema_difference():
    closes = [float(i) for i in range(60)]
    result = ta.macd(closes, 12, 26, 9)
    fast, slow = ta.ema(closes, 12), ta.ema(closes, 26)
    compared = 0
    for i, line in enumerate(result["line"]):
        if not math.isnan(line):
            assert line == pytest.approx(fast[i] - slow[i])
            compared += 1
    assert compared > 10
    assert len(result["hist"]) == len(closes)


def test_bollinger_bands_are_ordered_around_the_middle():
    closes = [100 + math.sin(i / 3.0) * 5 for i in range(120)]
    bb = ta.bollinger(closes, 20, 2.0)
    for i in range(19, len(closes)):
        assert bb["lower"][i] <= bb["mid"][i] <= bb["upper"][i]
        assert bb["bandwidth"][i] > 0
    assert bb["mid"][19] == pytest.approx(statistics.fmean(closes[:20]))


def test_atr_is_positive_and_bounded_by_the_true_range():
    candles = synthetic_candles("ATRTEST", "D1", 200)
    high = [c.high for c in candles]
    low = [c.low for c in candles]
    close = [c.close for c in candles]
    result = ta.atr(high, low, close, 14)
    valid = [v for v in result if not math.isnan(v)]
    assert valid and all(v > 0 for v in valid)
    assert max(valid) <= max(ta.true_range(high, low, close)) + 1e-9


def test_adx_and_di_are_in_their_valid_ranges():
    candles = synthetic_candles("ADXTEST", "D1", 200)
    result = ta.adx([c.high for c in candles], [c.low for c in candles], [c.close for c in candles], 14)
    triples = [(a, p, m) for a, p, m in zip(result["adx"], result["plus_di"], result["minus_di"]) if not math.isnan(a)]
    assert triples
    for adx_value, plus, minus in triples:
        assert 0.0 <= adx_value <= 100.0
        assert plus >= 0.0 and minus >= 0.0


def test_supertrend_direction_is_discrete():
    candles = synthetic_candles("STTEST", "D1", 300)
    result = ta.supertrend([c.high for c in candles], [c.low for c in candles], [c.close for c in candles], 10, 3.0)
    assert set(result["direction"]) <= {-1, 0, 1}
    assert result["direction"][-1] in (-1, 1)
    assert len(result["line"]) == len(candles)


def test_zigzag_alternates_and_only_ever_marks_the_last_pivot_provisional():
    candles = synthetic_candles("ZZTEST", "D1", 300)
    pivots = ta.zigzag([c.high for c in candles], [c.low for c in candles], [c.close for c in candles], 6.0)
    assert len(pivots) >= 4
    kinds = [p["kind"] for p in pivots]
    assert all(a != b for a, b in zip(kinds, kinds[1:])), "zigzag pivots must alternate high/low"
    assert all(pivots[i]["index"] < pivots[i + 1]["index"] for i in range(len(pivots) - 1))
    assert all(not p.get("provisional") for p in pivots[:-1]), "only the trailing pivot may be provisional"


def test_zigzag_rejects_moves_smaller_than_the_threshold():
    candles = synthetic_candles("ZZTHR", "D1", 300)
    closes = [c.close for c in candles]
    small = ta.zigzag([c.high for c in candles], [c.low for c in candles], closes, 40.0)
    big = ta.zigzag([c.high for c in candles], [c.low for c in candles], closes, 4.0)
    assert len(small) <= len(big)


def test_volume_profile_poc_sits_inside_the_value_area():
    candles = synthetic_candles("VPTEST", "D1", 200)
    profile = ta.volume_profile(candles, bins=40)
    assert len(profile["bins"]) == 40
    assert min(c.low for c in candles) <= profile["poc"] <= max(c.high for c in candles)
    assert profile["val"] <= profile["poc"] <= profile["vah"]
    assert sum(b["volume"] for b in profile["bins"]) == pytest.approx(sum(c.volume for c in candles), rel=1e-6)


def test_ichimoku_cloud_is_shifted_forward_by_26_bars():
    candles = synthetic_candles("ICHITEST", "D1", 200)
    result = ta.ichimoku([c.high for c in candles], [c.low for c in candles], [c.close for c in candles])
    assert all(math.isnan(v) for v in result["senkou_a"][:26])
    assert not math.isnan(result["senkou_a"][-1])
    assert result["cloud_top"][-1] >= result["cloud_bottom"][-1]


def test_compute_all_series_all_match_the_candle_length():
    candles = synthetic_candles("LENTEST", "D1", 250)
    ind = ta.compute_all(candles, "D1")
    assert len(ind["close"]) == len(candles)
    assert len(ind["rsi14"]) == len(ind["atr14"]) == len(ind["ema20"]) == len(candles)
    assert len(ind["macd"]["hist"]) == len(ind["supertrend"]["direction"]) == len(ind["psar"]["sar"]) == len(candles)


def test_snapshot_is_complete_and_finite():
    candles = synthetic_candles("SNAPTEST", "D1", 300)
    snap = ta.snapshot(ta.compute_all(candles, "D1"))
    assert snap["price"]["close"] == pytest.approx(candles[-1].close)
    assert snap["trend"]["label"] in ("صعودی", "نزولی", "خنثی / رنج")
    assert snap["momentum"]["rsi_state"] in ("overbought", "oversold", "bullish", "bearish", "neutral")
    for group in ("price", "trend", "momentum", "volatility", "volume", "levels"):
        assert snap[group], group
    for key in ("atr14", "bb_bandwidth", "hv20"):
        value = snap["volatility"][key]
        assert value is None or not math.isnan(value)
    for key in ("ema200", "supertrend", "psar"):
        assert key in snap["levels"]


def test_resample_daily_to_weekly_aggregates_correctly():
    candles = synthetic_candles("WEEKTEST", "D1", 60)
    weekly = resample(candles, "W1")
    assert 1 < len(weekly) < len(candles)
    assert weekly[-1].close == candles[-1].close
    assert weekly[0].high <= max(c.high for c in candles) + 1e-9
    assert weekly[0].low >= min(c.low for c in candles) - 1e-9
    # volume is additive, close is last, high/low are extremes
    assert weekly[0].volume == pytest.approx(sum(c.volume for c in candles if c.dt.isocalendar()[1] == weekly[0].dt.isocalendar()[1]))


def test_pivot_points_classic_relationships():
    levels = ta.pivot_points([120.0], [100.0], [110.0], "classic")
    assert levels["p"] == pytest.approx(110.0)
    assert levels["r1"] == pytest.approx(120.0)
    assert levels["s1"] == pytest.approx(100.0)
    assert levels["r2"] > levels["r1"] > levels["p"] > levels["s1"] > levels["s2"]


def test_fibonacci_levels_are_ordered():
    fib = ta.fibonacci_levels(100.0, 200.0, "up")
    r = fib["retracement"]
    assert r["0.0"] == pytest.approx(100.0)
    assert r["0.382"] < r["0.5"] < r["0.618"] < r["1.0"] == pytest.approx(200.0)
    assert fib["extension"]["1.618"] == pytest.approx(261.8)
    down = ta.fibonacci_levels(200.0, 100.0, "down")
    assert down["retracement"]["0.618"] < down["retracement"]["0.382"]


def test_candle_properties():
    candle = Candle(dt=None, open=100, high=110, low=90, close=105)  # type: ignore[arg-type]
    assert candle.is_bullish and not candle.is_bearish
    assert candle.body == 5 and candle.range == 20
    assert candle.upper_shadow == 5 and candle.lower_shadow == 10
    assert candle.typical == pytest.approx((110 + 90 + 105) / 3)


def test_vwap_is_volume_weighted_and_anchored():
    candles = synthetic_candles("VWAPTEST", "D1", 60)
    result = ta.vwap(candles, anchor="session")
    assert len(result) == len(candles)
    valid = [v for v in result if not math.isnan(v)]
    assert valid
    assert min(c.low for c in candles) <= min(valid) and max(valid) <= max(c.high for c in candles) + 1e-6


def test_squeeze_flags_compression_and_release():
    from iran_market.core import Candle as _C

    def series(closes):
        return [
            _C(dt=None, open=v, high=v * 1.001, low=v * 0.999, close=v, volume=1.0, symbol="S", source="t")  # type: ignore[arg-type]
            for v in closes
        ]

    quiet = [100.0 + 0.01 * math.sin(i) for i in range(120)]
    quiet_squeeze = ta.compute_all(series(quiet), "D1")["squeeze"]["on"]
    assert any(quiet_squeeze), "a near-flat series must register a volatility squeeze"

    wild = [100.0 * (1.25 if i % 2 else 0.8) for i in range(120)]
    wild_squeeze = ta.compute_all(series(wild), "D1")["squeeze"]["on"]
    assert not all(wild_squeeze), "a violently oscillating series is never inside the squeeze"
