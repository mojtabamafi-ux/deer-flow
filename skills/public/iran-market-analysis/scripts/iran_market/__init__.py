"""Iran market analysis engine (DeerFlow skill).

Zero hard dependencies: the whole analysis stack (indicators, Elliott, RTM,
ICT, ACT, backtester, signal engine) is standard-library Python.  ``fastapi``,
``uvicorn`` and ``plotly`` are only needed by the interactive dashboard.
"""

from .core import Candle, FetchResult, Instrument, Quote  # noqa: F401

__version__ = "1.0.0"
__all__ = ["Candle", "FetchResult", "Instrument", "Quote", "__version__"]
