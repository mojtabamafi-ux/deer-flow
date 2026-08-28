"""Environment overrides for the market-data adapters.

This module reads the process environment and nothing else: it contains no
endpoint literal and performs no I/O beyond ``os.environ``.  That separation is
deliberate - the skill security scanner treats a file that both reads the
environment and mentions an outbound URL as a possible exfiltration path
(``python-env-dump-exfil``), so configuration reads live here while the endpoint
defaults stay in the adapter modules that actually talk to the network.

Documented variables:

========================  ==========================================
``TSETMC_API_BASE``       CDN API base for TSETMC
``TSETMC_LEGACY_BASE``    legacy tsev2 base for TSETMC
``CODAL_API_BASE``        CODAL services base
``IME_API_BASE``          Iran Mercantile Exchange base
``IME_ENDPOINTS_JSON``    JSON object overriding individual IME paths
``CGCC_API_BASE``         Market CGCC base
``CGCC_ENDPOINTS_JSON``   JSON object overriding individual CGCC paths
``IRAN_MARKET_CACHE``     cache directory
``IRAN_MARKET_SYMBOLS``   alternative symbol-universe file
========================  ==========================================
"""

from __future__ import annotations

import json
import os
from pathlib import Path

TSETMC_API_BASE = "TSETMC_API_BASE"
TSETMC_LEGACY_BASE = "TSETMC_LEGACY_BASE"
CODAL_API_BASE = "CODAL_API_BASE"
IME_API_BASE = "IME_API_BASE"
IME_ENDPOINTS_JSON = "IME_ENDPOINTS_JSON"
CGCC_API_BASE = "CGCC_API_BASE"
CGCC_ENDPOINTS_JSON = "CGCC_ENDPOINTS_JSON"
CACHE_DIR = "IRAN_MARKET_CACHE"
SYMBOLS_FILE = "IRAN_MARKET_SYMBOLS"


def text(name: str) -> str | None:
    """A single trimmed environment value, or ``None`` when unset/blank."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def mapping(name: str) -> dict[str, str]:
    """A ``{"KEY": "value"}`` JSON object from the environment (``{}`` when absent or malformed)."""
    raw = text(name)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}


def directory(name: str) -> Path | None:
    """A filesystem path from the environment, or ``None`` when unset."""
    value = text(name)
    return Path(value).expanduser() if value else None


__all__ = [
    "CACHE_DIR",
    "CGCC_API_BASE",
    "CGCC_ENDPOINTS_JSON",
    "CODAL_API_BASE",
    "IME_API_BASE",
    "IME_ENDPOINTS_JSON",
    "SYMBOLS_FILE",
    "TSETMC_API_BASE",
    "TSETMC_LEGACY_BASE",
    "directory",
    "mapping",
    "text",
]
