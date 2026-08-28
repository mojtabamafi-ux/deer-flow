"""End-to-end engine and dashboard API tests (offline / sample data only)."""

from __future__ import annotations

import json
import math

import pytest
from iran_market.core import to_jsonable
from iran_market.data.registry import DataSourceRegistry
from iran_market.engine import analyze, scan_market

SAMPLE_SYMBOLS = ["46348559193224090", "BTC_USDT", "USD_IRR", "SAFFRON_IME"]

CHART_SERIES = ("ema9", "ema20", "ema50", "bb_upper", "bb_lower", "rsi14", "macd_line", "adx", "atr14", "supertrend", "psar", "vwap", "senkou_a")


@pytest.fixture(scope="module")
def registry() -> DataSourceRegistry:
    return DataSourceRegistry(mode="sample")


@pytest.mark.parametrize("code", SAMPLE_SYMBOLS)
def test_analyze_returns_a_complete_json_safe_payload(registry, code):
    result = analyze(code, registry, timeframe="D1", limit=120, backtest_limit=400)
    json.dumps(result, ensure_ascii=False)  # to_jsonable already ran; this proves it
    to_jsonable(result)

    meta = result["meta"]
    assert meta["symbol"] == code
    assert meta["candles"]["bars"] == meta["chart_bars"] <= 120, "meta.candles must describe the chart window, not the history"
    assert meta["history_bars"] == 400
    assert meta["data_mode"] == "sample"
    assert meta["synthetic"] is True
    for key in ("chart", "snapshot", "structure", "patterns", "elliott", "rtm", "ict", "act", "setups", "backtest", "signals", "recommendation", "disclaimer"):
        assert key in result, key
    assert not math.isnan(result["snapshot"]["price"]["close"])


def test_recommendation_is_explained_and_risk_bounded(registry):
    result = analyze("46348559193224090", registry, timeframe="D1", limit=120, backtest_limit=400)
    rec = result["recommendation"]
    assert rec["action"] in ("buy", "sell", "wait")
    assert rec["action_fa"]
    assert rec["key_factors"] and rec["confluence"]
    assert -100.0 <= rec["score"] <= 100.0
    assert 0.0 <= rec["confidence"] <= 1.0
    assert isinstance(rec["risks"], list)
    if rec["action"] != "wait":
        assert rec["entry"] and rec["stop"] and rec["target"]
        assert rec["rr"] >= 1.0
        if rec["action"] == "buy":
            assert rec["stop"] < rec["entry"] < rec["target"]
        else:
            assert rec["target"] < rec["entry"] < rec["stop"]
        assert rec["reasons"], "an actionable recommendation must state why"
    assert "تضمین" in result["disclaimer"]
    assert "تضمین سود" in rec["disclaimer"]


def test_win_rate_is_measured_with_a_sample_size_and_never_promised(registry):
    result = analyze("46348559193224090", registry, timeframe="D1", limit=120, backtest_limit=600)
    by_setup = result["backtest"]["by_setup"]
    assert by_setup
    for setup_id, metric in by_setup.items():
        assert metric["trades"] >= 1
        assert 0.0 <= metric["win_rate"] <= 1.0
        assert "expectancy_r" in metric and "profit_factor" in metric
        assert metric["sample_ok"] is (metric["trades"] >= 20)
        assert setup_id
    for signal in result["signals"]:
        measured = signal["measured"]
        assert "trades" in measured and "win_rate" in measured and "sample_ok" in measured


def test_shorts_only_appear_where_the_market_allows_them(registry):
    equity = analyze("46348559193224090", registry, timeframe="D1", limit=120, backtest_limit=400)
    assert equity["meta"]["allow_short"] is False
    assert all(s["direction"] > 0 for s in equity["signals"])
    assert equity["recommendation"]["action"] in ("buy", "wait")
    assert set((equity["backtest"]["overall"].get("directions") or {}).keys()) <= {"long"}

    fx = analyze("USD_IRR", registry, timeframe="D1", limit=120, backtest_limit=400)
    assert fx["meta"]["allow_short"] is True
    assert "short_signals_suppressed" in equity["meta"]


def test_a_long_only_market_never_gets_an_actionable_short_but_is_still_warned(registry):
    """The Elliott engine used to emit counter-trend sells for TSE equities."""
    result = analyze("46348559193224090", registry, timeframe="D1", limit=120, backtest_limit=400)
    assert result["meta"]["allow_short"] is False
    assert all(s["direction"] > 0 for s in result["signals"])
    assert result["recommendation"]["action"] != "sell"
    # the bearish read must still reach the user - as a risk, not as an order
    if result["meta"]["short_signals_suppressed"]:
        assert any("فروش استقراضی" in risk for risk in result["recommendation"]["risks"])


def test_chart_payload_is_clipped_to_the_chart_window(registry):
    result = analyze("46348559193224090", registry, timeframe="D1", limit=100, backtest_limit=400)
    chart = result["chart"]
    assert len(chart["dt"]) == 100
    for key in ("open", "high", "low", "close", "volume", *CHART_SERIES):
        assert len(chart[key]) == 100, key
    assert chart["close"][-1] == pytest.approx(result["snapshot"]["price"]["close"])
    assert chart["dt"] == sorted(chart["dt"])


def test_overlay_blocks_are_present_and_clipped_to_the_chart_window(registry):
    result = analyze("46348559193224090", registry, timeframe="D1", limit=100, backtest_limit=400)
    first_dt = result["chart"]["dt"][0]
    for zone in result["rtm"]["overlay"]:
        assert zone["x0"] >= first_dt
        assert set(zone) >= {"x0", "x1", "y0", "y1", "kind", "type"}
    assert set(result["ict"]["overlay"]) >= {"order_blocks", "fvg", "sweeps", "dealing_range"}
    assert isinstance(result["elliott"]["overlay"], dict)
    assert set(result["elliott"]["overlay"]) == {"lines", "annotations"}
    assert isinstance(result["act"]["overlay"], list)
    for box in result["act"]["overlay"]:
        assert box["x0"] >= first_dt


def test_elliott_payload_carries_the_count_and_its_summary(registry):
    result = analyze("BTC_USDT", registry, timeframe="D1", limit=120, backtest_limit=400)
    elliott = result["elliott"]
    assert elliott["summary"]
    assert elliott["pivots"]
    assert elliott["current"] is None or "targets" in elliott["current"]
    for wave in elliott["waves"]:
        assert wave["label"] in ("1", "2", "3", "4", "5", "A", "B", "C")
        assert wave["start_index"] < wave["end_index"]


def test_scan_market_returns_rows_ranked_by_confluence(registry):
    rows = scan_market(registry, market="gold", timeframe="D1", limit=120)
    assert rows
    # ranked by the strength of the read, whichever direction it points in
    strengths = [abs(r["score"]) for r in rows]
    assert strengths == sorted(strengths, reverse=True)
    for row in rows:
        assert row["action"] in ("buy", "sell", "wait")
        assert row["price"] > 0
        assert row["code"]


def test_upload_csv_is_analysed_like_any_other_symbol(registry, tmp_path):
    csv = tmp_path / "history.csv"
    csv.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        + "\n".join(f"1403/{(i // 28) + 1:02d}/{(i % 28) + 1:02d},{100 + i:.1f},{102 + i:.1f},{99 + i:.1f},{101 + i:.1f},{1000 + i}" for i in range(80)),
        encoding="utf-8",
    )
    uploaded = registry.upload_file("MYDATA", csv, timeframe="D1")
    assert uploaded.ok, uploaded.error
    result = analyze("MYDATA", registry, timeframe="D1", limit=80, backtest_limit=80, with_backtest=False)
    assert result["meta"]["data_mode"] == "file"
    assert result["meta"]["chart_bars"] > 0
    assert result["recommendation"]["action"] in ("buy", "sell", "wait")
    assert result["recommendation"]["key_factors"]


def test_insufficient_history_degrades_instead_of_raising(registry):
    result = analyze("46348559193224090", registry, timeframe="MN1", limit=5, backtest_limit=5)
    assert result["recommendation"]["action"] == "wait"
    assert result["signals"] == [] or result["meta"].get("error") or result["meta"]["history_bars"] >= 30


# ---------------------------------------------------------------------------
# dashboard HTTP API
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from iran_market.dashboard.server import create_app

    return TestClient(create_app(mode="sample"))


def test_dashboard_serves_the_ui_and_assets(client):
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="chart"' in page.text
    assert 'lang="fa"' in page.text and 'dir="rtl"' in page.text
    for asset in ("/static/app.js", "/static/styles.css"):
        response = client.get(asset)
        assert response.status_code == 200, asset
        assert len(response.content) > 1000


def test_dashboard_serves_plotly_from_the_installed_package(client):
    response = client.get("/vendor/plotly.min.js")
    assert response.status_code == 200
    assert len(response.content) > 1_000_000
    assert "Plotly" in response.content[:200_000].decode("utf-8", "ignore")


def test_dashboard_health_never_probes_the_network(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "sample"
    assert body["health_cache"] is None, "/api/health must not trigger a cold exchange probe on page load"
    assert body["disclaimer"]
    assert body["symbols"] > 0
    assert "D1" in body["timeframes"]


def test_dashboard_routes_expose_every_key_the_front_end_reads(client):
    markets = client.get("/api/markets").json()
    assert isinstance(markets, list) and markets
    assert {"id", "label", "symbols"} <= set(markets[0])

    symbols = client.get("/api/symbols", params={"market": "equity"}).json()
    assert isinstance(symbols, list) and symbols
    assert {"code", "symbol", "name", "market"} <= set(symbols[0])

    analysis = client.get("/api/analysis", params={"symbol": "46348559193224090", "tf": "D1", "limit": 120, "backtest_limit": 400}).json()
    assert analysis["meta"]["chart_bars"] > 0
    assert analysis["chart"]["dt"] and analysis["chart"]["close"]
    assert isinstance(analysis["signals"], list)
    assert analysis["recommendation"]["action"] in ("buy", "sell", "wait")
    assert analysis["recommendation"]["confluence"]
    assert isinstance(analysis["setups"]["ranking"], list)
    assert isinstance(analysis["rtm"]["overlay"], list)
    assert "overlay" in analysis["ict"]
    assert "overlay" in analysis["elliott"]
    assert "active" in analysis["act"]


def test_dashboard_rejects_an_unknown_timeframe(client):
    response = client.get("/api/analysis", params={"symbol": "46348559193224090", "tf": "H7"})
    assert response.status_code == 400


def test_dashboard_reports_an_unknown_symbol_instead_of_crashing(client):
    response = client.get("/api/analysis", params={"symbol": "NOT_A_SYMBOL", "limit": 100})
    assert response.status_code in (200, 404)
    body = response.json()
    assert body.get("meta", {}).get("error") or body.get("ok") is False or "error" in json.dumps(body, ensure_ascii=False).lower()


def test_dashboard_upload_route_accepts_csv_text(client):
    payload = "Date,Open,High,Low,Close,Volume\n" + "\n".join(
        f"1403/{(i // 28) + 1:02d}/{(i % 28) + 1:02d},{100 + i},{102 + i},{99 + i},{101 + i},{1000 + i}" for i in range(80)
    )
    response = client.post("/api/upload", json={"symbol": "UPLOADED", "text": payload})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["bars"] > 0
    # and it is analysable like any other symbol
    assert client.get("/api/analysis", params={"symbol": "UPLOADED", "limit": 60}).status_code == 200


def test_dashboard_upload_route_rejects_an_empty_body(client):
    assert client.post("/api/upload", json={"symbol": "", "text": ""}).status_code == 400


def test_dashboard_scan_route(client):
    rows = client.get("/api/scan", params={"market": "fx"}).json()
    assert isinstance(rows, list) and rows
    assert {"code", "action", "score", "price"} <= set(rows[0])


def test_dashboard_sources_route_documents_every_requested_endpoint(client):
    body = client.get("/api/sources").json()
    names = {e["api"] for e in body["endpoints"]}
    assert all({"url", "api", "verified"} <= set(e) for e in body["endpoints"])
    # the endpoint families the analysis is supposed to be wired to
    assert {
        "TSETMC_AllSymbols",
        "TSETMC_Index",
        "TSETMC_Symbol",
        "TSETMC_Nav",
        "TSETMC_Option",
        "TSETMC_Transaction",
        "TSETMC_History",
        "TSETMC_Candlestick",
        "TSETMC_Shareholder",
        "CODAL_Announcement",
        "IME_Futures",
        "IME_Option",
        "IME_Certificate",
        "IME_Fund",
        "IME_Physical",
        "Market_CGCC_Commodity",
        "Market_CGCC_Gold",
        "Market_CGCC_Forex",
        "Market_CGCC_Crypto",
    } <= names
    # none of them has been confirmed against a live server from this environment
    assert all(e["verified"] is False for e in body["endpoints"])
    assert body["mode"] == "sample"
    assert isinstance(body["sources"], list) and body["sources"]


def test_dashboard_doctor_route_reports_the_universe_without_verifying_symbols(client):
    body = client.get("/api/doctor").json()
    assert {"health", "endpoints", "universe"} <= set(body)
    assert body["universe"]["symbols"] > 30
    assert body["universe"]["verified"] == 0, "no code has been confirmed against the live API here"
    assert "verification" not in body["universe"]
