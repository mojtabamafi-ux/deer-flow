"""CLI tests for the commands documented in SKILL.md.

The scripts are the skill's public interface, so the documented invocations are
exercised here exactly as written in SKILL.md (with ``--mode sample`` so they
never touch the network).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "skills" / "public" / "iran-market-analysis" / "scripts"
ANALYZE = SCRIPTS / "analyze.py"
DOCTOR = SCRIPTS / "doctor.py"
SERVE = SCRIPTS / "serve_dashboard.py"


def run(script: Path, *args: str, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(SCRIPTS),
    )


def test_analyze_list_prints_the_universe():
    result = run(ANALYZE, "--list")
    assert result.returncode == 0, result.stderr
    assert "46348559193224090" in result.stdout
    assert "فولاد" in result.stdout
    assert "index" in result.stdout and "crypto" in result.stdout


def test_analyze_persian_symbol_reports_in_persian():
    result = run(ANALYZE, "--symbol", "فولاد", "--mode", "sample", "--bars", "120", "--backtest-bars", "400")
    assert result.returncode == 0, result.stderr
    assert "فولاد مبارکه اصفهان" in result.stdout
    assert "پیشنهاد:" in result.stdout
    assert "تضمین" in result.stdout, "the disclaimer must always be printed"
    assert "نرخ پیروزی" in result.stdout


def test_analyze_json_is_machine_readable():
    result = run(ANALYZE, "--code", "BTC_USDT", "--mode", "sample", "--bars", "120", "--backtest-bars", "400", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["meta"]["symbol"] == "BTC_USDT"
    assert payload["meta"]["data_mode"] == "sample"
    assert payload["recommendation"]["action"] in ("buy", "sell", "wait")
    assert payload["chart"]["dt"]


def test_analyze_scan_ranks_a_market():
    result = run(ANALYZE, "--scan", "fx", "--mode", "sample")
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert rows
    strengths = [abs(r["score"]) for r in rows]
    assert strengths == sorted(strengths, reverse=True)


def test_analyze_file_can_be_named_with_symbol(tmp_path):
    csv = tmp_path / "history.csv"
    csv.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        + "\n".join(f"1403/{(i // 28) + 1:02d}/{(i % 28) + 1:02d},{1000 + i},{1015 + i},{990 + i},{1008 + i},{500000}" for i in range(90)),
        encoding="utf-8",
    )
    result = run(ANALYZE, "--file", str(csv), "--symbol", "MYDATA", "--bars", "60", "--backtest-bars", "90")
    assert result.returncode == 0, result.stderr
    assert "MYDATA" in result.stdout
    assert "منبع داده: file" in result.stdout, "an uploaded file must be reported as the source"


def test_analyze_without_a_target_exits_2():
    result = run(ANALYZE, "--mode", "sample")
    assert result.returncode == 2
    assert "--list" in result.stderr


def test_analyze_rejects_conflicting_targets():
    result = run(ANALYZE, "--symbol", "فولاد", "--scan", "gold")
    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr


def test_doctor_json_reports_every_source():
    result = run(DOCTOR, "--json", "--mode", "sample")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert {"health", "endpoints", "universe"} <= set(report)
    assert report["universe"]["symbols"] > 30
    assert {e["api"] for e in report["endpoints"]} >= {"TSETMC_Index", "IME_Futures", "Market_CGCC_Gold"}


def test_doctor_write_without_a_source_of_truth_exits_2():
    result = run(DOCTOR, "--write")
    assert result.returncode == 2
    assert "--write" in result.stderr


def test_serve_dashboard_is_importable_and_reports_missing_deps_cleanly():
    """The dashboard entry point must at least parse; create_app is covered by the API tests."""
    result = run(SERVE, "--help")
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout and "--host" in result.stdout


@pytest.mark.parametrize("script", [ANALYZE, DOCTOR, SERVE])
def test_scripts_run_from_any_working_directory(script, tmp_path):
    """They insert their own directory into sys.path, so cwd must not matter."""
    result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, cwd=str(tmp_path), timeout=120)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
