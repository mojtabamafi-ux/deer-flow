#!/usr/bin/env python3
"""Run the interactive dashboard.

    python serve_dashboard.py --port 8010            # bind 0.0.0.0:8010
    python serve_dashboard.py --mode sample          # never touch the network
    python serve_dashboard.py --port 8010 --host 127.0.0.1

``--host`` defaults to 0.0.0.0 so the dashboard is reachable through container
and sandbox preview proxies.  Requires ``fastapi``, ``uvicorn`` and ``plotly``;
the analysis CLI works without them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0 so previews/proxies can reach it)")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--mode", choices=("auto", "live", "sample"), default="auto")
    parser.add_argument("--reload", action="store_true", help="uvicorn autoreload (development)")
    args = parser.parse_args(argv)

    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("داشبورد به fastapi/uvicorn/plotly نیاز دارد:\n  pip install fastapi uvicorn plotly", file=sys.stderr)
        return 3

    from iran_market.dashboard.server import run

    print(f"داشبورد روی http://{args.host}:{args.port} (حالت داده: {args.mode})")
    run(host=args.host, port=args.port, mode=args.mode, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
