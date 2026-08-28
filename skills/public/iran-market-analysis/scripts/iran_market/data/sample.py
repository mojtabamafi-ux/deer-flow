"""Deterministic synthetic market data (the offline fallback).

The Iranian endpoints are unreachable from sandboxes, CI runners and many
corporate networks.  Rather than failing, the registry falls back to this
generator so the dashboard, the backtester and the tests all have real,
*structured* bars to work with.

Everything here is explicitly labelled: ``Candle.synthetic is True`` and
``source="sample"``, and the dashboard renders a permanent banner.  **No number
produced by this module is market data.**

The generator is not a plain random walk - it walks through a regime cycle
(downtrend -> accumulation -> impulse -> distribution -> correction) where the
impulse is built from five sub-legs with Fibonacci-style retracements and the
correction from an A-B-C.  That is what lets the Elliott, RTM, ICT and ACT
engines be developed and tested against structures they are supposed to find.
"""

from __future__ import annotations

import math
import random
import zlib
from datetime import datetime, timedelta

from ..core import Candle
from ..jalali import trading_days_back

#: per-market behaviour: daily volatility, price limit, session shape
MARKET_PROFILES = {
    "equity": {"vol": 0.022, "limit_pct": 7.0, "start_price": 5000.0, "base_volume": 8_000_000.0, "tick": 1.0},
    "index": {"vol": 0.011, "limit_pct": 0.0, "start_price": 2_000_000.0, "base_volume": 0.0, "tick": 1.0},
    "fund": {"vol": 0.014, "limit_pct": 5.0, "start_price": 12_000.0, "base_volume": 3_000_000.0, "tick": 1.0},
    "commodity": {"vol": 0.018, "limit_pct": 5.0, "start_price": 250_000.0, "base_volume": 250_000.0, "tick": 10.0},
    "gold": {"vol": 0.013, "limit_pct": 0.0, "start_price": 45_000_000.0, "base_volume": 12_000.0, "tick": 1000.0},
    "fx": {"vol": 0.010, "limit_pct": 0.0, "start_price": 62_000.0, "base_volume": 900_000.0, "tick": 10.0},
    "crypto": {"vol": 0.032, "limit_pct": 0.0, "start_price": 65_000.0, "base_volume": 1_400.0, "tick": 0.5},
    "bond": {"vol": 0.004, "limit_pct": 0.0, "start_price": 10_000.0, "base_volume": 500_000.0, "tick": 1.0},
    "option": {"vol": 0.055, "limit_pct": 0.0, "start_price": 300.0, "base_volume": 40_000_000.0, "tick": 0.5},
}

#: regime cycle: (name, drift multiplier, volatility multiplier, relative length)
REGIME_CYCLE = (
    ("downtrend", -1.0, 1.2, 18),
    ("accumulation", 0.0, 0.45, 22),
    ("impulse", 1.0, 1.35, 34),
    ("distribution", 0.0, 0.7, 16),
    ("correction", -0.6, 1.1, 20),
)

SESSION_OPEN = (9, 0)
SESSION_CLOSE = (12, 30)


def seed_for(symbol: str, timeframe: str, extra: str = "") -> int:
    """Stable, process-independent seed (``hash()`` is salted per interpreter)."""
    return zlib.crc32(f"{symbol}|{timeframe}|{extra}".encode()) & 0x7FFFFFFF


def _impulse_returns(rng: random.Random, legs: int, magnitude: float) -> list[float]:
    """Five-wave impulse with Fibonacci retracements, then an A-B-C correction."""
    w1 = magnitude * rng.uniform(0.85, 1.15)
    # wave 2 retraces 38.2-78.6% of wave 1 and must never retrace 100%
    w2 = -w1 * rng.uniform(0.382, 0.786)
    # wave 3 is never the shortest: usually 1.272-1.618 x wave 1
    w3 = w1 * rng.uniform(1.272, 2.1)
    # wave 4 must not overlap wave 1's territory -> keep it above w1's top
    w4 = -w3 * rng.uniform(0.236, 0.382)
    w5 = rng.choice([w1, w1 * 0.618, w3 * 0.618])
    impulse = [w1, w2, w3, w4, w5]
    total = sum(impulse)
    # A-B-C zigzag correction: A takes 38.2-61.8% off, B retraces 50-78.6% of A,
    # C extends 1.0-1.618 x A.
    a = -total * rng.uniform(0.382, 0.618)
    b = -a * rng.uniform(0.5, 0.786)
    c = a * rng.uniform(1.0, 1.618)
    correction = [a, b, c]
    return _spread(rng, impulse + correction, legs)


def _spread(rng: random.Random, legs: list[float], count: int) -> list[float]:
    """Distribute ``legs`` (as fractions of the whole move) across ``count`` bars."""
    if count <= 0:
        return []
    weights: list[float] = []
    for leg in legs:
        weights.append(abs(leg) or 0.01)
    total_weight = sum(weights) or 1.0
    spans = [max(1, int(round(count * w / total_weight))) for w in weights]
    # fix rounding drift on the last leg
    drift = count - sum(spans)
    if drift:
        spans[-1] = max(1, spans[-1] + drift)
    out: list[float] = []
    for leg, span in zip(legs, spans):
        per = leg / span
        for _ in range(span):
            jitter = rng.uniform(0.65, 1.35)
            out.append(per * jitter)
    return out[:count]


def _regime_plan(rng: random.Random, bars: int) -> list[tuple[str, int]]:
    """Build the regime schedule so the series ends mid-cycle (never always at a top)."""
    plan: list[tuple[str, int]] = []
    index = rng.randrange(len(REGIME_CYCLE))
    covered = 0
    while covered < bars:
        name, _, _, length = REGIME_CYCLE[index]
        span = max(6, int(length * rng.uniform(0.8, 1.25)))
        span = min(span, bars - covered)
        plan.append((name, span))
        covered += span
        index = (index + 1) % len(REGIME_CYCLE)
    return plan


def _daily_dates(bars: int, end: datetime | None = None) -> list[datetime]:
    end_date = (end or datetime.now()).date()
    days = trading_days_back(end_date, bars)
    return [datetime(d.year, d.month, d.day, 12, 30) for d in days]


def _intraday_dates(bars: int, minutes: int, end: datetime | None = None) -> list[datetime]:
    """Session timestamps for ``minutes`` bars, walking back over trading days."""
    open_h, open_m = SESSION_OPEN
    close_h, close_m = SESSION_CLOSE
    per_day = max(1, (close_h * 60 + close_m - open_h * 60 - open_m) // minutes)
    days_needed = max(1, (bars + per_day - 1) // per_day)
    end_date = (end or datetime.now()).date()
    days = trading_days_back(end_date, days_needed)[-days_needed:]
    stamps: list[datetime] = []
    for day in days:
        for i in range(per_day):
            minute = open_h * 60 + open_m + i * minutes
            stamps.append(datetime(day.year, day.month, day.day, minute // 60, minute % 60))
    return stamps[-bars:]


def synthetic_candles(
    symbol: str,
    timeframe: str = "D1",
    bars: int = 400,
    market: str = "equity",
    end: datetime | None = None,
    profile: dict | None = None,
) -> list[Candle]:
    """Generate ``bars`` deterministic candles for ``symbol``.

    Same inputs always produce the same series, so tests can assert on exact
    wave structures and the dashboard stays stable between refreshes.
    """
    profile = {**MARKET_PROFILES.get(market, MARKET_PROFILES["equity"]), **(profile or {})}
    rng = random.Random(seed_for(symbol, timeframe))
    bars = max(30, int(bars))

    minutes = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H2": 120, "H4": 240}.get(timeframe)
    dates = _intraday_dates(bars, minutes) if minutes else _daily_dates(bars)
    bars = len(dates)

    # intraday series compress the same story into fewer sessions
    vol_scale = (minutes / 330.0) ** 0.5 if minutes else 1.0
    base_vol = profile["vol"] * vol_scale

    plan = _regime_plan(rng, bars)
    returns: list[float] = []
    regime_by_name = {entry[0]: entry for entry in REGIME_CYCLE}
    for name, span in plan:
        _, drift, vol_mult, _ = regime_by_name[name]
        if name == "impulse":
            leg_returns = _impulse_returns(rng, span, base_vol * 5.5 * drift)
            returns.extend(leg_returns)
        elif name == "accumulation":
            returns.extend(base_vol * rng.gauss(0.0, 0.7) for _ in range(span))
        elif name == "distribution":
            returns.extend(base_vol * rng.gauss(-0.15, 0.9) for _ in range(span))
        else:
            per_bar = base_vol * drift * 0.85
            returns.extend(base_vol * rng.gauss(per_bar / base_vol, vol_mult * 0.75) for _ in range(span))
    returns = returns[:bars]

    anchor = profile["start_price"]
    price = anchor * rng.uniform(0.7, 1.3)
    limit = profile["limit_pct"] / 100.0 if profile["limit_pct"] else None
    tick = profile["tick"] or 1.0
    base_volume = profile["base_volume"]

    candles: list[Candle] = []
    volume_state = rng.uniform(0.8, 1.2)
    for i, when in enumerate(dates):
        ret = returns[i] if i < len(returns) else 0.0
        if limit:
            ret = max(-limit, min(limit, ret))
        open_price = price * (1.0 + rng.gauss(0.0, base_vol * 0.25))
        if limit:
            open_price = min(price * (1 + limit), max(price * (1 - limit), open_price))
        close = open_price * (1.0 + ret)
        wick = abs(rng.gauss(0.0, base_vol)) * 0.9
        high = max(open_price, close) * (1.0 + wick)
        low = min(open_price, close) * (1.0 - wick)
        if limit:
            high = min(price * (1 + limit), high)
            low = max(price * (1 - limit), low)

        # volume: dry-up in accumulation, spike on impulse/breakout bars
        move = abs(close - open_price) / max(open_price, 1e-9)
        volume_state = max(0.35, volume_state * rng.uniform(0.93, 1.07))
        volume_factor = (1.0 + 9.0 * move / max(base_vol, 1e-6)) * volume_state
        volume = base_volume * volume_factor * rng.uniform(0.8, 1.25) if base_volume else 0.0

        def snap(value: float) -> float:
            return max(tick, round(value / tick) * tick)

        close = snap(close)
        open_price = snap(open_price)
        high = snap(max(high, open_price, close))
        low = snap(min(low, open_price, close))
        # keep the level plausible: a multiplicative walk over 1200 bars would
        # otherwise decay to pennies, which makes ATR ~0 and every ratio explode
        if price > anchor * 3.0 or price < anchor * 0.3:
            drift_correction = math.log(anchor / price) * 0.02
            close *= math.exp(drift_correction)
            open_price *= math.exp(drift_correction)
            high *= math.exp(drift_correction)
            low *= math.exp(drift_correction)
            price = close
        candles.append(
            Candle(
                dt=when,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=round(volume, 2),
                value=round(volume * (open_price + close) / 2.0, 2),
                trades=int(max(1, volume / max(base_volume, 1.0) * 900)) if base_volume else 0,
                last=close,
                symbol=symbol,
                source="sample",
                timeframe=timeframe,
                synthetic=True,
            )
        )
        price = close
    return candles


def synthetic_quote(symbol: str, name: str = "", market: str = "equity", timeframe: str = "D1") -> dict:
    bars = synthetic_candles(symbol, timeframe, 3, market=market)
    last, prev = bars[-1], bars[-2]
    return {
        "symbol": symbol,
        "name": name or symbol,
        "market": market,
        "last": last.close,
        "close": last.close,
        "prev_close": prev.close,
        "open": last.open,
        "high": last.high,
        "low": last.low,
        "volume": last.volume,
        "value": last.value,
        "trades": last.trades,
        "as_of": last.dt.isoformat(),
        "change": last.close - prev.close,
        "change_pct": 100.0 * (last.close - prev.close) / prev.close if prev.close else 0.0,
        "source": "sample",
        "synthetic": True,
    }


def shift_session(minutes: int) -> timedelta:
    return timedelta(minutes=minutes)


__all__ = ["MARKET_PROFILES", "REGIME_CYCLE", "seed_for", "synthetic_candles", "synthetic_quote"]
