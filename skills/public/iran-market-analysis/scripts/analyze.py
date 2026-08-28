#!/usr/bin/env python3
"""One-shot analysis CLI - the entry point the DeerFlow agent calls.

Examples
--------
    # full analysis, machine readable
    python analyze.py --symbol فولاد --json > analysis.json

    # human readable report for the agent to summarise
    python analyze.py --code 46348559193224090 --timeframe D1 --bars 400

    # explicit offline mode (never touches the network)
    python analyze.py --symbol BTC_USDT --mode sample

    # scan a whole market and rank it by confluence score
    python analyze.py --scan equity --timeframe D1

    # analyse a user supplied CSV
    python analyze.py --file history.csv --symbol MYDATA

Exit code is 0 on success, 2 on bad arguments, 3 when no data could be produced.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from iran_market.core import to_jsonable  # noqa: E402
from iran_market.data.registry import DataSourceRegistry  # noqa: E402
from iran_market.engine import analyze, scan_market  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--symbol", help="symbol as listed by --list (e.g. فولاد, BTC_USDT); with --file it names the uploaded series")
    target.add_argument("--code", help="instrument code / TSETMC insCode")
    target.add_argument("--scan", metavar="MARKET", help="scan a market (equity, gold, fx, crypto, commodity, index, all)")
    target.add_argument("--list", action="store_true", help="list the known symbols and exit")
    parser.add_argument("--file", help="analyse a local CSV/JSON history instead of an API symbol (combine with --symbol to name it)")
    parser.add_argument("--timeframe", "-t", default="D1", help="M5 M15 M30 H1 H4 D1 W1 MN1 (default D1)")
    parser.add_argument("--bars", type=int, default=400, help="visible chart window (default 400)")
    parser.add_argument("--backtest-bars", type=int, default=1200, help="history loaded for the win-rate measurement (default 1200)")
    parser.add_argument("--mode", choices=("auto", "live", "sample"), default="auto", help="auto falls back to synthetic data when the APIs are unreachable")
    parser.add_argument("--no-backtest", action="store_true", help="skip the backtest (faster)")
    parser.add_argument("--json", action="store_true", help="print the full analysis as JSON")
    parser.add_argument("--allow-short", action="store_true", help="allow short setups (futures/commodity/fx/crypto)")
    return parser


def resolve_symbol(registry: DataSourceRegistry, args: argparse.Namespace) -> str | None:
    if args.code:
        return args.code
    if args.symbol:
        matches = registry.symbols(query=args.symbol)
        exact = [m for m in matches if m.get("symbol") == args.symbol or m.get("name") == args.symbol]
        if exact:
            return str(exact[0]["code"])
        if matches:
            return str(matches[0]["code"])
        return args.symbol
    return None


def print_report(result: dict) -> None:
    meta = result.get("meta") or {}
    rec = result.get("recommendation") or {}
    snap = result.get("snapshot") or {}
    print("=" * 78)
    print(f"{meta.get('name')} ({meta.get('symbol')}) - {meta.get('market')}")
    print(f"منبع داده: {meta.get('data_mode')} | تایم‌فریم: {meta.get('timeframe')} | کندل‌ها: {meta.get('chart_bars')} از {meta.get('history_bars')}")
    if meta.get("synthetic"):
        print("هشدار: داده‌ها مصنوعی (sample) هستند و قیمت واقعی بازار نیستند.")
    if meta.get("live_error"):
        print(f"خطای منبع زنده: {meta['live_error']}")
    print("-" * 78)
    price = snap.get("price") or {}
    trend = snap.get("trend") or {}
    def r(value, digits=2):
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "-"

    print(f"قیمت: {r(price.get('close'))}  تغییر: {r(price.get('change_pct'))}%  روند: {trend.get('label')}  ADX: {r(trend.get('adx'), 1)}")
    print(f"امواج الیوت: {(result.get('elliott') or {}).get('summary')}")
    rtm = (result.get("rtm") or {}).get("summary") or {}
    print(f"RTM: {rtm.get('zones_total')} ناحیه، {rtm.get('zones_live')} فعال، {rtm.get('zones_mitigated')} شکسته‌شده")
    act = (result.get("act") or {}).get("active") or {}
    print(f"ACT: {act.get('phase_fa') or 'بدون فاز'}")
    print("-" * 78)
    print(f"پیشنهاد: {rec.get('action_fa')}  (امتیاز {rec.get('score')}, اطمینان {rec.get('confidence')})")
    for key, label in (("entry", "ورود"), ("stop", "استاپ"), ("target", "هدف ۱"), ("target2", "هدف ۲"), ("rr", "R:R")):
        if rec.get(key) is not None:
            print(f"  {label}: {rec[key]}")
    for reason in rec.get("reasons") or []:
        print(f"  + {reason}")
    for risk in rec.get("risks") or []:
        print(f"  ! {risk}")
    print("-" * 78)
    print(f"سیگنال‌ها ({len(result.get('signals') or [])}):")
    for signal in (result.get("signals") or [])[:12]:
        measured = signal.get("measured") or {}
        extra = f" | نرخ پیروزی تاریخی {round(100 * measured['win_rate'], 1)}% روی {measured['trades']} معامله" if measured.get("trades") else ""
        print(f"  [{signal['engine']}] {'خرید' if signal['direction'] > 0 else 'فروش'} {signal.get('title') or signal.get('setup')} | ورود {signal['entry']} استاپ {signal['stop']} هدف {signal.get('target')} R:R {signal.get('rr')}{extra}")
    ranking = (result.get("setups") or {}).get("ranking") or []
    if ranking:
        print("-" * 78)
        print("ستاپ‌ها بر اساس نرخ پیروزی اندازه‌گیری‌شده روی همین داده‌ها:")
        for row in ranking[:10]:
            win = "-" if row["win_rate"] is None else f"{round(100 * row['win_rate'], 1)}%"
            print(f"  {row['name'][:38]:40s} معاملات {row['trades']:3d}  پیروزی {win:>7}  امید {row['expectancy_r']}  درجه {row['grade']}")
    overall = (result.get("backtest") or {}).get("overall") or {}
    if overall.get("trades"):
        print(f"کل: {overall['trades']} معامله | پیروزی {round(100 * overall['win_rate'], 1)}% | امید {overall['expectancy_r']}R | ضریب سود {overall['profit_factor']} | افت {overall['max_drawdown_r']}R")
    print("-" * 78)
    print(result.get("disclaimer", ""))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = DataSourceRegistry(mode=args.mode)

    if args.list:
        for market in registry.markets():
            print(f"\n# {market['label']} ({market['id']}) - {market['symbols']} نماد")
            for item in registry.symbols(market=market["id"]):
                print(f"  {item['code']:<22} {item.get('symbol', ''):<12} {item.get('name', '')}")
        return 0

    if args.scan:
        market = None if args.scan == "all" else args.scan
        rows = scan_market(registry, market=market, timeframe=args.timeframe, limit=min(args.bars, 300))
        print(json.dumps(to_jsonable(rows), ensure_ascii=False, indent=2))
        return 0

    if args.file:
        uploaded = registry.upload_file(args.symbol or Path(args.file).stem, args.file, timeframe=args.timeframe)
        if not uploaded.ok:
            print(f"بارگذاری فایل ناموفق: {uploaded.error}", file=sys.stderr)
            return 3
        symbol = args.symbol or Path(args.file).stem
    else:
        symbol = resolve_symbol(registry, args)
        if not symbol:
            print("یک نماد مشخص کنید (--symbol یا --code) یا --list را ببینید.", file=sys.stderr)
            return 2

    result = analyze(
        symbol,
        registry,
        timeframe=args.timeframe,
        limit=args.bars,
        mode=args.mode,
        allow_short=args.allow_short or None,
        with_backtest=not args.no_backtest,
        backtest_limit=args.backtest_bars,
    )
    if not result.get("signals") and (result.get("meta") or {}).get("error"):
        print(f"داده‌ای تولید نشد: {result['meta']['error']}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
