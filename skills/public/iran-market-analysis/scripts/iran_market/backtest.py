"""Event-driven backtester that *measures* the win rate of every setup.

Nothing in this skill claims a win rate as a property of a strategy.  A number
like "80%" is only ever the historical result on the bars that were loaded, and
it is reported together with the sample size, the exit-reason distribution and
an explicit "نمونه کم" flag when there are too few trades to mean anything.

Execution model (deliberately pessimistic):

* a signal on bar *i* can only be filled from bar *i+1* onwards;
* market setups fill at the **open** of the next bar, limit setups fill at the
  limit price only if a later bar actually trades through it (within
  ``entry_window`` bars), otherwise the trade expires unfilled;
* if a bar's range covers both the stop and the target, the **stop** is assumed
  to have been hit first;
* commission + slippage are charged on both sides and expressed in R;
* positions never overlap inside one setup's run, so a setup cannot be credited
  with trades it could not have taken.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .core import Candle


@dataclass(slots=True)
class BacktestConfig:
    max_bars: int = 60
    entry_window: int = 10
    allow_short: bool = False
    fees_bps: float = 6.0  # TSE commission on each side
    slippage_bps: float = 5.0
    overlap: bool = False  # when False only one open trade per setup
    partial_at_1r: bool = False  # take half off at +1R, trail the rest to the target
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Trade:
    setup: str
    engine: str
    direction: int
    signal_index: int
    entry_index: int
    exit_index: int
    entry: float
    stop: float
    target: float
    exit_price: float
    r: float
    return_pct: float
    bars_held: int
    outcome: str  # win | loss | timeout | expired
    exit_reason: str
    dt_signal: str = ""
    dt_exit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cost_r(entry: float, risk: float, config: BacktestConfig) -> float:
    if risk <= 0:
        return 0.0
    cost_price = entry * (config.fees_bps + config.slippage_bps) / 10_000.0
    return 2.0 * cost_price / risk


def simulate_signal(candles: Sequence[Candle], signal: dict, config: BacktestConfig) -> Trade | None:
    """Simulate one signal forward through the bars.  Returns None when expired."""
    index = signal["index"]
    direction = int(signal.get("direction", 1))
    entry_price = float(signal["entry"])
    stop = float(signal["stop"])
    target = float(signal["target"])
    risk = abs(entry_price - stop)
    if risk <= 0 or index + 1 >= len(candles):
        return None
    if risk < 0.0025 * entry_price:
        # a stop tighter than a quarter of a percent is measurement noise, not a
        # trade; scoring it would dominate the win rate with fake -300R outcomes
        return None
    if direction < 0 and not config.allow_short:
        return None

    fill_index = None
    fill_price = None
    if signal.get("entry_kind", "market") == "market" or abs(entry_price - candles[index].close) / entry_price < 1e-6:
        fill_index = index + 1
        fill_price = candles[fill_index].open
    else:
        for j in range(index + 1, min(index + 1 + config.entry_window, len(candles))):
            bar = candles[j]
            if direction > 0 and bar.low <= entry_price <= bar.high:
                fill_index, fill_price = j, entry_price
                break
            if direction < 0 and bar.low <= entry_price <= bar.high:
                fill_index, fill_price = j, entry_price
                break
        if fill_index is None:
            return Trade(
                setup=signal.get("setup", signal.get("type", "")),
                engine=signal.get("engine", ""),
                direction=direction,
                signal_index=index,
                entry_index=-1,
                exit_index=-1,
                entry=entry_price,
                stop=stop,
                target=target,
                exit_price=entry_price,
                r=0.0,
                return_pct=0.0,
                bars_held=0,
                outcome="expired",
                exit_reason="ورود انجام نشد",
                dt_signal=signal.get("dt", ""),
            )

    costs = _cost_r(fill_price, risk, config)
    end = min(fill_index + config.max_bars, len(candles) - 1)
    for j in range(fill_index, end + 1):
        bar = candles[j]
        if direction > 0:
            stopped = bar.low <= stop
            targeted = bar.high >= target
        else:
            stopped = bar.high >= stop
            targeted = bar.low <= target
        if stopped:
            exit_price = stop if j > fill_index else min(stop, bar.open) if direction > 0 else max(stop, bar.open)
            return _trade(candles, signal, direction, fill_index, fill_price, j, exit_price, risk, costs, "loss", "استاپ")
        if targeted:
            exit_price = target if j > fill_index else max(target, bar.open) if direction > 0 else min(target, bar.open)
            return _trade(candles, signal, direction, fill_index, fill_price, j, exit_price, risk, costs, "win", "هدف")
    exit_price = candles[end].close
    r = direction * (exit_price - fill_price) / risk - costs
    return _trade(candles, signal, direction, fill_index, fill_price, end, exit_price, risk, costs, "win" if r > 0 else "loss", "پایان زمان", forced_r=r)


def _trade(
    candles: Sequence[Candle],
    signal: dict,
    direction: int,
    fill_index: int,
    fill_price: float,
    exit_index: int,
    exit_price: float,
    risk: float,
    costs: float,
    outcome: str,
    exit_reason: str,
    forced_r: float | None = None,
) -> Trade:
    r = forced_r if forced_r is not None else direction * (exit_price - fill_price) / risk - costs
    return Trade(
        setup=signal.get("setup", signal.get("type", "")),
        engine=signal.get("engine", ""),
        direction=direction,
        signal_index=signal["index"],
        entry_index=fill_index,
        exit_index=exit_index,
        entry=round(fill_price, 6),
        stop=round(signal["stop"], 6),
        target=round(signal["target"], 6),
        exit_price=round(exit_price, 6),
        r=round(r, 4),
        return_pct=round(100.0 * direction * (exit_price - fill_price) / fill_price, 4) if fill_price else 0.0,
        bars_held=exit_index - fill_index,
        outcome=outcome,
        exit_reason=exit_reason,
        dt_signal=signal.get("dt", ""),
        dt_exit=candles[exit_index].dt.isoformat() if exit_index < len(candles) else "",
    )


def run_backtest(candles: Sequence[Candle], signals: Sequence[dict], config: BacktestConfig | None = None) -> dict[str, Any]:
    """Simulate every signal and aggregate per setup + overall."""
    config = config or BacktestConfig()
    trades: list[Trade] = []
    expired = 0
    busy_until: dict[str, int] = {}
    for signal in sorted(signals, key=lambda s: s["index"]):
        setup_id = signal.get("setup", signal.get("type", "unknown"))
        if not config.overlap and setup_id in busy_until and signal["index"] < busy_until[setup_id]:
            continue
        trade = simulate_signal(candles, signal, config)
        if trade is None:
            continue
        if trade.outcome == "expired":
            expired += 1
            continue
        trades.append(trade)
        busy_until[setup_id] = trade.exit_index
    if not trades:
        return {"config": config.to_dict(), "trades": [], "overall": _metrics([], expired, candles), "by_setup": {}}

    by_setup: dict[str, list[Trade]] = {}
    for trade in trades:
        by_setup.setdefault(trade.setup, []).append(trade)
    return {
        "config": config.to_dict(),
        "trades": [t.to_dict() for t in trades],
        "overall": _metrics(trades, expired, candles),
        "by_setup": {setup: _metrics(items, 0, candles) for setup, items in sorted(by_setup.items())},
        "equity": _equity_curve(trades),
    }


def _metrics(trades: Sequence[Trade], expired: int, candles: Sequence[Candle]) -> dict[str, Any]:
    total = len(trades)
    if total == 0:
        return {
            "trades": 0,
            "expired": expired,
            "win_rate": None,
            "expectancy_r": None,
            "profit_factor": None,
            "avg_win_r": None,
            "avg_loss_r": None,
            "max_drawdown_r": None,
            "avg_bars": None,
            "sample_ok": False,
        }
    wins = [t for t in trades if t.r > 0]
    losses = [t for t in trades if t.r <= 0]
    gross_win = sum(t.r for t in wins)
    gross_loss = abs(sum(t.r for t in losses))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in trades:
        equity += trade.r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": total,
        "expired": expired,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / total, 4),
        "avg_win_r": round(gross_win / len(wins), 4) if wins else 0.0,
        "avg_loss_r": round(-gross_loss / len(losses), 4) if losses else 0.0,
        "expectancy_r": round(sum(t.r for t in trades) / total, 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "max_drawdown_r": round(max_dd, 4),
        "avg_bars": round(sum(t.bars_held for t in trades) / total, 2),
        "best_r": round(max(t.r for t in trades), 3),
        "worst_r": round(min(t.r for t in trades), 3),
        "exit_reasons": _histogram(t.exit_reason for t in trades),
        "directions": _histogram("long" if t.direction > 0 else "short" for t in trades),
        "total_r": round(sum(t.r for t in trades), 3),
        "sample_ok": total >= 20,
        "bars_available": len(candles),
    }


def _histogram(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _equity_curve(trades: Sequence[Trade]) -> list[dict[str, Any]]:
    curve: list[dict[str, Any]] = []
    equity = 0.0
    for trade in trades:
        equity += trade.r
        curve.append({"dt": trade.dt_exit, "setup": trade.setup, "r": round(trade.r, 4), "equity_r": round(equity, 4)})
    return curve


def sharpe_r(trades: Sequence[Trade], bars_per_year: int = 250) -> float | None:
    """Annualised Sharpe of the per-trade R series (a rough but honest figure)."""
    if len(trades) < 5:
        return None
    returns = [t.r for t in trades]
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if var <= 0:
        return None
    stdev = var**0.5
    trades_per_year = max(1.0, len(trades) / max(1.0, bars_per_year / 250.0)) if bars_per_year else len(trades)
    return round(mean / stdev * (trades_per_year**0.5), 3)


__all__ = ["BacktestConfig", "Trade", "run_backtest", "sharpe_r", "simulate_signal"]
