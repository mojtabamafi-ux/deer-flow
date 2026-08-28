"""Data-source contracts shared by the TSETMC / IME / Market_CGCC adapters.

Every adapter returns :class:`~iran_market.core.FetchResult` so the caller always
knows whether the numbers came from a live API, the local cache, a user supplied
file, or the deterministic synthetic fallback.  Nothing in the analysis stack is
allowed to guess.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from ..core import Candle, FetchResult, Instrument, Quote


class MarketSource(ABC):
    """Minimal interface every market data provider implements."""

    #: stable id used in API responses, caches and CLI flags
    name: str = ""
    #: human readable label shown in the dashboard
    label: str = ""
    #: markets this provider serves (subset of the registry's market list)
    markets: tuple[str, ...] = ()
    #: whether the provider can serve intraday bars
    supports_intraday: bool = False
    #: whether short selling exists in this venue (futures/commodity/crypto: yes)
    supports_short: bool = False

    @abstractmethod
    def list_instruments(self, market: str | None = None, query: str | None = None) -> FetchResult:
        """Return ``list[Instrument]`` wrapped in a :class:`FetchResult`."""

    @abstractmethod
    def candles(self, code: str, timeframe: str = "D1", limit: int = 400) -> FetchResult:
        """Return ``list[Candle]`` wrapped in a :class:`FetchResult`."""

    def quote(self, code: str) -> FetchResult:
        """Optional: last trade snapshot.  Default derives it from the last candle."""
        result = self.candles(code, "D1", 2)
        if not result.ok or not result.data:
            return FetchResult(data=None, mode=result.mode, source=self.name, ok=False, error=result.error)
        bars: Sequence[Candle] = result.data
        last = bars[-1]
        prev_close = bars[-2].close if len(bars) > 1 else None
        return FetchResult(
            data=Quote(
                symbol=code,
                last=last.close,
                close=last.close,
                prev_close=prev_close,
                open=last.open,
                high=last.high,
                low=last.low,
                volume=last.volume,
                value=last.value,
                trades=last.trades,
                as_of=last.dt,
                source=self.name,
                synthetic=last.synthetic,
            ),
            mode=result.mode,
            source=self.name,
        )

    def health(self) -> dict[str, Any]:
        """Cheap reachability probe used by ``doctor.py`` and the dashboard badge."""
        return {"name": self.name, "label": self.label, "reachable": None, "detail": "not probed"}


class SourceError(RuntimeError):
    """Raised inside adapters; the registry converts it into a failed FetchResult."""


def validate_candles(bars: Sequence[Candle]) -> list[Candle]:
    """Drop malformed bars and enforce OHLC consistency.

    Remote feeds regularly return zero/negative prices for halted sessions, or
    ``high < close`` rows when the feed is mid-update.  Those bars poison every
    indicator downstream (ATR/True Range go negative), so they are repaired here
    instead of inside each indicator.
    """
    cleaned: list[Candle] = []
    for bar in bars:
        if bar.close is None or bar.close <= 0:
            continue
        high = max(bar.high, bar.close, bar.open)
        low = min(bar.low, bar.close, bar.open)
        if low <= 0:
            low = min(bar.close, bar.open)
        cleaned.append(
            Candle(
                dt=bar.dt,
                open=max(bar.open, low),
                high=high,
                low=low,
                close=bar.close,
                volume=max(bar.volume or 0.0, 0.0),
                value=max(bar.value or 0.0, 0.0),
                trades=max(bar.trades or 0, 0),
                last=bar.last,
                symbol=bar.symbol,
                source=bar.source,
                timeframe=bar.timeframe,
                synthetic=bar.synthetic,
            )
        )
    cleaned.sort(key=lambda b: b.dt)
    # de-duplicate timestamps keeping the last occurrence (feeds repeat rows)
    deduped: dict = {}
    for bar in cleaned:
        deduped[bar.dt] = bar
    return list(deduped.values())


__all__ = ["FetchResult", "Instrument", "MarketSource", "Quote", "SourceError", "validate_candles"]
