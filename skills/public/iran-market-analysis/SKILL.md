---
name: iran-market-analysis
description: Use this skill when the user asks about Iranian financial markets - Tehran Stock Exchange (TSE/Fara Bourse), Iran Mercantile Exchange (IME) futures and physical rings, gold coins, FX rates, or crypto - and wants technical analysis, chart reading, Elliott wave counts, RTM/ICT/ACT supply-demand analysis, measured setup win rates, or a buy/sell/wait recommendation. Also use it to run the interactive Persian dashboard for these markets. It fetches history (or falls back to deterministic offline data), computes about 40 indicator families, draws Elliott waves, backtests every setup on the same series, and returns an explained, risk-bounded recommendation. It never guarantees profit and never trades.
metadata:
  market: iranian-markets
  language: fa
  runtime: python3
  dashboard: scripts/serve_dashboard.py
compatibility: Requires Python 3.11+. The analysis core uses only the standard library. The optional dashboard additionally needs fastapi, uvicorn and plotly; install them with pip only when the user asks for the dashboard. Live data needs network access to TSETMC, IME or Market CGCC endpoints; without it the skill automatically uses deterministic offline sample data and says so.
---

# Iran Market Analysis

## Overview

Quantitative technical analysis for Iranian markets: TSE/Fara Bourse equities and
indices, IME futures/certificates/funds/physical rings, gold, FX and crypto.

One analysis run produces:

- **Indicators** - about 40 families (EMA stack, RSI, MACD, Bollinger/Keltner
  squeeze, ADX/DMI, ATR, SuperTrend, PSAR, Ichimoku, VWAP, OBV/MFI/CMF,
  Donchian, volume profile, pivot points, Fibonacci, linear-regression slope).
- **Elliott waves** - a rule-checked 5-wave count plus the A-B-C correction and
  any nested minor-degree count inside wave 3, with chart overlay coordinates,
  invalidation level and Fibonacci targets.
- **RTM (Read The Market)** - every supply/demand zone (RBR, DBR, DBD, RBD) with
  FTR, quartiles, golden zone, MPL, mitigation status, and all resulting buy and
  sell signals.
- **ICT** - order blocks, fair value gaps, liquidity sweeps, BOS/CHoCH, dealing
  range with premium/discount and OTE.
- **ACT** - Accumulation → Compression → Trend: Wyckoff-style range detection,
  volatility squeeze, and breakout/breakdown confirmation with ADX and volume.
- **Setups + measured statistics** - 13 classical setups plus the engine setups,
  each backtested on the same series so the reported win rate is measured, not
  claimed.
- **Recommendation** - buy / sell / wait with entry, stop, targets, R:R, the
  factor scores behind it, supporting signals, and explicit risks.

## When to use it

- "فولاد را تحلیل کن" / "analyse فولاد مبارکه" / any TSE, IME, gold, FX or crypto symbol.
- "داشبورد تحلیل را اجرا کن" - run the interactive dashboard.
- "کدام ستاپ‌ها بیشترین نرخ پیروزی را دارند" - measured setup ranking.
- "داده‌های من را تحلیل کن" - analyse a user CSV/JSON history file.

## Workflow

### Step 1: pick the symbol

List the known universe (7 markets, ~40 codes) if the user is not specific:

```bash
python /mnt/skills/public/iran-market-analysis/scripts/analyze.py --list
```

`--symbol` accepts the Persian ticker (فولاد), `--code` the raw code
(46348559193224090). Unknown symbols are still analysed if the data source can
resolve them.

### Step 2: run the analysis

```bash
# human-readable report (use this to write the answer)
python /mnt/skills/public/iran-market-analysis/scripts/analyze.py --symbol فولاد --timeframe D1 --bars 400

# full machine-readable payload (chart series, overlays, every signal)
python /mnt/skills/public/iran-market-analysis/scripts/analyze.py --symbol فولاد --json

# a whole market, ranked by confluence strength
python /mnt/skills/public/iran-market-analysis/scripts/analyze.py --scan equity

# offline on purpose (never touches the network)
python /mnt/skills/public/iran-market-analysis/scripts/analyze.py --symbol BTC_USDT --mode sample

# the user's own history file
python /mnt/skills/public/iran-market-analysis/scripts/analyze.py --file /mnt/user-data/uploads/history.csv --symbol MYDATA
```

Timeframes: `M5 M15 M30 H1 H4 D1 W1 MN1`. `--bars` is the visible chart window;
`--backtest-bars` (default 1200) is how much history the win-rate measurement
loads. Add `--allow-short` only for futures/commodity/FX/crypto - it is off by
default because short selling is not possible on TSE equities.

### Step 3: answer in Persian, from the payload

Report, in this order: price and trend → Elliott count → RTM/ICT/ACT state →
the signals with entry/stop/target/R:R → the recommendation section → measured
setup statistics with their sample sizes → risks. Explain *why* each signal
fires in one plain sentence. Always state the data source (`live`, `file` or
`sample`) and, when it is `sample`, that the prices are synthetic.

### Step 4 (optional): the interactive dashboard

```bash
python /mnt/skills/public/iran-market-analysis/scripts/serve_dashboard.py --host 0.0.0.0 --port 8010
```

Bind `0.0.0.0` so container/sandbox previews can reach it. The page is a Persian
RTL dashboard: symbol picker across all markets, Plotly candlestick chart with
toggleable indicators and overlays (Elliott waves, RTM zones, ICT order blocks
and FVGs, ACT range), signal cards, engine panels, the setup-ranking table and a
highlighted recommendation card. It serves Plotly locally - no CDN, no internet
needed. `--mode sample` keeps it fully offline.

## Data sources and connectivity

Live adapters exist for TSETMC (history, instruments, NAV, options, trades,
shareholders, client type), CODAL announcements, IME (futures, options,
certificates, funds, physical) and Market CGCC (commodity, gold, FX, crypto).
Base URLs and paths are overridable by environment variable, and every endpoint
is listed with a `verified` flag because Iranian endpoints are unreachable from
many networks.

Routing order for every request: **user-uploaded file → live provider → 10-minute
cache → deterministic synthetic data**. The mode actually used is reported on
every result, so never present sample data as market data.

Check what a machine can reach, and repair the symbol table:

```bash
python /mnt/skills/public/iran-market-analysis/scripts/doctor.py                # probe every endpoint
python /mnt/skills/public/iran-market-analysis/scripts/doctor.py --verify-symbols --json
python /mnt/skills/public/iran-market-analysis/scripts/doctor.py --verify-symbols --write   # rewrite references/symbols.json
```

`--write` is the only command that modifies files, and it only marks codes that
the live API confirmed.

## Rules that always apply

1. **Never guarantee profit, ROI, or that a move will happen.** Win rates in the
   output are measured on the analysed history; always quote the trade count
   next to them, and flag anything under 20 trades as `نمونه کم`.
2. **No personalised financial advice.** The output is an analytical reading of
   the data, not a recommendation tailored to anyone's financial situation.
3. **Never trade, place or cancel an order automatically.** There is no trading
   path in this skill; present signals and let the user decide.
4. **Only report what the data shows.** If a source is unreachable, say so and
   say which mode the numbers came from. Never invent prices, codes or levels.
5. Long-only for TSE equities. When the engine sees a bearish setup there, report
   it as a risk warning for holders, not as a sell order.

## Reference files

- `references/methodology.md` - exact indicator definitions, RTM/ICT/ACT/Elliott
  rules, backtest assumptions and the grading scale. Read it before explaining a
  number you did not compute yourself.
- `references/data-sources.md` - endpoint list, environment variables, response
  handling, symbol-table repair.
- `references/symbols.json` - the symbol universe (override with the
  `IRAN_MARKET_SYMBOLS` environment variable).

## Limitations

- Endpoint schemas for TSETMC/IME/CGCC are best-effort: they were written
  defensively and could not be verified against the live services. Run `doctor.py`
  on a machine with access before trusting live numbers.
- Intraday timeframes need an intraday-capable source; `TSETMC_Candlestick`
  covers this, but sample mode synthesises the bars.
- Elliott counts, RTM zones and ACT phases are probabilistic readings of price
  structure. They are inputs to a decision, not predictions.
