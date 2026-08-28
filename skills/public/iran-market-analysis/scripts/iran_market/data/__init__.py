"""Market data adapters: TSETMC, IME, Market_CGCC, user files, synthetic fallback."""

from .base import MarketSource, SourceError, validate_candles  # noqa: F401
from .file_source import FileSource  # noqa: F401
from .ime import ImeSource  # noqa: F401
from .market_cgcc import CgccSource  # noqa: F401
from .registry import DataSourceRegistry, build_registry  # noqa: F401
from .sample import synthetic_candles  # noqa: F401
from .tsetmc import TsetmcSource  # noqa: F401

__all__ = [
    "CgccSource",
    "DataSourceRegistry",
    "FileSource",
    "ImeSource",
    "MarketSource",
    "SourceError",
    "TsetmcSource",
    "build_registry",
    "synthetic_candles",
    "validate_candles",
]
