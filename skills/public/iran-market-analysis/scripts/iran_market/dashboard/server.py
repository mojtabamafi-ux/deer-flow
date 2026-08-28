"""FastAPI dashboard for the iran-market-analysis skill.

Serves the RTL Persian single-page dashboard plus a small JSON API.  The
analysis engine is imported directly - there is no separate worker, no database
and no background job, so what the API returns is exactly what the CLI returns.

Runtime dependencies for the dashboard only: ``fastapi``, ``uvicorn``, ``plotly``
(the chart library is served from the installed ``plotly`` package so the page
works with no CDN and no internet access).

Everything binds ``0.0.0.0`` and accepts any origin on purpose: the dashboard is
meant to run inside sandboxes and container previews where the browser reaches
it through a proxy on a different host.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core import to_jsonable
from ..data.registry import DataSourceRegistry
from ..engine import analyze, scan_market
from ..signals import DISCLAIMER

STATIC_DIR = Path(__file__).parent / "static"

TIMEFRAMES = ("M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")


def _plotly_js_path() -> Path | None:
    """Locate plotly.min.js inside the installed plotly package (no CDN needed)."""
    try:
        import plotly
    except ImportError:
        return None
    candidate = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    return candidate if candidate.exists() else None


def create_app(mode: str = "auto", registry: DataSourceRegistry | None = None):
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

    app = FastAPI(title="Iran Market Analysis Dashboard", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    reg = registry or DataSourceRegistry(mode=mode)

    @app.get("/", response_class=HTMLResponse)
    def index() -> Any:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            raise HTTPException(status_code=500, detail="dashboard assets missing")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @app.get("/static/{name}")
    def static(name: str) -> Any:
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.exists():
            raise HTTPException(status_code=404, detail="not found")
        media = {"js": "application/javascript", "css": "text/css"}.get(path.suffix.lstrip("."), "application/octet-stream")
        return FileResponse(path, media_type=f"{media}; charset=utf-8")

    @app.get("/vendor/plotly.min.js")
    def plotly_bundle() -> Any:
        path = _plotly_js_path()
        if path is None:
            return PlainTextResponse(
                "// plotly is not installed. Run: pip install plotly\n", status_code=503, media_type="application/javascript"
            )
        return FileResponse(path, media_type="application/javascript; charset=utf-8")

    @app.get("/api/health")
    def health() -> Any:
        """Cheap health check: no network probing (see /api/sources?force=true)."""
        return {
            "ok": True,
            "mode": reg.mode,
            "symbols": len(reg.symbols()),
            "markets": [m["id"] for m in reg.markets()],
            "timeframes": list(TIMEFRAMES),
            "health_cache": to_jsonable(reg.health_cached()),
            "disclaimer": DISCLAIMER,
        }

    @app.get("/api/sources")
    def sources(force: bool = False) -> Any:
        report = reg.health(force=force)
        return {**to_jsonable(report), "endpoints": to_jsonable(reg.endpoints())}

    @app.get("/api/markets")
    def markets() -> Any:
        return to_jsonable(reg.markets())

    @app.get("/api/symbols")
    def symbols(market: str | None = None, q: str | None = None) -> Any:
        return to_jsonable(reg.symbols(market=market, query=q))

    @app.get("/api/analysis")
    def analysis(symbol: str, tf: str = "D1", limit: int = 400, mode: str | None = None, backtest_limit: int = 1200, with_backtest: bool = True) -> Any:
        if tf not in TIMEFRAMES:
            raise HTTPException(status_code=400, detail=f"unsupported timeframe: {tf}")
        limit = max(30, min(limit, 3000))
        backtest_limit = max(limit, min(backtest_limit, 4000))
        return analyze(
            symbol,
            reg,
            timeframe=tf,
            limit=limit,
            mode=mode,
            backtest_limit=backtest_limit,
            with_backtest=with_backtest,
        )

    @app.get("/api/scan")
    def scan(market: str | None = None, tf: str = "D1", limit: int = 300, mode: str | None = None) -> Any:
        return to_jsonable(scan_market(reg, market=market, timeframe=tf, limit=limit, mode=mode))

    @app.post("/api/upload")
    async def upload(payload: dict) -> Any:
        symbol = str(payload.get("symbol") or "").strip()
        text = str(payload.get("text") or "")
        tf = str(payload.get("timeframe") or "D1")
        if not symbol or not text:
            raise HTTPException(status_code=400, detail="symbol and text are required")
        result = reg.upload(symbol, text, timeframe=tf, name=str(payload.get("name") or ""))
        if not result.ok:
            raise HTTPException(status_code=400, detail=result.error)
        return {"ok": True, "symbol": symbol, "bars": len(result.data or []), "mode": result.mode}

    @app.get("/api/doctor")
    def doctor(verify_symbols: bool = False) -> Any:
        return to_jsonable(reg.doctor(verify_symbols=verify_symbols))

    @app.exception_handler(Exception)
    async def unhandled(_request: Any, exc: Exception) -> Any:  # pragma: no cover
        return JSONResponse(status_code=500, content={"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    return app


def run(host: str = "0.0.0.0", port: int = 8010, mode: str = "auto", reload: bool = False) -> None:
    """Start the dashboard.  ``0.0.0.0`` so container/preview proxies can reach it."""
    import uvicorn

    os.environ.setdefault("IRAN_MARKET_MODE", mode)
    uvicorn.run(create_app(mode=mode), host=host, port=port, reload=reload, log_level="info")


__all__ = ["STATIC_DIR", "TIMEFRAMES", "create_app", "run"]
