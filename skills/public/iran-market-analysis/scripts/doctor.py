#!/usr/bin/env python3
"""Probe every data source and (optionally) verify the symbol codes.

The Iranian endpoints are **not** reachable from every network.  This script is
the supported way to find out what a given machine can actually reach, and to
repair the symbol table:

    python doctor.py                      # probe TSETMC / IME / Market_CGCC
    python doctor.py --verify-symbols     # also resolve every insCode live
    python doctor.py --verify-symbols --write   # rewrite references/symbols.json
                                                  with the codes that matched
    python doctor.py --discover --write         # add every instrument the exchange
                                                  lists (fills the ETF/fund market,
                                                  which ships empty on purpose)
    python doctor.py --json

Nothing is written unless ``--write`` is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from iran_market.core import to_jsonable  # noqa: E402
from iran_market.data.registry import UNIVERSE_PATH, DataSourceRegistry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify-symbols", action="store_true", help="resolve every TSETMC code through the live API and compare the returned symbol")
    parser.add_argument("--discover", action="store_true", help="pull the full TSETMC instrument list (TSETMC_AllSymbols) and add the instruments that are not in the universe yet")
    parser.add_argument("--discover-limit", type=int, default=200, help="how many discovered instruments to add (default 200)")
    parser.add_argument("--write", action="store_true", help="persist --verify-symbols / --discover results into references/symbols.json")
    parser.add_argument("--json", action="store_true", help="machine readable output")
    parser.add_argument("--mode", default="auto")
    args = parser.parse_args(argv)

    if not (args.verify_symbols or args.discover) and args.write:
        print("--write بدون --verify-symbols یا --discover کاری انجام نمی‌دهد.", file=sys.stderr)
        return 2

    registry = DataSourceRegistry(mode=args.mode)
    report = registry.doctor(verify_symbols=args.verify_symbols)
    discovered: list[dict] = []
    if args.discover:
        discovered = _discover(registry, limit=args.discover_limit)
        report["discovered"] = discovered

    if args.json:
        print(json.dumps(to_jsonable(report), ensure_ascii=False, indent=2))
        return 0

    health = report.get("health") or {}
    print(f"حالت پیکربندی‌شده: {health.get('mode')}  |  حالت مؤثر: {health.get('effective_mode')}")
    print(f"دسترسی زنده به حداقل یک منبع: {'بله' if health.get('live_available') else 'خیر'}")
    print()
    for source in health.get("sources") or []:
        print(f"== {source.get('label') or source.get('name')}")
        for probe in source.get("probes") or []:
            mark = "OK " if probe.get("ok") else "ERR"
            print(f"  [{mark}] {probe.get('api', ''):<22} {probe.get('url')}")
            if not probe.get("ok"):
                print(f"        {probe.get('error')}")
    print()
    print("جدول endpoint ها (verified=False یعنی مسیر هنوز روی سرویس زنده تأیید نشده است):")
    for endpoint in report.get("endpoints") or []:
        print(f"  [{'v' if endpoint.get('verified') else '?'}] {endpoint['api']:<24} {endpoint['url']}")

    verification = (report.get("universe") or {}).get("verification")
    if verification:
        print()
        matched = sum(1 for v in verification if v["status"] == "verified")
        mismatched = sum(1 for v in verification if v["status"] == "mismatch")
        failed = sum(1 for v in verification if v["status"] == "error")
        print(f"نتیجه راستی‌آزمایی کدها: {matched} تأیید، {mismatched} ناسازگار، {failed} خطا")
        for item in verification:
            if item["status"] in ("mismatch", "error"):
                print(f"  [{item['status']}] {item['code']} {item.get('symbol')} -> {item.get('returned_symbol') or item.get('reason')}")
        if args.write:
            _write_universe(registry, verification)
    if discovered:
        print()
        print(f"کشفinstrument: {len(discovered)} مورد جدید از TSETMC_AllSymbols")
        by_market: dict[str, int] = {}
        for item in discovered:
            by_market[item["market"]] = by_market.get(item["market"], 0) + 1
        for market, count in sorted(by_market.items()):
            print(f"  {market}: {count}")
        if not args.write:
            print("  (برای ذخیره در references/symbols.json گزینه --write را اضافه کنید)")

    if args.write and (verification or discovered):
        _write_universe(payload_universe_path(), verification or [], discovered)
    return 0


def payload_universe_path() -> Path:
    return Path(UNIVERSE_PATH)


def _discover(registry: DataSourceRegistry, limit: int) -> list[dict]:
    """Add instruments from the exchange's own list - never invented codes."""
    result = registry.tsetmc.list_instruments()
    if not result.ok or not result.data:
        print(f"کشف نمادها ناموفق بود: {result.error}", file=sys.stderr)
        return []
    known = {str(s["code"]) for s in registry.symbols()}
    out: list[dict] = []
    for instrument in result.data:
        code = str(instrument.code)
        if code in known:
            continue
        market = _market_of(instrument)
        entry = {
            "code": code,
            "symbol": instrument.symbol,
            "name": instrument.name,
            "market": market,
            "sector": instrument.sector,
            "source": "tsetmc",
            "verified": True,  # it came from the exchange itself
        }
        registry.register_universe_entry(entry)
        known.add(code)
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def _market_of(instrument) -> str:
    """Map a TSETMC instrument to one of the universe's market buckets."""
    text = f"{instrument.market} {instrument.sector} {instrument.extra.get('flow', '')} {instrument.name}".lower()
    if "صندوق" in text or "fund" in text or "etf" in text:
        return "fund"
    if "اختیار" in text or "option" in text:
        return "option"
    if "آتی" in text or "future" in text:
        return "commodity"
    if "شاخص" in text or "index" in text:
        return "index"
    if "فرابورس" in text or "فرا بورس" in text:
        return "equity"
    return "equity"


def _write_universe(path: Path, verification: list[dict], discovered: list[dict]) -> None:
    """Persist verified flags, repair mismatched codes and append discovered ones."""
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"markets": [], "symbols": []}
    symbols = payload.setdefault("symbols", [])
    existing = {str(e.get("code")) for e in symbols}
    for entry in discovered:
        if str(entry["code"]) not in existing:
            symbols.append(entry)
            existing.add(str(entry["code"]))
    by_code = {str(v["code"]): v for v in verification}
    changed = 0
    for entry in payload.get("symbols", []):
        result = by_code.get(str(entry.get("code")))
        if not result:
            continue
        if result["status"] == "verified":
            entry["verified"] = True
            changed += 1
        elif result["status"] == "mismatch":
            entry["verified"] = False
            entry["note"] = f"live symbol is {result.get('returned_symbol')} ({result.get('returned_name')})"
            changed += 1
    payload["verified"] = all(e.get("verified") for e in payload.get("symbols", []) if e.get("source") == "tsetmc")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nفایل {path} به‌روزرسانی شد ({changed} مورد).")


if __name__ == "__main__":
    raise SystemExit(main())
