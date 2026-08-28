"""Data-source registry: universe, routing, caching and honest fallback.

Routing rules (in order):

1. a user-registered file (:class:`~.file_source.FileSource`) wins - the user
   explicitly said "analyse this";
2. the provider that owns the symbol's market is asked for live data
   (``mode="live"`` in auto/live mode);
3. a cached copy, if any (``mode="cache"``);
4. the deterministic synthetic generator (``mode="sample"``).

Every result carries the mode it actually came from; the dashboard renders it as
a banner and the CLI prints it.  Falling back silently would be the one thing
worse than not having data at all.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..core import Candle, FetchResult, Instrument, Quote, resample
from ..data.sample import synthetic_candles
from .file_source import FileSource
from .ime import ImeSource
from .market_cgcc import CgccSource
from .tsetmc import TsetmcSource

SKILL_ROOT = Path(__file__).resolve().parents[3]
UNIVERSE_PATH = Path(os.environ.get("IRAN_MARKET_SYMBOLS", SKILL_ROOT / "references" / "symbols.json"))

DEFAULT_MARKETS = (
    {"id": "index", "label": "شاخص‌ها", "source": "tsetmc", "allow_short": False},
    {"id": "equity", "label": "سهام", "source": "tsetmc", "allow_short": False},
    {"id": "fund", "label": "صندوق‌ها", "source": "tsetmc", "allow_short": False},
    {"id": "commodity", "label": "بورس کالا", "source": "ime", "allow_short": True},
    {"id": "gold", "label": "طلا", "source": "market_cgcc", "allow_short": True},
    {"id": "fx", "label": "ارز", "source": "market_cgcc", "allow_short": True},
    {"id": "crypto", "label": "رمزارز", "source": "market_cgcc", "allow_short": True},
)

_FALLBACK_SYMBOLS = (
    ("32097828799138957", "شاخص کل", "شاخص کل بورس تهران", "index", "tsetmc"),
    ("46348559193224090", "فولاد", "فولاد مبارکه اصفهان", "equity", "tsetmc"),
    ("USD_IRR", "دلار آزاد", "دلار آمریکا به ریال", "fx", "market_cgcc"),
    ("BTC_USDT", "بیت‌کوین", "Bitcoin / Tether", "crypto", "market_cgcc"),
    ("SAFFRON_IME", "زعفران", "گواهی سپرده زعفران", "commodity", "ime"),
)

#: how long a health probe result is trusted
HEALTH_TTL_SECONDS = 300


class DataSourceRegistry:
    """Single entry point the engines and the dashboard use for market data."""

    def __init__(self, mode: str = "auto", universe_path: Path | str = UNIVERSE_PATH) -> None:
        self.mode = mode if mode in ("auto", "live", "sample") else "auto"
        self.tsetmc = TsetmcSource()
        self.ime = ImeSource()
        self.cgcc = CgccSource()
        self.files = FileSource()
        self.sources: dict[str, Any] = {
            self.tsetmc.name: self.tsetmc,
            self.ime.name: self.ime,
            self.cgcc.name: self.cgcc,
            self.files.name: self.files,
        }
        self.universe_path = Path(universe_path)
        self._universe: list[dict[str, Any]] = []
        self._markets: list[dict[str, Any]] = list(DEFAULT_MARKETS)
        self._index: dict[str, dict[str, Any]] = {}
        self._health: dict[str, Any] = {}
        self._health_at: float = 0.0
        self._candle_cache: dict[tuple[str, str], tuple[float, list[Candle], str]] = {}
        self.load_universe()

    # -- universe ----------------------------------------------------------
    def load_universe(self) -> None:
        payload: dict[str, Any] | None = None
        try:
            if self.universe_path.exists():
                payload = json.loads(self.universe_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("symbols"), list):
            self._universe = [s for s in payload["symbols"] if isinstance(s, dict) and s.get("code")]
            if isinstance(payload.get("markets"), list) and payload["markets"]:
                self._markets = payload["markets"]
        else:
            self._universe = [
                {"code": code, "symbol": symbol, "name": name, "market": market, "source": source, "verified": False}
                for code, symbol, name, market, source in _FALLBACK_SYMBOLS
            ]
        self._index = {str(s["code"]): s for s in self._universe}

    def markets(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for entry in self._universe:
            counts[entry.get("market", "other")] = counts.get(entry.get("market", "other"), 0) + 1
        out = []
        for market in self._markets:
            out.append({**market, "symbols": counts.get(market["id"], 0)})
        return out

    def symbols(self, market: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        items = list(self._universe)
        items.extend(
            {"code": m["symbol"], "symbol": m["symbol"], "name": m["name"], "market": "custom", "source": "file", "verified": True}
            for m in self.files.symbols()
        )
        if market and market != "all":
            items = [i for i in items if i.get("market") == market]
        if query:
            needle = query.strip().lower()
            items = [i for i in items if needle in str(i.get("symbol", "")).lower() or needle in str(i.get("name", "")).lower() or needle in str(i.get("code", "")).lower()]
        return items

    def resolve(self, code: str) -> dict[str, Any]:
        """Resolve a code (or Persian/Latin symbol) to its universe entry."""
        key = str(code)
        if key in self._index:
            return self._index[key]
        lowered = key.lower()
        if lowered in self._index:
            return self._index[lowered]
        for entry in self._universe:
            if entry.get("symbol") == key or entry.get("name") == key:
                return entry
        for meta in self.files.symbols():
            if meta["symbol"] == key:
                return {"code": key, "symbol": key, "name": meta["name"], "market": meta["market"], "source": "file"}
        return {"code": key, "symbol": key, "name": key, "market": "equity", "source": "tsetmc", "verified": False, "unknown": True}

    def market_of(self, code: str) -> str:
        return str(self.resolve(code).get("market") or "equity")

    def allow_short(self, code: str) -> bool:
        market = self.market_of(code)
        for entry in self._markets:
            if entry.get("id") == market:
                return bool(entry.get("allow_short"))
        return market in ("commodity", "gold", "fx", "crypto", "futures")

    # -- data --------------------------------------------------------------
    def candles(self, code: str, timeframe: str = "D1", limit: int = 400, mode: str | None = None) -> FetchResult:
        meta = self.resolve(code)
        active_mode = mode or self.mode
        cache_key = (str(meta.get("code", code)), timeframe)
        cached = self._candle_cache.get(cache_key)
        if cached and time.time() - cached[0] < 600:
            _, bars, cached_mode = cached
            return FetchResult(data=_tail(bars, timeframe, limit), mode=cached_mode, source=meta.get("source", ""))

        source_name = str(meta.get("source") or "tsetmc")
        source = self.sources.get(source_name)
        market = str(meta.get("market") or "equity")

        result: FetchResult | None = None
        # "sample" means "do not touch the network" - it must never mean "ignore
        # the history the user just uploaded", so the file source always runs.
        if source is not None and (active_mode != "sample" or source_name == self.files.name):
            try:
                result = source.candles(str(meta.get("code", code)), timeframe, limit)
            except Exception as exc:  # never let a bad provider kill the dashboard
                result = FetchResult(data=None, mode="live", source=source_name, ok=False, error=f"{type(exc).__name__}: {exc}")
            if result and result.ok and result.data:
                bars = list(result.data)
                self._candle_cache[cache_key] = (time.time(), bars, result.mode)
                return FetchResult(data=_tail(bars, timeframe, limit), mode=result.mode, source=source_name)

        if active_mode == "live":
            error = (result.error if result and result.error else "no data")
            return FetchResult(data=None, mode="live", source=source_name, ok=False, error=error)

        synthetic = synthetic_candles(symbol=str(meta.get("code", code)), timeframe=timeframe, bars=max(limit, 300), market=market)
        return FetchResult(
            data=synthetic[-limit:],
            mode="sample",
            source="sample",
            ok=True,
            error=(result.error if result and result.error else ""),
        )

    def quote(self, code: str, mode: str | None = None) -> FetchResult:
        meta = self.resolve(code)
        result = self.candles(code, "D1", 3, mode=mode)
        if not result.ok or not result.data:
            return FetchResult(data=None, mode=result.mode, source=result.source, ok=False, error=result.error)
        bars: list[Candle] = result.data
        last = bars[-1]
        prev = bars[-2].close if len(bars) > 1 else last.open
        return FetchResult(
            data=Quote(
                symbol=str(meta.get("code", code)),
                name=str(meta.get("name") or meta.get("symbol") or code),
                market=str(meta.get("market") or "equity"),
                last=last.close,
                close=last.close,
                prev_close=prev,
                open=last.open,
                high=last.high,
                low=last.low,
                volume=last.volume,
                value=last.value,
                trades=last.trades,
                as_of=last.dt,
                source=result.source,
                synthetic=last.synthetic,
            ),
            mode=result.mode,
            source=result.source,
        )

    def watchlist(self, market: str | None = None, mode: str | None = None) -> FetchResult:
        rows: list[dict[str, Any]] = []
        for entry in self.symbols(market=market):
            quote = self.quote(str(entry["code"]), mode=mode)
            if not quote.ok or not quote.data:
                continue
            q: Quote = quote.data
            rows.append(
                {
                    "code": entry["code"],
                    "symbol": entry.get("symbol"),
                    "name": entry.get("name"),
                    "market": entry.get("market"),
                    "last": q.last,
                    "change_pct": q.change_pct,
                    "volume": q.volume,
                    "mode": quote.mode,
                    "synthetic": q.synthetic,
                }
            )
        return FetchResult(data=rows, mode="mixed", source="registry")

    # -- health ------------------------------------------------------------
    def health(self, force: bool = False) -> dict[str, Any]:
        if not force and self._health and time.time() - self._health_at < HEALTH_TTL_SECONDS:
            return self._health
        report: dict[str, Any] = {"mode": self.mode, "checked_at": time.time(), "sources": []}
        for name in ("tsetmc", "ime", "market_cgcc"):
            source = self.sources[name]
            try:
                report["sources"].append(source.health())
            except Exception as exc:  # pragma: no cover - defensive
                report["sources"].append({"name": name, "reachable": False, "detail": f"{type(exc).__name__}: {exc}"})
        report["live_available"] = any(s.get("reachable") for s in report["sources"])
        report["effective_mode"] = "live" if (self.mode == "live" or (self.mode == "auto" and report["live_available"])) else "sample"
        self._health = report
        self._health_at = time.time()
        return report

    def health_cached(self) -> dict[str, Any] | None:
        """Last probe result, or ``None`` when nothing has been probed yet.

        The dashboard must never trigger a network probe implicitly: each source
        has a hard timeout and a cold probe of all three exchanges can take a
        minute.  Probing is an explicit user action.
        """
        return self._health or None

    def endpoints(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in ("tsetmc", "ime", "market_cgcc"):
            out.extend(self.sources[name].endpoints())
        return out

    def upload(self, symbol: str, text: str, timeframe: str = "D1", name: str = "") -> FetchResult:
        return self.files.register_text(symbol, text, timeframe, name)

    def upload_file(self, symbol: str, path: str | Path, timeframe: str = "D1", name: str = "") -> FetchResult:
        return self.files.register_path(symbol, path, timeframe, name)

    def doctor(self, verify_symbols: bool = False) -> dict[str, Any]:
        """Full reachability + code-verification report used by ``doctor.py``."""
        report: dict[str, Any] = {
            "health": self.health(force=True),
            "endpoints": self.endpoints(),
            "universe": {
                "path": str(self.universe_path),
                "symbols": len(self._universe),
                "verified": sum(1 for s in self._universe if s.get("verified")),
                "markets": len(self._markets),
            },
        }
        if verify_symbols:
            verified: list[dict[str, Any]] = []
            for entry in self._universe:
                if entry.get("source") != "tsetmc":
                    verified.append({"code": entry["code"], "symbol": entry.get("symbol"), "status": "skipped", "reason": "not a TSETMC code"})
                    continue
                info = self.tsetmc.symbol_info(str(entry["code"]))
                if not info.ok:
                    verified.append({"code": entry["code"], "symbol": entry.get("symbol"), "status": "error", "reason": info.error})
                    continue
                instrument: Instrument = info.data
                matches = instrument.symbol == entry.get("symbol")
                verified.append(
                    {
                        "code": entry["code"],
                        "symbol": entry.get("symbol"),
                        "returned_symbol": instrument.symbol,
                        "returned_name": instrument.name,
                        "status": "verified" if matches else "mismatch",
                    }
                )
            report["universe"]["verification"] = verified
        return report

    def register_universe_entry(self, entry: dict[str, Any]) -> None:
        """Add/replace a universe entry at runtime (used by ``doctor.py`` repairs)."""
        code = str(entry["code"])
        self._index[code] = entry
        for index, existing in enumerate(self._universe):
            if str(existing.get("code")) == code:
                self._universe[index] = entry
                return
        self._universe.append(entry)


def _tail(bars: list[Candle], timeframe: str, limit: int) -> list[Candle]:
    if bars and bars[0].timeframe != timeframe:
        bars = resample(bars, timeframe)
    return bars[-limit:] if limit and limit > 0 else bars


def build_registry(mode: str = "auto") -> DataSourceRegistry:
    return DataSourceRegistry(mode=mode)


__all__ = ["DEFAULT_MARKETS", "DataSourceRegistry", "HEALTH_TTL_SECONDS", "UNIVERSE_PATH", "build_registry"]
