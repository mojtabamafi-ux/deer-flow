"""Core data structures shared by every engine of the iran-market-analysis skill.

The whole analysis stack is deliberately **dependency free**: only the Python
standard library is required.  ``fastapi``/``uvicorn``/``plotly`` are needed for
the interactive dashboard, and nothing else.

Every function in this package operates on plain ``list[float]`` series and on
lists of :class:`Candle`.  ``NAN`` is used for "not yet computable" so that the
warm-up period of every indicator stays explicit instead of being silently
back-filled with a wrong value.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

NAN = float("nan")
UNSET = float("inf")  # sentinel: "no extremum found yet" (zigzag leg reset)

#: Minutes per timeframe.  Used for resampling and for ATR annualisation.
TIMEFRAME_MINUTES: dict[str, int] = {
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H2": 120,
    "H4": 240,
    "D1": 1440,
    "W1": 10080,
    "MN1": 43200,
}

#: Timeframes that can be derived from another one (source -> targets).
RESAMPLE_TARGETS: dict[str, tuple[str, ...]] = {
    "M5": ("M15", "M30", "H1", "H4", "D1", "W1", "MN1"),
    "M15": ("M30", "H1", "H4", "D1", "W1", "MN1"),
    "M30": ("H1", "H4", "D1", "W1", "MN1"),
    "H1": ("H2", "H4", "D1", "W1", "MN1"),
    "H4": ("D1", "W1", "MN1"),
    "D1": ("W1", "MN1"),
}

TRADING_SESSION_MINUTES = 330  # TSETMC continuous session 09:00-12:30 (+ pre-open)
BARS_PER_YEAR: dict[str, int] = {
    "M5": 15840,
    "M15": 5280,
    "M30": 2640,
    "H1": 1320,
    "H2": 660,
    "H4": 330,
    "D1": 250,  # ~250 TSE trading days / year
    "W1": 50,
    "MN1": 12,
}


@dataclass(slots=True)
class Candle:
    """One OHLCV bar.

    ``value`` is the trade value in rials (TSETMC ``pDrCotVal``) and ``last`` the
    last traded price (``pDrCotVal`` vs ``PClosing`` distinction matters on the
    TSE, where the official close can differ from the final trade).
    """

    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    value: float = 0.0
    trades: int = 0
    last: float | None = None
    symbol: str = ""
    source: str = "sample"
    timeframe: str = "D1"
    synthetic: bool = False

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return max(self.high - self.low, 0.0)

    @property
    def upper_shadow(self) -> float:
        return self.high - max(self.close, self.open)

    @property
    def lower_shadow(self) -> float:
        return min(self.close, self.open) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["dt"] = self.dt.isoformat()
        return out


@dataclass(slots=True)
class Quote:
    """Snapshot of the latest market state for one instrument."""

    symbol: str
    name: str = ""
    market: str = ""
    last: float | None = None
    close: float | None = None
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    value: float | None = None
    trades: int | None = None
    status: str = ""
    as_of: datetime | None = None
    source: str = "sample"
    synthetic: bool = False

    @property
    def change(self) -> float | None:
        if self.last is None or not self.prev_close:
            return None
        return self.last - self.prev_close

    @property
    def change_pct(self) -> float | None:
        if self.change is None or not self.prev_close:
            return None
        return 100.0 * self.change / self.prev_close


@dataclass(slots=True)
class Instrument:
    """Static/semi-static instrument metadata."""

    code: str
    symbol: str
    name: str = ""
    market: str = ""
    exchange: str = ""
    sector: str = ""
    source: str = "sample"
    base_volume: float | None = None
    tick_size: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FetchResult:
    """Uniform envelope for every data-source call.

    ``mode`` is what actually produced the data: ``live`` (remote API answered),
    ``cache`` (served from the local cache), ``sample`` (deterministic synthetic
    fallback) or ``file`` (user supplied CSV/JSON).  The dashboard always renders
    this so nobody mistakes synthetic bars for market data.
    """

    data: Any = None
    mode: str = "live"
    source: str = ""
    ok: bool = True
    error: str = ""
    fetched_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source": self.source,
            "ok": self.ok,
            "error": self.error,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


# ---------------------------------------------------------------------------
# series helpers
# ---------------------------------------------------------------------------


def is_nan(x: float | None) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def last_valid(series: Sequence[float]) -> float:
    """Last non-NaN value of ``series`` (``NAN`` when there is none)."""
    for value in reversed(series):
        if not is_nan(value):
            return value
    return NAN


def first_valid_index(series: Sequence[float]) -> int:
    for i, value in enumerate(series):
        if not is_nan(value):
            return i
    return -1


def col(candles: Sequence[Candle], key: str) -> list[float]:
    """Extract one numeric column out of a candle list."""
    return [float(getattr(c, key) or 0.0) for c in candles]


def timestamps(candles: Sequence[Candle]) -> list[datetime]:
    return [c.dt for c in candles]


def iso_list(candles: Sequence[Candle]) -> list[str]:
    return [c.dt.isoformat() for c in candles]


def safe_round(value: float | None, digits: int = 4) -> float | None:
    if value is None or is_nan(value):
        return None
    return round(float(value), digits)


def pct_change(previous: float, current: float) -> float:
    if not previous:
        return 0.0
    return 100.0 * (current - previous) / previous


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def mean(values: Sequence[float]) -> float:
    vals = [v for v in values if not is_nan(v)]
    if not vals:
        return NAN
    return sum(vals) / len(vals)


def stdev(values: Sequence[float], sample: bool = True) -> float:
    """Sample (or population) standard deviation, matching ``pandas.Series.std``."""
    vals = [v for v in values if not is_nan(v)]
    n = len(vals)
    denom = (n - 1) if sample else n
    if n == 0 or denom <= 0:
        return NAN
    mu = sum(vals) / n
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / denom)


def rolling(series: Sequence[float], period: int, fn: Callable[[Sequence[float]], float]) -> list[float]:
    """Generic rolling window; the first ``period - 1`` entries are ``NAN``."""
    out = [NAN] * len(series)
    if period <= 0:
        return out
    for i in range(period - 1, len(series)):
        window = series[i - period + 1 : i + 1]
        if any(is_nan(v) for v in window):
            continue
        out[i] = fn(window)
    return out


def rolling_max(series: Sequence[float], period: int) -> list[float]:
    return rolling(series, period, max)


def rolling_min(series: Sequence[float], period: int) -> list[float]:
    return rolling(series, period, min)


def rolling_sum(series: Sequence[float], period: int) -> list[float]:
    return rolling(series, period, sum)


def rolling_mean(series: Sequence[float], period: int) -> list[float]:
    return rolling(series, period, mean)


def rolling_std(series: Sequence[float], period: int, sample: bool = True) -> list[float]:
    return rolling(series, period, lambda w: stdev(w, sample=sample))


def percentile_rank(series: Sequence[float], period: int) -> list[float]:
    """Rolling percentile rank of the current value inside its own window (0-100).

    Used for the volatility squeeze: "bandwidth is in the lowest N% of the last
    ``period`` bars".
    """
    out = [NAN] * len(series)
    if period <= 1:
        return out
    for i in range(period - 1, len(series)):
        window = [v for v in series[i - period + 1 : i + 1] if not is_nan(v)]
        if len(window) < 2:
            continue
        current = window[-1]
        below = sum(1 for v in window if v < current)
        out[i] = 100.0 * below / (len(window) - 1)
    return out


def slope(series: Sequence[float], period: int) -> list[float]:
    """Rolling least-squares slope of ``series`` over ``period`` bars."""
    out = [NAN] * len(series)
    if period < 2:
        return out
    xs = list(range(period))
    x_mean = sum(xs) / period
    denom = sum((x - x_mean) ** 2 for x in xs)
    if denom == 0:
        return out
    for i in range(period - 1, len(series)):
        window = series[i - period + 1 : i + 1]
        if any(is_nan(v) for v in window):
            continue
        y_mean = sum(window) / period
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, window))
        out[i] = num / denom
    return out


def linreg(series: Sequence[float], period: int) -> dict[str, list[float]]:
    """Rolling linear regression: value, slope, intercept and R^2."""
    n = len(series)
    value = [NAN] * n
    sl = [NAN] * n
    ic = [NAN] * n
    r2 = [NAN] * n
    if period < 2:
        return {"value": value, "slope": sl, "intercept": ic, "r2": r2}
    xs = list(range(period))
    x_mean = sum(xs) / period
    sxx = sum((x - x_mean) ** 2 for x in xs)
    for i in range(period - 1, n):
        window = series[i - period + 1 : i + 1]
        if any(is_nan(v) for v in window):
            continue
        y_mean = sum(window) / period
        sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, window))
        syy = sum((y - y_mean) ** 2 for y in window)
        b = sxy / sxx
        a = y_mean - b * x_mean
        sl[i] = b
        ic[i] = a
        value[i] = a + b * (period - 1)
        r2[i] = 1.0 if syy == 0 else (sxy * sxy) / (sxx * syy)
    return {"value": value, "slope": sl, "intercept": ic, "r2": r2}


# ---------------------------------------------------------------------------
# candle resampling (daily -> weekly/monthly, intraday -> H1/H4/D1)
# ---------------------------------------------------------------------------


def _bucket_key(dt: datetime, timeframe: str) -> tuple[int, ...]:
    if timeframe == "MN1":
        return (dt.year, dt.month)
    if timeframe == "W1":
        # ISO week, Monday anchored.
        iso = dt.isocalendar()
        return (iso[0], iso[1])
    if timeframe == "D1":
        return (dt.year, dt.month, dt.day)
    minutes = TIMEFRAME_MINUTES.get(timeframe, 60)
    if minutes >= 1440:
        return (dt.year, dt.month, dt.day)
    total = dt.hour * 60 + dt.minute
    bucket = (total // minutes) * minutes
    return (dt.year, dt.month, dt.day, bucket)


def resample(candles: Sequence[Candle], timeframe: str) -> list[Candle]:
    """Aggregate bars into a coarser timeframe.

    Returns a copy of the input when the target timeframe is not coarser than
    the source timeframe.
    """
    if not candles:
        return []
    src = candles[0].timeframe
    src_min = TIMEFRAME_MINUTES.get(src, 1440)
    dst_min = TIMEFRAME_MINUTES.get(timeframe, 1440)
    if dst_min <= src_min:
        return list(candles)

    out: list[Candle] = []
    current_key: tuple[int, ...] | None = None
    bucket: list[Candle] = []

    def flush() -> None:
        if not bucket:
            return
        out.append(
            Candle(
                dt=bucket[-1].dt,
                open=bucket[0].open,
                high=max(c.high for c in bucket),
                low=min(c.low for c in bucket),
                close=bucket[-1].close,
                volume=sum(c.volume for c in bucket),
                value=sum(c.value for c in bucket),
                trades=sum(c.trades for c in bucket),
                last=bucket[-1].last,
                symbol=bucket[0].symbol,
                source=bucket[0].source,
                timeframe=timeframe,
                synthetic=bucket[0].synthetic,
            )
        )

    for candle in candles:
        key = _bucket_key(candle.dt, timeframe)
        if key != current_key:
            flush()
            bucket = []
            current_key = key
        bucket.append(candle)
    flush()
    return out


def bars_per_year(timeframe: str) -> int:
    return BARS_PER_YEAR.get(timeframe, 250)


def annualise_return(mean_bar_return: float, timeframe: str) -> float:
    bpy = bars_per_year(timeframe)
    if mean_bar_return <= -1:
        return -1.0
    return (1.0 + mean_bar_return) ** bpy - 1.0


def annualise_vol(bar_stdev: float, timeframe: str) -> float:
    return bar_stdev * math.sqrt(bars_per_year(timeframe))


def window_slice(candles: Sequence[Candle], limit: int | None) -> list[Candle]:
    if limit is None or limit <= 0 or limit >= len(candles):
        return list(candles)
    return list(candles[-limit:])


def describe_candles(candles: Sequence[Candle]) -> dict[str, Any]:
    """Small diagnostic block rendered next to every analysis result."""
    if not candles:
        return {"bars": 0}
    return {
        "bars": len(candles),
        "timeframe": candles[0].timeframe,
        "from": candles[0].dt.isoformat(),
        "to": candles[-1].dt.isoformat(),
        "source": candles[0].source,
        "synthetic": bool(candles[0].synthetic),
        "first_price": candles[0].close,
        "last_price": candles[-1].close,
        "return_pct": pct_change(candles[0].close, candles[-1].close),
        "high": max(c.high for c in candles),
        "low": min(c.low for c in candles),
        "avg_volume": mean([c.volume for c in candles]),
    }


def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses/datetimes/NaN into JSON-safe values."""
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    if isinstance(obj, float):
        return None if math.isnan(obj) else obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return to_jsonable(asdict(obj))
    return str(obj)


def fmt_number(value: float | None, digits: int = 0) -> str:
    if value is None or is_nan(value):
        return "-"
    return f"{value:,.{digits}f}"


def fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or is_nan(value):
        return "-"
    return f"{value:+.{digits}f}%"


__all__ = [
    "BARS_PER_YEAR",
    "NAN",
    "UNSET",
    "RESAMPLE_TARGETS",
    "TIMEFRAME_MINUTES",
    "Candle",
    "FetchResult",
    "Instrument",
    "Quote",
    "annualise_return",
    "annualise_vol",
    "bars_per_year",
    "clamp",
    "col",
    "describe_candles",
    "first_valid_index",
    "fmt_number",
    "fmt_pct",
    "is_nan",
    "iso_list",
    "last_valid",
    "linreg",
    "mean",
    "pct_change",
    "percentile_rank",
    "resample",
    "rolling",
    "rolling_max",
    "rolling_mean",
    "rolling_min",
    "rolling_std",
    "rolling_sum",
    "safe_round",
    "slope",
    "stdev",
    "timestamps",
    "to_jsonable",
    "window_slice",
]
