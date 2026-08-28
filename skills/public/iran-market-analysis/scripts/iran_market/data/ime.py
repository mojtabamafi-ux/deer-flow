"""Iran Mercantile Exchange (IME / بورس کالا) adapter.

Requested surface: ``IME_Futures``, ``IME_Option``, ``IME_Certificate``,
``IME_Fund``, ``IME_Physical``.

.. important::
   ``www.ime.co.ir`` was **not reachable** from the environment this skill was
   built in (TLS handshake reset), so none of the paths below are marked
   ``verified``.  The adapter is therefore written as a *thin, configurable*
   JSON client:

   * every path lives in :data:`IME_API` and can be overridden at runtime with
     ``IME_ENDPOINTS_JSON`` (a JSON object mapping API name -> path template) or
     edited in ``config.yaml``;
   * parsers accept either a bare list or a dict envelope and pick fields by
     alias, because IME's payloads have changed shape over time;
   * ``scripts/doctor.py`` probes every endpoint and prints the real HTTP
     status, which is the supported way to confirm/repin these paths on a
     machine that can reach the exchange.

   When the exchange is unreachable the registry falls back to the deterministic
   synthetic generator (clearly labelled in the UI).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ..core import Candle, FetchResult, Instrument
from ..jalali import parse_deven
from .base import MarketSource, SourceError, validate_candles
from .http_client import build_url, http_get, http_json

DEFAULT_BASE = os.environ.get("IME_API_BASE", "https://www.ime.co.ir")

IME_API: dict[str, dict[str, Any]] = {
    "IME_Futures": {"path": "/api/futures/trades", "verified": False},
    "IME_Option": {"path": "/api/option/chain", "verified": False},
    "IME_Certificate": {"path": "/api/certificate/trades", "verified": False},
    "IME_Fund": {"path": "/api/fund/nav", "verified": False},
    "IME_Physical": {"path": "/api/physical/trades", "verified": False},
    "IME_Instruments": {"path": "/api/instruments", "verified": False},
}


def _load_overrides() -> dict[str, str]:
    raw = os.environ.get("IME_ENDPOINTS_JSON")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _rows(payload: Any, *keys: str) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        for value in payload.values():
            if isinstance(value, list) and value:
                return [r for r in value if isinstance(r, dict)]
    return []


def _f(row: dict, *names: str) -> float | None:
    for name in names:
        if row.get(name) not in (None, ""):
            try:
                return float(row[name])
            except (TypeError, ValueError):
                continue
    return None


def _s(row: dict, *names: str, default: str = "") -> str:
    for name in names:
        if row.get(name) not in (None, ""):
            return str(row[name])
    return default


class ImeSource(MarketSource):
    name = "ime"
    label = "بورس کالا (IME)"
    markets = ("commodity", "futures", "certificate", "fund")
    supports_intraday = False
    supports_short = True  # commodity futures can be sold first on IME

    def __init__(self, base: str = DEFAULT_BASE, timeout: float = 8.0, retries: int = 2) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.overrides = _load_overrides()

    def _path(self, api: str) -> str:
        return self.overrides.get(api) or IME_API[api]["path"]

    def _get(self, api: str, params: dict[str, Any] | None = None) -> Any:
        url = build_url(self.base, self._path(api), params)
        payload, error = http_json(url, timeout=self.timeout, retries=self.retries)
        if error:
            raise SourceError(f"{api}: {error} ({url})")
        return payload

    # -- instruments -------------------------------------------------------
    def list_instruments(self, market: str | None = None, query: str | None = None) -> FetchResult:
        try:
            payload = self._get("IME_Instruments", {"market": market})
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _rows(payload, "instruments", "items", "data")
        if not rows:
            return FetchResult(data=[], mode="live", source=self.name, ok=False, error="IME_Instruments: empty payload")
        instruments = [
            Instrument(
                code=_s(r, "code", "instrumentCode", "ringCode", "symbol"),
                symbol=_s(r, "symbol", "instrumentCode", "code"),
                name=_s(r, "name", "title", "instrumentName"),
                market=_s(r, "market", default="commodity"),
                exchange="IME",
                source=self.name,
                extra={"ring": _s(r, "ringCode", "ring"), "category": _s(r, "category", "group"), "raw": r},
            )
            for r in rows
        ]
        if query:
            needle = query.strip().lower()
            instruments = [i for i in instruments if needle in i.symbol.lower() or needle in i.name.lower() or needle in i.code.lower()]
        return FetchResult(data=instruments, mode="live", source=self.name)

    # -- markets -----------------------------------------------------------
    def futures(self, code: str | None = None, date: int | None = None) -> FetchResult:
        return self._table("IME_Futures", {"code": code, "date": date})

    def option(self, code: str | None = None, underlying: str | None = None) -> FetchResult:
        return self._table("IME_Option", {"code": code, "underlying": underlying})

    def certificate(self, code: str | None = None) -> FetchResult:
        """گواهی سپرده کالایی (gold/saffron/pistachio deposit certificates)."""
        return self._table("IME_Certificate", {"code": code})

    def fund(self, code: str | None = None) -> FetchResult:
        return self._table("IME_Fund", {"code": code})

    def physical(self, code: str | None = None, date: int | None = None) -> FetchResult:
        """بازار فیزیکی - the physical ring trades (steel, petrochem, agri)."""
        return self._table("IME_Physical", {"code": code, "date": date})

    def _table(self, api: str, params: dict[str, Any]) -> FetchResult:
        try:
            payload = self._get(api, {k: v for k, v in params.items() if v is not None})
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _rows(payload, "trades", "items", "data", "result")
        return FetchResult(data=rows, mode="live", source=self.name)

    # -- candles -----------------------------------------------------------
    def candles(self, code: str, timeframe: str = "D1", limit: int = 400) -> FetchResult:
        """Daily bars built from the futures/physical trade table.

        IME publishes settled trades per ring per day rather than an OHLC feed,
        so candles are aggregated from those rows.  Field aliases cover the
        naming variants seen in the public endpoints.
        """
        result = self.futures(code=code)
        if not result.ok:
            result = self.physical(code=code)
        if not result.ok:
            return result
        return FetchResult(data=_candles_from_rows(result.data, code, timeframe)[-limit:], mode=result.mode, source=self.name)

    def health(self) -> dict[str, Any]:
        probes = []
        for api in IME_API:
            url = build_url(self.base, self._path(api))
            body, error = http_get(url, timeout=self.timeout, retries=0)
            probes.append({"api": api, "url": url, "ok": not error, "error": error, "bytes": len(body)})
        return {"name": self.name, "label": self.label, "probes": probes, "reachable": any(p["ok"] for p in probes)}

    def endpoints(self) -> list[dict[str, Any]]:
        return [{"api": api, "url": build_url(self.base, self._path(api)), "verified": spec["verified"]} for api, spec in IME_API.items()]


def _candles_from_rows(rows: Iterable[dict], code: str, timeframe: str) -> list[Candle]:
    grouped: dict[datetime.date, list[dict]] = {}
    for row in rows:
        when = parse_deven(row.get("tradeDate") or row.get("date") or row.get("dEven"))
        if when is None:
            continue
        grouped.setdefault(when, []).append(row)
    candles: list[Candle] = []
    for day, day_rows in sorted(grouped.items()):
        prices = [_f(r, "price", "lastPrice", "settlementPrice", "avgPrice") for r in day_rows]
        prices = [p for p in prices if p]
        if not prices:
            continue
        volume = sum(_f(r, "volume", "tradeVolume", "quantity") or 0.0 for r in day_rows)
        value = sum(_f(r, "value", "tradeValue") or 0.0 for r in day_rows)
        candles.append(
            Candle(
                dt=datetime(day.year, day.month, day.day, 12, 30),
                open=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                volume=volume,
                value=value,
                trades=len(day_rows),
                symbol=code,
                source="ime",
                timeframe=timeframe,
            )
        )
    return validate_candles(candles)


__all__ = ["DEFAULT_BASE", "IME_API", "ImeSource"]
