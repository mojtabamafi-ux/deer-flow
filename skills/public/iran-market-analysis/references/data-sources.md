# Data sources

The skill talks to three Iranian providers plus CODAL, and falls back to
deterministic offline data when they are unreachable.

**Every endpoint below ships with `verified: false`.** They were written from
public documentation and best-effort knowledge of the APIs; none of them could
be confirmed against a live server from the environment where this skill was
built. Run `doctor.py` on a machine with access before trusting live numbers,
and repair the paths with the environment variables below if a provider changed
its API.

## Routing

Every request goes through `DataSourceRegistry.candles(code, timeframe, limit)`:

1. **user-uploaded file** - anything registered through `/api/upload`,
   `registry.upload()` or `analyze.py --file`;
2. **live provider** for the symbol's market (TSETMC / IME / CGCC);
3. **in-memory cache** (10 minutes) so refreshing the dashboard does not refetch;
4. **deterministic synthetic data** (`sample`) - seeded from the symbol and
   timeframe, so the same symbol always produces the same series.

The mode actually used (`live` / `file` / `cache` / `sample`) is on every
result, is rendered as a banner in the dashboard, and must be reported to the
user. `--mode sample` forces step 4 and never touches the network; `--mode live`
fails instead of falling back.

Uploaded files always win over the network - even in sample mode, because
"offline" must never mean "ignore the user's data".

## TSETMC (`TSETMC_*`, `CODAL_*`)

| API id | Default URL |
| --- | --- |
| `TSETMC_AllSymbols` | `https://old.tsetmc.com/tsev2/data/InstSimple/IsPlus/-1` |
| `TSETMC_Index` / `TSETMC_History` | `https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{code}/{count}` |
| `TSETMC_Symbol` | `https://cdn.tsetmc.com/api/Instrument/GetInstrumentView/{code}` |
| `TSETMC_ClosingInfo` | `https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceInfo/{code}` |
| `TSETMC_Candlestick` | `https://old.tsetmc.com/tsev2/data/IntraDayPrice.aspx?i={code}` |
| `TSETMC_Nav` | `https://cdn.tsetmc.com/api/Fund/GetFundNavInfo/{code}` |
| `TSETMC_Option` | `https://cdn.tsetmc.com/api/Option/GetOptionBoard/{code}` |
| `TSETMC_Transaction` | `https://cdn.tsetmc.com/api/Trade/GetTradeHistory/{code}/{deven}/{showall}` |
| `TSETMC_Shareholder` | `https://cdn.tsetmc.com/api/ShareHolder/GetInstrumentShareHolder/{code}` |
| `TSETMC_ClientType` | `https://cdn.tsetmc.com/api/ClientType/GetClientTypeHistory/{code}/1/{count}` |
| `CODAL_Announcement` | `https://www.codal.ir/api/services/v2/info/search/` |

Dates come back as Jalali `dEven` integers (`14030415`) and are converted with
`jalali.parse_deven`. The all-symbols feed is a delimited text blob; three
layouts are tried (`@`-delimited, `;`-delimited, JSON) and the first that
produces rows wins.

Environment variables: `TSETMC_API_BASE`, `TSETMC_LEGACY_BASE`, `CODAL_API_BASE`.

## IME (`IME_*`)

| API id | Default path under `IME_API_BASE` |
| --- | --- |
| `IME_Futures` | `/api/futures/trades` |
| `IME_Option` | `/api/option/chain` |
| `IME_Certificate` | `/api/certificate/trades` |
| `IME_Fund` | `/api/fund/nav` |
| `IME_Physical` | `/api/physical/trades` |
| `IME_Instruments` | `/api/instruments` |

Environment variables: `IME_API_BASE`, `IME_ENDPOINTS_JSON` (a JSON object of
`{"IME_Futures": "/new/path", ...}` that overrides individual paths).

## Market CGCC (`Market_CGCC_*`)

| API id | Default path under `CGCC_API_BASE` |
| --- | --- |
| `Market_CGCC_Commodity` | `/api/v1/commodities` |
| `Market_CGCC_Gold` | `/api/v1/gold` |
| `Market_CGCC_Forex` | `/api/v1/forex` |
| `Market_CGCC_Crypto` | `/api/v1/crypto` |
| `Market_CGCC_History` | `/api/v1/history/{code}` |
| `Market_CGCC_Symbols` | `/api/v1/symbols` |

Environment variables: `CGCC_API_BASE`, `CGCC_ENDPOINTS_JSON`.

## Symbol universe

`references/symbols.json` - 7 markets, 38 codes:

| Market | id | Symbols | Short selling |
| --- | --- | --- | --- |
| شاخص‌ها | `index` | 4 | no |
| سهام بورس و فرابورس | `equity` | 19 | no |
| صندوق‌های قابل معامله | `fund` | 0 (fill with `--discover`) | no |
| بورس کالا (IME) | `commodity` | 5 | yes |
| طلا و سکه | `gold` | 4 | yes |
| ارز | `fx` | 3 | yes |
| رمزارز | `crypto` | 3 | yes |

Codes are best-effort and carry `"verified": false`. The `fund` market is
deliberately empty: inventing ETF `insCode`s would be worse than having none.
Fill it from the exchange itself:

```bash
python scripts/doctor.py --discover --write          # adds TSETMC_AllSymbols instruments
python scripts/doctor.py --verify-symbols --write    # repairs/flags the existing codes
```

`--discover` marks what it adds as `verified: true` because the rows come from
the exchange's own instrument list. `--verify-symbols` compares each `insCode`
against `TSETMC_Symbol` and reports `verified` / `mismatch` / `error` per code;
`--write` only sets `verified: true` for codes the live API confirmed and leaves
a note on mismatches instead of guessing.

Override the whole table with `IRAN_MARKET_SYMBOLS=/path/to/symbols.json`.

## Cache

`~/.cache/iran-market-analysis/{source}/{kind}/{key}.json` (override with
`IRAN_MARKET_CACHE`). Instrument lists are cached 24 h, price history 1 h with a
30-day stale fallback, so a flaky connection degrades instead of failing.

## Doctor

`scripts/doctor.py` is the supported connectivity check:

```bash
python scripts/doctor.py                     # probe every endpoint, print OK/ERR
python scripts/doctor.py --json              # machine readable
python scripts/doctor.py --discover --write  # grow the universe from the exchange
python scripts/doctor.py --verify-symbols --write
```

It reports the configured mode, the effective mode, per-endpoint reachability
with the exact error, the endpoint table with its `verified` flags, and the
universe summary (`symbols`, `verified`, `markets`, `path`). Nothing is written
without `--write`.

The dashboard's `/api/health` never probes the network - a cold 3-source probe
with 8-second timeouts would block page load. Probing only happens when the user
clicks "بررسی اتصال منابع" (`/api/sources?force=true`) or runs `doctor.py`.
