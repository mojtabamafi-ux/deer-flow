"""Backtester tests.

The win rate the dashboard quotes is only meaningful if the simulation is
honest, so these tests pin the pessimistic rules: fills happen *after* the
signal bar, limit orders must be traded through, a stop and target hit on the
same bar count as the stop, degenerate risk is skipped, and costs are charged.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from iran_market.backtest import BacktestConfig, run_backtest, sharpe_r, simulate_signal
from iran_market.core import Candle
from iran_market.data.sample import synthetic_candles
from iran_market.indicators import compute_all
from iran_market.setups import scan_setups, setup_ranking

START = datetime(2024, 1, 1, 12, 30)


def path_candles(values: list[float]) -> list[Candle]:
    """Candles whose high/low hug the open/close so fills are fully determined."""
    candles = []
    for i, close in enumerate(values):
        open_ = values[i - 1] if i else close
        candles.append(
            Candle(
                dt=START + timedelta(days=i),
                open=open_,
                high=max(open_, close),
                low=min(open_, close),
                close=close,
                volume=1_000_000.0,
                value=1_000_000.0 * close,
                symbol="BT",
                source="test",
                timeframe="D1",
            )
        )
    return candles


def signal(index: int, entry: float, stop: float, target: float, direction: int = 1, setup: str = "test_setup", entry_kind: str = "market") -> dict:
    return {
        "index": index,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "setup": setup,
        "engine": "test",
        "entry_kind": entry_kind,
        "strength": 0.6,
        "confidence": 0.6,
        "dt": (START + timedelta(days=index)).isoformat(),
    }


FREE = BacktestConfig(fees_bps=0, slippage_bps=0)


def test_market_entry_fills_at_the_next_open():
    candles = path_candles([100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 112.0])
    candles[6].open = 102.0  # the bar after the signal opens at 102
    trade = simulate_signal(candles, signal(5, 100.0, 95.0, 112.0), FREE)
    assert trade is not None
    assert trade.outcome == "win"
    assert trade.exit_reason == "هدف"
    assert trade.entry == pytest.approx(102.0)
    assert trade.entry_index == 6


def test_a_stop_and_target_on_the_same_bar_counts_as_the_stop():
    candles = path_candles([100.0, 100.0])
    candles[1] = Candle(dt=candles[1].dt, open=100.0, high=112.0, low=88.0, close=100.0, volume=1.0, value=1.0, symbol="BT", source="test")
    trade = simulate_signal(candles, signal(0, 100.0, 90.0, 112.0), FREE)
    assert trade is not None
    assert trade.outcome == "loss"
    assert trade.exit_reason == "استاپ"
    assert trade.r < 0


def test_a_stop_hit_before_the_target_is_a_one_R_loss():
    candles = path_candles([100.0, 100.5, 97.0, 95.0, 94.0, 93.0])
    trade = simulate_signal(candles, signal(0, 100.0, 98.0, 112.0), FREE)
    assert trade is not None
    assert trade.outcome == "loss"
    assert trade.exit_price == pytest.approx(98.0)
    assert trade.r == pytest.approx(-1.0)


def test_a_limit_entry_expires_when_never_traded_through():
    candles = path_candles([100.0] * 20)  # entry 95 is below every bar's low
    trade = simulate_signal(candles, signal(0, 95.0, 90.0, 110.0, entry_kind="limit"), BacktestConfig(fees_bps=0, slippage_bps=0, entry_window=5))
    assert trade is not None
    assert trade.outcome == "expired"
    assert trade.entry_index == -1
    assert trade.r == 0.0


def test_a_limit_entry_fills_on_the_pullback_that_trades_through_it():
    candles = path_candles([100.0, 100.0, 100.0, 100.0, 99.0, 94.0, 96.0, 104.0, 110.0, 111.0])
    candles[5].low = 93.0  # the limit at 95 is traded through on bar 5
    trade = simulate_signal(candles, signal(0, 95.0, 90.0, 110.0, entry_kind="limit"), FREE)
    assert trade is not None
    assert trade.outcome == "win"
    assert trade.entry_index == 5
    assert trade.entry == pytest.approx(95.0)


def test_a_trade_closes_at_max_bars_when_neither_level_is_hit():
    candles = path_candles([100.0] + [100.5] * 40)
    trade = simulate_signal(candles, signal(0, 100.0, 80.0, 200.0), BacktestConfig(fees_bps=0, slippage_bps=0, max_bars=10))
    assert trade is not None
    assert trade.exit_reason == "پایان زمان"
    assert trade.bars_held == 10
    assert trade.exit_index == 11


def test_costs_reduce_the_measured_result():
    candles = path_candles([100.0, 101.0, 104.0, 108.0])
    free = simulate_signal(candles, signal(0, 100.0, 95.0, 108.0), FREE)
    costly = simulate_signal(candles, signal(0, 100.0, 95.0, 108.0), BacktestConfig(fees_bps=60, slippage_bps=40))
    assert free is not None and costly is not None
    assert costly.r < free.r
    assert free.exit_price == costly.exit_price  # only the cost differs


def test_a_degenerate_stop_is_skipped_instead_of_exploding_the_win_rate():
    """A 0.002% stop once turned one tick into -336 R and dragged expectancy to -6.4 R."""
    candles = path_candles([100.0, 100.05, 99.9, 100.2, 100.1])
    assert simulate_signal(candles, signal(0, 100.0, 99.998, 101.0), FREE) is None


def test_shorts_are_rejected_when_shorting_is_disabled():
    candles = path_candles([100.0, 99.0, 95.0, 90.0])
    short = signal(0, 100.0, 105.0, 90.0, direction=-1)
    assert simulate_signal(candles, short, BacktestConfig(allow_short=False, fees_bps=0, slippage_bps=0)) is None
    allowed = simulate_signal(candles, short, BacktestConfig(allow_short=True, fees_bps=0, slippage_bps=0))
    assert allowed is not None and allowed.r > 0


def test_overlap_off_allows_only_one_open_trade_per_setup():
    candles = path_candles([100.0] + [100.5] * 30 + [112.0])
    signals = [signal(i, 100.0, 80.0, 112.0) for i in (0, 1, 2)]
    strict = run_backtest(candles, signals, BacktestConfig(fees_bps=0, slippage_bps=0, max_bars=30, overlap=False))
    loose = run_backtest(candles, signals, BacktestConfig(fees_bps=0, slippage_bps=0, max_bars=30, overlap=True))
    assert strict["overall"]["trades"] == 1
    assert loose["overall"]["trades"] == 3


def test_win_rate_expectancy_and_profit_factor_are_consistent():
    candles = path_candles([100.0, 101.0] + [105.0] * 20)
    result = run_backtest(candles, [signal(0, 100.0, 95.0, 104.0), signal(1, 100.0, 95.0, 104.0)], BacktestConfig(fees_bps=0, slippage_bps=0, overlap=True))
    overall = result["overall"]
    assert overall["trades"] == 2
    assert overall["wins"] == 2 and overall["losses"] == 0
    assert overall["win_rate"] == pytest.approx(1.0)
    assert overall["profit_factor"] is None  # no losing R means no finite profit factor
    assert overall["expectancy_r"] > 0
    assert overall["sample_ok"] is False, "two trades must never be reported as a reliable sample"


def test_metrics_aggregate_exactly():
    # two deterministic winners and one deterministic stop-out, simulated independently
    candles = path_candles([100.0, 101.0, 105.0, 111.0, 112.0, 100.0, 90.0])
    signals = [
        signal(0, 100.0, 95.0, 110.0, setup="a"),
        signal(1, 101.0, 96.0, 111.0, setup="b"),
        signal(4, 111.0, 103.0, 130.0, setup="c"),
    ]
    result = run_backtest(candles, signals, FREE)
    overall = result["overall"]
    assert overall["trades"] == 3
    assert overall["wins"] == 2 and overall["losses"] == 1
    assert overall["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert overall["total_r"] == pytest.approx(sum(t["r"] for t in result["trades"]), abs=1e-4)
    assert overall["expectancy_r"] == pytest.approx(overall["total_r"] / 3, abs=1e-4)
    assert overall["max_drawdown_r"] >= 0
    assert sorted(result["by_setup"]) == ["a", "b", "c"]
    assert result["by_setup"]["c"]["win_rate"] == 0.0


def test_an_empty_backtest_returns_zero_trades_without_crashing():
    candles = synthetic_candles("EMPTY", "D1", 300)
    result = run_backtest(candles, [], BacktestConfig())
    assert result["overall"]["trades"] == 0
    assert result["overall"]["win_rate"] is None
    assert result["overall"]["profit_factor"] is None
    assert result["overall"]["sample_ok"] is False
    assert result["by_setup"] == {}
    assert result["trades"] == []


def test_by_setup_is_keyed_and_rankable_with_a_small_sample_grade():
    candles = synthetic_candles("RANKTEST", "D1", 600)
    ind = compute_all(candles, "D1")
    signals = scan_setups(ind, candles)
    assert signals, "the sample series must produce setup signals"
    result = run_backtest(candles, signals)
    assert isinstance(result["by_setup"], dict)
    ranking = setup_ranking(result["by_setup"])
    assert ranking
    wins = [row["win_rate"] for row in ranking if row["win_rate"] is not None]
    assert wins == sorted(wins, reverse=True)
    for row in ranking:
        assert row["grade"] in ("A", "B", "C", "D", "نمونه کم")
        assert row["name"]
        if row["trades"] < 20:
            assert row["grade"] == "نمونه کم", "fewer than 20 trades must be flagged as a small sample"


def test_equity_curve_and_exit_reason_histograms():
    candles = synthetic_candles("METRICS", "D1", 600)
    result = run_backtest(candles, scan_setups(compute_all(candles, "D1"), candles))
    overall = result["overall"]
    assert overall["trades"] > 0
    assert overall["max_drawdown_r"] >= 0
    assert len(result["equity"]) == overall["trades"]
    assert result["equity"][-1]["equity_r"] == pytest.approx(sum(t["r"] for t in result["trades"]), abs=1e-3)
    assert set(overall["exit_reasons"]) <= {"هدف", "استاپ", "پایان زمان"}
    assert set(overall["directions"]) <= {"long", "short"}


def test_sharpe_is_none_on_tiny_or_constant_samples():
    assert sharpe_r([]) is None
    assert sharpe_r([SimpleNamespace(r=1.0)] * 4) is None  # fewer than 5 trades
    assert sharpe_r([SimpleNamespace(r=1.0)] * 10) is None  # zero variance
    value = sharpe_r([SimpleNamespace(r=r) for r in (1.0, -0.5, 0.8, -0.2, 1.4, -0.6, 0.9)])
    assert value is not None and math.isfinite(value)
