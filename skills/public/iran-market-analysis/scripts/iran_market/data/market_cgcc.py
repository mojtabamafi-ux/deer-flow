"""Market_CGCC adapter - commodities, gold, FX and crypto.

Requested surface: ``Market_CGCC`` for کامودیتی، طلا، ارز و ارز دیجیتال.

.. important::
   ``www.market.cgcc.ir`` did not resolve from the build environment
   (``Could not resolve host``), so the paths below are **unverified** and the
   endpoint table is fully overridable through ``CGCC_API_BASE`` and
   ``CGCC_ENDPOINTS_JSON``.  ``scripts/doctor.py`` probes them and reports the
   real status; the dashboard shows the probe result next to every price so a
   reader always knows which feed a number came from.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from ..core import Candle, FetchResult, Instrument
from ..jalali import parse_deven
from . import env_config
from .base import MarketSource, SourceError, validate_candles
from .http_client import build_url, http_get, http_json

DEFAULT_BASE = env_config.text(env_config.CGCC_API_BASE) or "https://www.market.cgcc.ir"

CGCC_API: dict[str, dict[str, Any]] = {
    "Market_CGCC_Commodity": {"path": "/api/v1/commodities", "market": "commodity", "verified": False},
    "Market_CGCC_Gold": {"path": "/api/v1/gold", "market": "gold", "verified": False},
    "Market_CGCC_Forex": {"path": "/api/v1/forex", "market": "fx", "verified": False},
    "Market_CGCC_Crypto": {"path": "/api/v1/crypto", "market": "crypto", "verified": False},
    "Market_CGCC_History": {"path": "/api/v1/history/{code}", "market": "history", "verified": False},
    "Market_CGCC_Symbols": {"path": "/api/v1/symbols", "market": "all", "verified": False},
}


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
                return float(str(row[name]).replace(",", ""))
            except (TypeError, ValueError):
                continue
    return None


def _s(row: dict, *names: str, default: str = "") -> str:
    for name in names:
        if row.get(name) not in (None, ""):
            return str(row[name])
    return default


class CgccSource(MarketSource):
    name = "market_cgcc"
    label = "بازار کامودیتی / طلا / ارز (Market_CGCC)"
    markets = ("commodity", "gold", "fx", "crypto")
    supports_intraday = True
    supports_short = True

    def __init__(self, base: str = DEFAULT_BASE, timeout: float = 8.0, retries: int = 2) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.overrides = env_config.mapping(env_config.CGCC_ENDPOINTS_JSON)

    def _path(self, api: str) -> str:
        return self.overrides.get(api) or CGCC_API[api]["path"]

    def _get(self, api: str, params: dict[str, Any] | None = None, **fmt: Any) -> Any:
        url = build_url(self.base, self._path(api).format(**fmt) if fmt else self._path(api), params)
        payload, error = http_json(url, timeout=self.timeout, retries=self.retries)
        if error:
            raise SourceError(f"{api}: {error} ({url})")
        return payload

    def list_instruments(self, market: str | None = None, query: str | None = None) -> FetchResult:
        apis = {
            "commodity": "Market_CGCC_Commodity",
            "gold": "Market_CGCC_Gold",
            "fx": "Market_CGCC_Forex",
            "crypto": "Market_CGCC_Crypto",
        }
        instruments: list[Instrument] = []
        errors: list[str] = []
        for mkt, api in apis.items():
            if market and market != mkt:
                continue
            try:
                payload = self._get(api)
            except SourceError as exc:
                errors.append(str(exc))
                continue
            for row in _rows(payload, "symbols", "items", "data"):
                instruments.append(
                    Instrument(
                        code=_s(row, "code", "symbol", "id"),
                        symbol=_s(row, "symbol", "code", "name"),
                        name=_s(row, "name", "title", "symbol"),
                        market=mkt,
                        exchange="Market_CGCC",
                        source=self.name,
                        extra={"raw": row},
                    )
                )
        if not instruments:
            return FetchResult(data=[], mode="live", source=self.name, ok=False, error="; ".join(errors) or "Market_CGCC: empty payload")
        if query:
            needle = query.strip().lower()
            instruments = [i for i in instruments if needle in i.symbol.lower() or needle in i.name.lower()]
        return FetchResult(data=instruments, mode="live", source=self.name)

    def candles(self, code: str, timeframe: str = "D1", limit: int = 400) -> FetchResult:
        try:
            payload = self._get("Market_CGCC_History", {"timeframe": timeframe, "limit": limit}, code=code)
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _rows(payload, "history", "candles", "data", "items")
        if not rows:
            return FetchResult(data=[], mode="live", source=self.name, ok=False, error="Market_CGCC_History: empty payload")
        return FetchResult(data=_candles_from_rows(rows, code, timeframe)[-limit:], mode="live", source=self.name)

    def board(self, market: str) -> FetchResult:
        api = {
            "commodity": "Market_CGCC_Commodity",
            "gold": "Market_CGCC_Gold",
            "fx": "Market_CGCC_Forex",
            "crypto": "Market_CGCC_Crypto",
        }.get(market)
        if not api:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=f"unknown CGCC market: {market}")
        try:
            payload = self._get(api)
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        return FetchResult(data=_rows(payload, "items", "symbols", "data"), mode="live", source=self.name)

    def health(self) -> dict[str, Any]:
        probes = []
        for api in CGCC_API:
            url = build_url(self.base, self._path(api).replace("{code}", "sample"))
            body, error = http_get(url, timeout=self.timeout, retries=0)
            probes.append({"api": api, "url": url, "ok": not error, "error": error, "bytes": len(body)})
        return {"name": self.name, "label": self.label, "probes": probes, "reachable": any(p["ok"] for p in probes)}

    def endpoints(self) -> list[dict[str, Any]]:
        return [{"api": api, "url": build_url(self.base, self._path(api)), "verified": spec["verified"]} for api, spec in CGCC_API.items()]


def _candles_from_rows(rows: Iterable[dict], code: str, timeframe: str) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        raw_when = row.get("date") or row.get("time") or row.get("timestamp") or row.get("dEven")
        when = parse_deven(raw_when)
        if when is None and isinstance(raw_when, (int, float)) and raw_when > 1_000_000_000:
            when = datetime.fromtimestamp(float(raw_when) / (1000 if raw_when > 1e12 else 1)).date()
        if when is None:
            continue
        open_ = _f(row, "open", "o")
        high = _f(row, "high", "h")
        low = _f(row, "low", "l")
        close = _f(row, "close", "c", "last", "price")
        if close is None:
            continue
        candles.append(
            Candle(
                dt=datetime(when.year, when.month, when.day, 12, 30),
                open=open_ or close,
                high=high or close,
                low=low or close,
                close=close,
                volume=_f(row, "volume", "v", "qty") or 0.0,
                value=_f(row, "value", "turnover") or 0.0,
                trades=int(_f(row, "trades", "count") or 0),
                symbol=code,
                source="market_cgcc",
                timeframe=timeframe,
            )
        )
    return validate_candles(candles)


__all__ = ["CGCC_API", "CgccSource", "DEFAULT_BASE"]
