"""TSETMC adapter (Tehran Stock Exchange / Iran Fara Bourse).

Covers the requested surface:

===================================  =================================================
Requested API                        Endpoint used
===================================  =================================================
``TSETMC_AllSymbols``                ``old.tsetmc.com/tsev2/data/InstSimple/IsPlus/-1``
                                     (+ ``cdn`` variants as fallbacks)
``TSETMC_Index``                     ``cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{index}/…``
``TSETMC_Symbol``                    ``cdn.tsetmc.com/api/Instrument/GetInstrumentView/{insCode}``
``TSETMC_Nav``                       ``cdn.tsetmc.com/api/Fund/GetFundNavInfo/{insCode}``
``TSETMC_Option``                    ``cdn.tsetmc.com/api/Option/GetOptionBoard/{insCode}``
``TSETMC_Transaction``               ``cdn.tsetmc.com/api/Trade/GetTradeHistory/{insCode}/{dEven}/{all}``
``TSETMC_History``                   ``cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{insCode}/{count}``
``TSETMC_Candlestick``               ``old.tsetmc.com/tsev2/data/IntraDayPrice.aspx?i={insCode}`` (M5)
``TSETMC_Shareholder``               ``cdn.tsetmc.com/api/ShareHolder/GetInstrumentShareHolder/{insCode}``
``CODAL_Announcement``               ``www.codal.ir/api/services/v2/info/search/``
===================================  =================================================

.. important::
   These paths are the ones published by TSETMC's own front-end at the time of
   writing and are **not** reachable from the sandbox this skill was built in.
   They are therefore marked ``verified=False`` below and are probed at runtime
   by ``scripts/doctor.py``, which reports the real HTTP status for every
   endpoint.  Every endpoint is overridable through ``config.yaml`` /
   environment variables (``TSETMC_API_BASE``, ``TSETMC_LEGACY_BASE``,
   ``CODAL_API_BASE``) so an operator can repoint the adapter without editing
   code.  Parsers are deliberately tolerant: TSETMC wraps payloads in a named
   envelope (``closingPriceDailyList``, ``instrumentInfo``, …) whose key changes
   between endpoints, so values are found by known field names rather than by a
   fixed key path.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core import Candle, FetchResult, Instrument, Quote
from ..jalali import parse_deven
from . import env_config
from .base import MarketSource, SourceError, validate_candles
from .http_client import build_url, http_json, http_text

DEFAULT_API_BASE = env_config.text(env_config.TSETMC_API_BASE) or "https://cdn.tsetmc.com/api"
DEFAULT_LEGACY_BASE = env_config.text(env_config.TSETMC_LEGACY_BASE) or "https://old.tsetmc.com/tsev2/data"
DEFAULT_CODAL_BASE = env_config.text(env_config.CODAL_API_BASE) or "https://www.codal.ir/api/services/v2"

#: insCode of the total index (شاخص کل / TEDPIX) and the equal-weight index.
INDEX_CODES = {
    "total": "32097828799138957",
    "equal_weight": "67130298613737946",
    "price50": "33623456836198858",
    "free_float": "56717416382860333",
}

#: Endpoint table.  ``verified`` is False until ``doctor.py`` has confirmed the
#: path against the live service; see the module docstring.
TSETMC_API: dict[str, dict[str, Any]] = {
    "TSETMC_AllSymbols": {"path": "/InstSimple/IsPlus/-1", "base": "legacy", "verified": False},
    "TSETMC_Index": {"path": "/ClosingPrice/GetClosingPriceDailyList/{code}/{count}", "base": "api", "verified": False},
    "TSETMC_Symbol": {"path": "/Instrument/GetInstrumentView/{code}", "base": "api", "verified": False},
    "TSETMC_Nav": {"path": "/Fund/GetFundNavInfo/{code}", "base": "api", "verified": False},
    "TSETMC_Option": {"path": "/Option/GetOptionBoard/{code}", "base": "api", "verified": False},
    "TSETMC_Transaction": {"path": "/Trade/GetTradeHistory/{code}/{deven}/{showall}", "base": "api", "verified": False},
    "TSETMC_History": {"path": "/ClosingPrice/GetClosingPriceDailyList/{code}/{count}", "base": "api", "verified": False},
    "TSETMC_Candlestick": {"path": "/IntraDayPrice.aspx?i={code}", "base": "legacy", "verified": False},
    "TSETMC_Shareholder": {"path": "/ShareHolder/GetInstrumentShareHolder/{code}", "base": "api", "verified": False},
    "TSETMC_ClientType": {"path": "/ClientType/GetClientTypeHistory/{code}/1/{count}", "base": "api", "verified": False},
    "TSETMC_ClosingInfo": {"path": "/ClosingPrice/GetClosingPriceInfo/{code}", "base": "api", "verified": False},
    "CODAL_Announcement": {"path": "/info/search/", "base": "codal", "verified": False},
}

#: Flow codes as published by TSETMC (``flow`` field of GetInstrumentView).
FLOW_LABELS = {
    1: "بورس - بازار اول",
    2: "بورس - بازار دوم",
    3: "فرا بورس",
    4: "فرا بورس - بازار پایه",
    5: "بازار پایه فرا بورس",
    6: "بازار سوم",
    7: "بازار چهارم",
    8: "بازار اختیاری",
    9: "بورس انرژی",
    10: "بورس کالا",
}

CACHE_DIR = env_config.directory(env_config.CACHE_DIR) or (Path.home() / ".cache" / "iran-market-analysis")


def _unwrap(payload: Any, *keys: str) -> Any:
    """Pull the useful list/dict out of TSETMC's varying envelopes."""
    if payload is None:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            if key in payload and payload[key] is not None:
                return payload[key]
        # single-key envelope: {"closingPriceDailyList": [...]}
        if len(payload) == 1:
            return next(iter(payload.values()))
        # nested one level deeper
        for value in payload.values():
            if isinstance(value, (list, dict)) and value:
                return value
    return None


def _pick(row: dict, *names: str, default: float | None = None) -> float | None:
    for name in names:
        if name in row and row[name] is not None:
            try:
                return float(row[name])
            except (TypeError, ValueError):
                continue
    return default


def _pick_str(row: dict, *names: str, default: str = "") -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return default


class TsetmcSource(MarketSource):
    name = "tsetmc"
    label = "بورس تهران (TSETMC)"
    markets = ("equity", "index", "fund", "option", "bond", "commodity")
    supports_intraday = True
    supports_short = False  # equity is long-only on the TSE

    def __init__(self, api_base: str = DEFAULT_API_BASE, legacy_base: str = DEFAULT_LEGACY_BASE, codal_base: str = DEFAULT_CODAL_BASE, cache: bool = True, timeout: float = 8.0, retries: int = 2) -> None:
        self.api_base = api_base.rstrip("/")
        self.legacy_base = legacy_base.rstrip("/")
        self.codal_base = codal_base.rstrip("/")
        self.cache = cache
        self.timeout = timeout
        self.retries = retries
        self._instruments: list[Instrument] | None = None

    # -- plumbing ----------------------------------------------------------
    def _url(self, api_name: str, **fmt: Any) -> str:
        spec = TSETMC_API[api_name]
        base = {"api": self.api_base, "legacy": self.legacy_base, "codal": self.codal_base}[spec["base"]]
        return build_url(base, spec["path"].format(**fmt))

    def _json(self, api_name: str, params: dict[str, Any] | None = None, **fmt: Any) -> Any:
        url = self._url(api_name, **fmt)
        payload, error = http_json(url, timeout=self.timeout, retries=self.retries, params=params)
        if error:
            raise SourceError(f"{api_name}: {error} ({url})")
        return payload

    def _cache_path(self, kind: str, key: str) -> Path:
        return CACHE_DIR / self.name / kind / f"{key}.json"

    def _cache_write(self, kind: str, key: str, payload: Any) -> None:
        if not self.cache:
            return
        try:
            path = self._cache_path(kind, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # cache is best-effort

    def _cache_read(self, kind: str, key: str, max_age_seconds: float) -> Any | None:
        if not self.cache:
            return None
        try:
            path = self._cache_path(kind, key)
            if not path.exists():
                return None
            if time.time() - path.stat().st_mtime > max_age_seconds:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    # -- instruments -------------------------------------------------------
    def list_instruments(self, market: str | None = None, query: str | None = None) -> FetchResult:
        """Full instrument list (``TSETMC_AllSymbols``).

        The feed is a delimited text blob.  Three layouts have been observed in
        the wild (``@``-delimited, ``;``-delimited, and JSON), so each is tried
        in turn; the first that yields rows wins and the result is cached for a
        day.
        """
        cached = self._cache_read("instruments", "all", 86400)
        rows: list[dict] = []
        mode = "live"
        if cached:
            rows = cached
            mode = "cache"
        else:
            url = self._url("TSETMC_AllSymbols")
            body, error = http_text(url, timeout=self.timeout, retries=self.retries)
            if error:
                return FetchResult(data=None, mode="live", source=self.name, ok=False, error=f"TSETMC_AllSymbols: {error}")
            rows = _parse_inst_simple(body)
            if not rows:
                return FetchResult(data=None, mode="live", source=self.name, ok=False, error="TSETMC_AllSymbols: unparsable payload")
            self._cache_write("instruments", "all", rows)

        instruments = [_instrument_from_row(r) for r in rows]
        instruments = [i for i in instruments if i.code]
        if market:
            instruments = [i for i in instruments if i.market == market or i.extra.get("flow") == market]
        if query:
            needle = query.strip().lower()
            instruments = [
                i
                for i in instruments
                if needle in i.symbol.lower() or needle in i.name.lower() or needle in i.code
            ]
        self._instruments = instruments
        return FetchResult(data=instruments, mode=mode, source=self.name)

    def symbol_info(self, code: str) -> FetchResult:
        """``TSETMC_Symbol`` - static + last-session metadata for one insCode."""
        try:
            payload = self._json("TSETMC_Symbol", code=code)
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        info = _unwrap(payload, "instrumentInfo") or {}
        if not info:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error="TSETMC_Symbol: empty instrumentInfo")
        flow = info.get("flow")
        instrument = Instrument(
            code=str(code),
            symbol=_pick_str(info, "lVal18", "instrumentID", "lVal18AFC"),
            name=_pick_str(info, "lVal30", "lVal18AFC"),
            market=_market_for_flow(flow),
            exchange="TSETMC",
            source=self.name,
            base_volume=_pick(info, "baseVol"),
            tick_size=_pick(info, "tickSize", "kAjCapValCpsIdx"),
            min_price=_pick(info, "minPrice", "staticThreshold", "pMin"),
            max_price=_pick(info, "maxPrice", "pMax"),
            extra={
                "flow": flow,
                "flow_label": FLOW_LABELS.get(int(flow), "") if isinstance(flow, (int, float)) else "",
                "last_update": info.get("lastUpdate") or info.get("evenDate"),
                "y_val": info.get("yVal"),
                "contract_size": info.get("contractSize"),
                "strike_price": info.get("strikePrice"),
                "expiration": info.get("expirationDate"),
                "isin": _pick_str(info, "iSin", "instrumentID"),
                "raw": info,
            },
        )
        return FetchResult(data=instrument, mode="live", source=self.name)

    def quote(self, code: str) -> FetchResult:
        """``TSETMC_ClosingInfo`` merged into a :class:`Quote`."""
        try:
            payload = self._json("TSETMC_ClosingInfo", code=code)
        except SourceError:
            # Fall back to the last daily bar; the caller still gets a price.
            return super().quote(code)
        row = _unwrap(payload, "closingPriceInfo") or payload or {}
        if isinstance(row, list):
            row = row[0] if row else {}
        last = _pick(row, "pDrCotVal", "pClosing")
        prev = _pick(row, "priceYesterday")
        return FetchResult(
            data=Quote(
                symbol=str(code),
                last=last,
                close=_pick(row, "pClosing", "pDrCotVal"),
                prev_close=prev,
                open=_pick(row, "priceFirst"),
                high=_pick(row, "priceMax"),
                low=_pick(row, "priceMin"),
                volume=_pick(row, "qTotTran5J"),
                value=_pick(row, "qTotCap"),
                trades=int(_pick(row, "zTotTran") or 0),
                status=_pick_str(row, "instrumentState", "yMarNSC"),
                as_of=datetime.now(UTC),
                source=self.name,
            ),
            mode="live",
            source=self.name,
            ok=bool(last),
        )

    # -- history -----------------------------------------------------------
    def history(self, code: str, count: int = 400) -> FetchResult:
        """``TSETMC_History`` - daily closing-price list (raw rows)."""
        try:
            payload = self._json("TSETMC_History", code=code, count=count)
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _unwrap(payload, "closingPriceDailyList")
        if not rows:
            return FetchResult(data=[], mode="live", source=self.name, ok=False, error="TSETMC_History: empty closingPriceDailyList")
        self._cache_write("history", f"{code}_{count}", rows)
        return FetchResult(data=rows, mode="live", source=self.name)

    def candles(self, code: str, timeframe: str = "D1", limit: int = 400) -> FetchResult:
        from ..core import resample

        if timeframe == "M5":
            result = self.intraday(code)
            if result.ok and result.data:
                return FetchResult(data=result.data[-limit:], mode=result.mode, source=self.name)
            return result

        cached = self._cache_read("history", f"{code}_{limit}", 3600)
        if cached:
            rows, mode = cached, "cache"
        else:
            result = self.history(code, limit)
            if not result.ok:
                # serve a stale cache rather than nothing when the API is down
                stale = self._cache_read("history", f"{code}_{limit}", 86400 * 30)
                if stale:
                    rows, mode = stale, "cache"
                else:
                    return result
            else:
                rows, mode = result.data, "live"

        candles = _candles_from_history(rows, code=code, timeframe="D1")
        if timeframe != "D1":
            candles = resample(candles, timeframe)
        return FetchResult(data=candles[-limit:], mode=mode, source=self.name)

    def intraday(self, code: str) -> FetchResult:
        """``TSETMC_Candlestick`` - 5-minute bars for the current session."""
        url = self._url("TSETMC_Candlestick", code=code)
        body, error = http_text(url, timeout=self.timeout, retries=self.retries)
        if error:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=f"TSETMC_Candlestick: {error}")
        rows = _parse_intraday(body)
        if not rows:
            return FetchResult(data=[], mode="live", source=self.name, ok=False, error="TSETMC_Candlestick: unparsable payload")
        return FetchResult(data=_candles_from_intraday(rows, code=code), mode="live", source=self.name)

    # -- extensions --------------------------------------------------------
    def shareholders(self, code: str) -> FetchResult:
        """``TSETMC_Shareholder`` - major holders (real/legal breakdown)."""
        try:
            payload = self._json("TSETMC_Shareholder", code=code)
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _unwrap(payload, "shareHolder", "instrumentShareHolder") or []
        return FetchResult(data=rows, mode="live", source=self.name)

    def client_type_history(self, code: str, count: int = 100) -> FetchResult:
        """``TSETMC_ClientType`` - real vs legal money flow per day."""
        try:
            payload = self._json("TSETMC_ClientType", code=code, count=count)
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _unwrap(payload, "clientType", "clientTypeHistory") or []
        return FetchResult(data=rows, mode="live", source=self.name)

    def transactions(self, code: str, deven: int, show_all: bool = True) -> FetchResult:
        """``TSETMC_Transaction`` - tick/trade tape for one Jalali day."""
        try:
            payload = self._json("TSETMC_Transaction", code=code, deven=deven, showall=str(show_all).lower())
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _unwrap(payload, "tradeHistory", "transaction") or []
        return FetchResult(data=rows, mode="live", source=self.name)

    def option_board(self, code: str) -> FetchResult:
        """``TSETMC_Option`` - option chain for an underlying insCode."""
        try:
            payload = self._json("TSETMC_Option", code=code)
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _unwrap(payload, "optionBoard", "option") or []
        return FetchResult(data=rows, mode="live", source=self.name)

    def nav(self, code: str) -> FetchResult:
        """``TSETMC_Nav`` - NAV history for ETFs/funds."""
        try:
            payload = self._json("TSETMC_Nav", code=code)
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _unwrap(payload, "navInfo", "fundNav", "navHistory") or payload
        return FetchResult(data=rows, mode="live", source=self.name)

    def index(self, kind: str = "total", count: int = 400) -> FetchResult:
        """``TSETMC_Index`` - the index series, returned as candles."""
        code = INDEX_CODES.get(kind, kind)
        try:
            payload = self._json("TSETMC_Index", code=code, count=count)
        except SourceError as exc:
            return FetchResult(data=None, mode="live", source=self.name, ok=False, error=str(exc))
        rows = _unwrap(payload, "closingPriceDailyList")
        if not rows:
            return FetchResult(data=[], mode="live", source=self.name, ok=False, error="TSETMC_Index: empty list")
        return FetchResult(data=_candles_from_history(rows, code=code, timeframe="D1"), mode="live", source=self.name)

    def codal_announcements(self, symbol: str, size: int = 25, page: int = 1) -> FetchResult:
        """``CODAL_Announcement`` - company filings from codal.ir."""
        url = self._url("CODAL_Announcement")
        payload, error = http_json(
            url,
            timeout=self.timeout,
            retries=self.retries,
            params={"symbol": symbol, "size": size, "page": page, "publisher": "false"},
        )
        if error:
            return FetchResult(data=None, mode="live", source="codal", ok=False, error=f"CODAL_Announcement: {error}")
        rows = _unwrap(payload, "TracingNo", "letters", "Items") or []
        return FetchResult(data=rows, mode="live", source="codal")

    # -- health ------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        probes: list[dict[str, Any]] = []
        for api_name in ("TSETMC_AllSymbols", "TSETMC_History", "TSETMC_Candlestick"):
            code = INDEX_CODES["total"]
            if api_name == "TSETMC_History":
                url = self._url(api_name, code=code, count=5)
            else:
                url = self._url(api_name, code=code)
            body, error = http_get_head(url)
            probes.append({"api": api_name, "url": url, "ok": not error, "error": error, "bytes": len(body)})
        return {"name": self.name, "label": self.label, "probes": probes, "reachable": any(p["ok"] for p in probes)}

    def endpoints(self) -> list[dict[str, Any]]:
        """Endpoint table rendered by ``doctor.py`` / the dashboard's source panel."""
        out = []
        for api_name, spec in TSETMC_API.items():
            base = {"api": self.api_base, "legacy": self.legacy_base, "codal": self.codal_base}[spec["base"]]
            out.append({"api": api_name, "url": base + spec["path"], "verified": spec["verified"]})
        return out


# ---------------------------------------------------------------------------
# payload parsers
# ---------------------------------------------------------------------------


def http_get_head(url: str, timeout: float = 8.0) -> tuple[bytes, str]:
    from .http_client import http_get

    return http_get(url, timeout=timeout, retries=0)


def _market_for_flow(flow: Any) -> str:
    """Map TSETMC's ``flow`` code onto this skill's market taxonomy.

    The flow code alone cannot distinguish ETFs from ordinary shares (both trade
    in flows 1-5); the registry refines that using the instrument symbol list.
    """
    try:
        flow_int = int(flow)
    except (TypeError, ValueError):
        return "equity"
    if flow_int == 10:
        return "commodity"
    if flow_int == 8:
        return "option"
    return "equity"


def _instrument_from_row(row: dict) -> Instrument:
    flow = row.get("flow") or row.get("Flow")
    return Instrument(
        code=str(row.get("insCode") or row.get("InsCode") or row.get("code") or ""),
        symbol=str(row.get("instrumentID") or row.get("InstrumentID") or row.get("lVal18") or ""),
        name=str(row.get("lVal30") or row.get("LVal30") or row.get("lVal18AFC") or ""),
        market=_market_for_flow(flow),
        exchange="TSETMC",
        source="tsetmc",
        extra={"flow": flow, "flow_label": FLOW_LABELS.get(int(flow), "") if isinstance(flow, (int, float)) else "", "cGrValCot": row.get("cGrValCot")},
    )


def _parse_inst_simple(body: str) -> list[dict]:
    """Parse ``InstSimple/IsPlus`` blobs.

    Observed layouts (all tolerated):

    * ``@ID@InsCode@InstrumentID@LatinName@CValMne@LVal18@...`` one record per ``@``-block
    * ``;``-delimited records with ``|``-separated fields
    * a JSON array of objects
    """
    text = body.strip().lstrip("\ufeff")
    if not text:
        return []
    if text[0] in "[{":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
        rows = _unwrap(payload) or []
        return [r for r in rows if isinstance(r, dict)]

    rows: list[dict] = []
    if "@" in text:
        # Records are ``@``-prefixed; fields inside are ``|``- or `,`-separated.
        for chunk in text.split("@"):
            chunk = chunk.strip()
            if not chunk:
                continue
            fields = [f for f in chunk.replace("|", ",").split(",") if f != ""]
            rows.append(_fields_to_row(fields))
        return [r for r in rows if r.get("insCode")]

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = [f for f in line.replace("|", ",").split(",") if f != ""]
        rows.append(_fields_to_row(fields))
    return [r for r in rows if r.get("insCode")]


def _fields_to_row(fields: Sequence[str]) -> dict:
    """Best-effort positional mapping of the InstSimple field list."""
    row: dict[str, Any] = {}
    for i, value in enumerate(fields):
        if i == 0:
            row["insCode"] = value
        elif i == 1:
            row["instrumentID"] = value
        elif i == 2:
            row["lVal30"] = value
        elif i == 3:
            row["lVal18"] = value
        elif i == 4:
            row["flow"] = value
    # If the first token is not a 15-19 digit insCode, the layout differs; drop it.
    code = str(row.get("insCode", ""))
    if not (10 <= len(code) <= 20 and code.isdigit()):
        return {}
    return row


def _candles_from_history(rows: Iterable[dict], code: str, timeframe: str) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        when = parse_deven(row.get("dEven"))
        if when is None:
            continue
        close = _pick(row, "pClosing", "pDrCotVal", "priceLast")
        last = _pick(row, "pDrCotVal", "pClosing")
        open_ = _pick(row, "priceFirst", "open")
        high = _pick(row, "priceMax", "high")
        low = _pick(row, "priceMin", "low")
        if close is None or open_ is None or high is None or low is None:
            continue
        candles.append(
            Candle(
                dt=datetime(when.year, when.month, when.day),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=_pick(row, "qTotTran5J", "qTotTran", "volume") or 0.0,
                value=_pick(row, "qTotCap", "value") or 0.0,
                trades=int(_pick(row, "zTotTran", "trades") or 0),
                last=last,
                symbol=str(row.get("insCode") or code),
                source="tsetmc",
                timeframe=timeframe,
            )
        )
    return validate_candles(candles)


def _parse_intraday(body: str) -> list[dict]:
    """Parse ``IntraDayPrice.aspx`` CSV: ``HH:MM;P;last;open;high;low;volume``."""
    rows: list[dict] = []
    for line in body.strip().splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 6 or ":" not in parts[0]:
            continue

        def num(index: int) -> float | None:
            try:
                return float(parts[index])
            except (ValueError, IndexError):
                return None

        rows.append(
            {
                "time": parts[0],
                "last": num(2),
                "open": num(3),
                "high": num(4),
                "low": num(5),
                "volume": num(6) if len(parts) > 6 else 0.0,
            }
        )
    return rows


def _candles_from_intraday(rows: Iterable[dict], code: str) -> list[Candle]:
    today = datetime.now().date()
    candles: list[Candle] = []
    for row in rows:
        hour, _, minute = row["time"].partition(":")
        try:
            when = datetime(today.year, today.month, today.day, int(hour), int(minute))
        except ValueError:
            continue
        values = [v for v in (row["last"], row["open"], row["high"], row["low"]) if v]
        if not values:
            continue
        candles.append(
            Candle(
                dt=when,
                open=row["open"] or row["last"] or values[0],
                high=row["high"] or max(values),
                low=row["low"] or min(values),
                close=row["last"] or values[-1],
                volume=row["volume"] or 0.0,
                symbol=code,
                source="tsetmc",
                timeframe="M5",
            )
        )
    return validate_candles(candles)


__all__ = [
    "DEFAULT_API_BASE",
    "DEFAULT_CODAL_BASE",
    "DEFAULT_LEGACY_BASE",
    "FLOW_LABELS",
    "INDEX_CODES",
    "TSETMC_API",
    "TsetmcSource",
]
