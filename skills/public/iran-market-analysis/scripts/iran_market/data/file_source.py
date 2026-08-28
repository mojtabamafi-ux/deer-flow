"""CSV/JSON history loader - the "my own data" path.

Accepts the shapes people actually export from TSETMC clients, TradingView and
Excel:

* ``date,open,high,low,close,volume`` with or without a header
* Jalali (``1403-04-15`` / ``14030415``) or Gregorian (``2024-07-06``) dates
* thousands separators, Persian digits, ``<OHLC>`` columns, ``;`` delimiters
* a JSON array of objects with the same aliases as the TSETMC payload

Registered symbols behave exactly like exchange symbols: they flow through the
same indicator, Elliott, RTM, ICT, ACT and backtest engines.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core import Candle, FetchResult
from ..jalali import parse_deven
from .base import MarketSource, validate_candles

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

FIELD_ALIASES = {
    "date": ("date", "dt", "time", "datetime", "deven", "dEven", "تاریخ", "تاريخ"),
    "open": ("open", "o", "باز", "قیمت باز"),
    "high": ("high", "h", "بیشترین", "سقف"),
    "low": ("low", "l", "کمترین", "کف"),
    "close": ("close", "c", "last", "قیمت", "بستن", "آخرین"),
    "volume": ("volume", "vol", "v", "حجم", "qTotTran5J"),
    "value": ("value", "turnover", "ارزش", "qTotCap"),
    "trades": ("trades", "count", "تعداد", "zTotTran"),
}


def _clean_number(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).translate(_PERSIAN_DIGITS)
    text = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    if text in ("", "-", ".", "-."):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean_header(name: str) -> str:
    return str(name).translate(_PERSIAN_DIGITS).strip().strip('"').lower()


def _map_header(headers: Iterable[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    cleaned = [_clean_header(h) for h in headers]
    for field, aliases in FIELD_ALIASES.items():
        for index, name in enumerate(cleaned):
            if name in aliases:
                mapping[field] = index
                break
    return mapping


def _row_to_candle(values: dict[str, Any], symbol: str, timeframe: str, source: str) -> Candle | None:
    when = parse_deven(values.get("date"))
    if when is None:
        raw = values.get("date")
        if isinstance(raw, (int, float)) and raw > 1_000_000_000:
            when = datetime.fromtimestamp(float(raw) / (1000 if raw > 1e12 else 1)).date()
    if when is None:
        return None
    close = _clean_number(values.get("close"))
    open_ = _clean_number(values.get("open")) or close
    high = _clean_number(values.get("high")) or close
    low = _clean_number(values.get("low")) or close
    if close is None:
        return None
    return Candle(
        dt=datetime(when.year, when.month, when.day, 12, 30),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=_clean_number(values.get("volume")) or 0.0,
        value=_clean_number(values.get("value")) or 0.0,
        trades=int(_clean_number(values.get("trades")) or 0),
        symbol=symbol,
        source=source,
        timeframe=timeframe,
    )


def parse_table(text: str, symbol: str = "", timeframe: str = "D1") -> list[Candle]:
    """Parse CSV/TSV/semicolon-delimited text into candles."""
    if not text.strip():
        return []
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return []
    header = rows[0]
    mapping = _map_header(header)
    candles: list[Candle] = []
    if "date" in mapping:
        data_rows = rows[1:]
    else:
        # no header: assume date,open,high,low,close,volume
        mapping = {"date": 0, "open": 1, "high": 2, "low": 3, "close": 4, "volume": 5}
        data_rows = rows
    for row in data_rows:
        if len(row) < 2:
            continue
        values = {field: row[index] if index < len(row) else None for field, index in mapping.items()}
        candle = _row_to_candle(values, symbol, timeframe, "file")
        if candle:
            candles.append(candle)
    return validate_candles(candles)


def parse_json(text: str, symbol: str = "", timeframe: str = "D1") -> list[Candle]:
    payload = json.loads(text)
    if isinstance(payload, dict):
        for key in ("data", "history", "candles", "items", "closingPriceDailyList"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        return []
    candles = [_row_to_candle(row, symbol, timeframe, "file") for row in payload if isinstance(row, dict)]
    return validate_candles([c for c in candles if c])


class FileSource(MarketSource):
    """In-memory registry of user supplied histories, keyed by symbol."""

    name = "file"
    label = "فایل بارگذاری‌شده"
    markets = ("equity", "index", "fund", "commodity", "gold", "fx", "crypto", "futures")
    supports_intraday = True
    supports_short = False

    def __init__(self) -> None:
        self._series: dict[str, list[Candle]] = {}
        self._meta: dict[str, dict[str, Any]] = {}

    def register_text(self, symbol: str, text: str, timeframe: str = "D1", name: str = "") -> FetchResult:
        text = text.lstrip("\ufeff")
        try:
            candles = parse_json(text, symbol, timeframe) if text.lstrip().startswith(("{", "[")) else parse_table(text, symbol, timeframe)
        except json.JSONDecodeError as exc:
            return FetchResult(data=None, mode="file", source=self.name, ok=False, error=f"invalid JSON: {exc}")
        if not candles:
            return FetchResult(data=[], mode="file", source=self.name, ok=False, error="no parsable rows found")
        self._series[symbol] = candles
        self._meta[symbol] = {
            "symbol": symbol,
            "name": name or symbol,
            "market": "custom",
            "source": "file",
            "bars": len(candles),
            "timeframe": timeframe,
            "from": candles[0].dt.isoformat(),
            "to": candles[-1].dt.isoformat(),
        }
        return FetchResult(data=candles, mode="file", source=self.name)

    def register_path(self, symbol: str, path: str | Path, timeframe: str = "D1", name: str = "") -> FetchResult:
        file_path = Path(path)
        if not file_path.exists():
            return FetchResult(data=None, mode="file", source=self.name, ok=False, error=f"file not found: {file_path}")
        return self.register_text(symbol, file_path.read_text(encoding="utf-8", errors="replace"), timeframe, name or file_path.stem)

    def symbols(self) -> list[dict[str, Any]]:
        return list(self._meta.values())

    def list_instruments(self, market: str | None = None, query: str | None = None) -> FetchResult:
        from ..core import Instrument

        items = [
            Instrument(code=s["symbol"], symbol=s["symbol"], name=s["name"], market=s["market"], source=self.name)
            for s in self._meta.values()
        ]
        if query:
            needle = query.strip().lower()
            items = [i for i in items if needle in i.symbol.lower() or needle in i.name.lower()]
        return FetchResult(data=items, mode="file", source=self.name)

    def candles(self, code: str, timeframe: str = "D1", limit: int = 400) -> FetchResult:
        from ..core import resample

        bars = self._series.get(code)
        if not bars:
            return FetchResult(data=None, mode="file", source=self.name, ok=False, error=f"no file registered for {code}")
        if timeframe != bars[0].timeframe:
            bars = resample(bars, timeframe)
        return FetchResult(data=bars[-limit:], mode="file", source=self.name)

    def drop(self, symbol: str) -> bool:
        had_series = self._series.pop(symbol, None) is not None
        self._meta.pop(symbol, None)
        return had_series


__all__ = ["FIELD_ALIASES", "FileSource", "parse_json", "parse_table"]
