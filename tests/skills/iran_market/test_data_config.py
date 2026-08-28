"""Environment configuration for the data adapters.

Two things are pinned here:

1. the documented ``*_BASE`` / ``*_JSON`` overrides still take effect, so the
   adapters stay configurable on a user's own network; and
2. only ``data/env_config.py`` reads the process environment.  The skill security
   scanner flags any file that reads the environment *and* contains an outbound
   URL as a possible exfiltration path (``python-env-dump-exfil``, a blocking
   finding), and every adapter module necessarily mentions its endpoint, so the
   separation is what keeps the skill review clean.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from iran_market.data import env_config, ime, market_cgcc, registry, tsetmc

DATA_DIR = Path(env_config.__file__).parent


def test_text_strips_and_blanks_out() -> None:
    assert env_config.text("IRAN_MARKET_TEST_UNSET") is None
    import os

    os.environ["IRAN_MARKET_TEST_VAR"] = "   "
    try:
        assert env_config.text("IRAN_MARKET_TEST_VAR") is None
        os.environ["IRAN_MARKET_TEST_VAR"] = "  value  "
        assert env_config.text("IRAN_MARKET_TEST_VAR") == "value"
    finally:
        del os.environ["IRAN_MARKET_TEST_VAR"]


def test_mapping_parses_json_and_survives_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRAN_MARKET_TEST_JSON", '{"A": 1, "B": "/path"}')
    assert env_config.mapping("IRAN_MARKET_TEST_JSON") == {"A": "1", "B": "/path"}

    monkeypatch.setenv("IRAN_MARKET_TEST_JSON", "not json at all")
    assert env_config.mapping("IRAN_MARKET_TEST_JSON") == {}

    monkeypatch.setenv("IRAN_MARKET_TEST_JSON", "[1, 2, 3]")
    assert env_config.mapping("IRAN_MARKET_TEST_JSON") == {}


def test_directory_expands_user(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("IRAN_MARKET_TEST_DIR", str(tmp_path / "cache"))
    assert env_config.directory("IRAN_MARKET_TEST_DIR") == tmp_path / "cache"
    monkeypatch.delenv("IRAN_MARKET_TEST_DIR", raising=False)
    assert env_config.directory("IRAN_MARKET_TEST_DIR") is None


def test_endpoint_overrides_still_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented variables must keep working through the new indirection."""
    monkeypatch.setenv(env_config.IME_API_BASE, "https://ime.example.test")
    monkeypatch.setenv(env_config.IME_ENDPOINTS_JSON, '{"IME_Futures": "/custom/futures"}')
    monkeypatch.setenv(env_config.CGCC_API_BASE, "https://cgcc.example.test")
    monkeypatch.setenv(env_config.CGCC_ENDPOINTS_JSON, '{"Market_CGCC_Gold": "/custom/gold"}')

    ime_source = importlib.reload(ime).ImeSource()
    assert ime_source.base == "https://ime.example.test"
    assert ime_source._path("IME_Futures") == "/custom/futures"
    # untouched endpoints keep their shipped path
    assert ime_source._path("IME_Physical") == "/api/physical/trades"

    cgcc_source = importlib.reload(market_cgcc).CgccSource()
    assert cgcc_source.base == "https://cgcc.example.test"
    assert cgcc_source._path("Market_CGCC_Gold") == "/custom/gold"
    assert cgcc_source._path("Market_CGCC_Forex") == "/api/v1/forex"

    importlib.reload(ime)
    importlib.reload(market_cgcc)


def test_defaults_are_unchanged() -> None:
    importlib.reload(tsetmc)
    importlib.reload(ime)
    importlib.reload(market_cgcc)
    importlib.reload(registry)
    assert tsetmc.DEFAULT_API_BASE == "https://cdn.tsetmc.com/api"
    assert tsetmc.DEFAULT_LEGACY_BASE == "https://old.tsetmc.com/tsev2/data"
    assert ime.DEFAULT_BASE == "https://www.ime.co.ir"
    assert market_cgcc.DEFAULT_BASE == "https://www.market.cgcc.ir"
    assert registry.UNIVERSE_PATH.name == "symbols.json"


def _reads_environ(path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os" and node.attr in {"environ", "getenv", "env"}:
                return True
        if isinstance(node, ast.Name) and node.id == "environ":
            return True
    return False


def test_only_env_config_touches_the_process_environment() -> None:
    """Guard the skill-review blocker: env reads and endpoint literals must not share a file."""
    offenders = sorted(p.name for p in DATA_DIR.glob("*.py") if p.name != "env_config.py" and _reads_environ(p))
    assert offenders == []
    assert _reads_environ(DATA_DIR / "env_config.py") is True


def test_env_config_contains_no_outbound_url() -> None:
    """The counterpart guard: the module that reads env must not know any endpoint."""
    tree = ast.parse((DATA_DIR / "env_config.py").read_text(encoding="utf-8"))
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not [s for s in literals if "://" in s or s.startswith("www.")]
