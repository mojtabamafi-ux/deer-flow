# Methodology

Everything the skill reports is defined here. If a number in the output is not
explained by this document, treat it as unverified.

## Conventions

- **Warm-up is `NaN`, never `0`.** A `0` RSI and a `0` ATR are real values in
  some libraries and would generate false signals during warm-up.
- **EMA is SMA-seeded** (TradingView convention): the first `N-1` values are
  `NaN`, the seed is the SMA of the first `N` values, then `α = 2/(N+1)`.
- **Wilder smoothing (`rma`, `α = 1/N`)** is used for RSI, ATR, ADX/DMI and MFI.
  RSI(14) on the canonical Wilder dataset reproduces 70.4641 / 66.2496 /
  66.4809 / 69.3469.
- **Every series is causal.** Bar `i` uses only bars `≤ i`. Indicators are
  computed once over the whole history and the chart window is sliced
  afterwards, so analysing 1200 bars and showing the last 400 gives exactly the
  same values as analysing 400 bars - with far more trades to measure.
- Jalali conversion uses the jalaali-js algorithm with truncating division
  (`_div` / `_mod` in `jalali.py`). Python's `//` floors toward −∞ and shifts
  dates by a year; do not "simplify" it. Verified against `jdatetime` over
  5000 consecutive days and against the Nowruz reference dates.

## Indicators (`indicators.py`)

`compute_all(candles, timeframe)` returns aligned series; `snapshot(ind)`
reduces them to the last bar grouped as `price / trend / momentum / volatility
/ volume / levels`.

| Family | Members |
| --- | --- |
| Moving averages | SMA, EMA, WMA, DEMA, TEMA, HMA, EMA stack 9/20/50/100/200 |
| Momentum | RSI(6,14), StochRSI, Stochastic, MACD(12,26,9), CCI(20), Williams %R, ROC, momentum, Awesome Oscillator |
| Trend | ADX/+DI/−DI(14), SuperTrend(10,3), Parabolic SAR, Ichimoku (tenkan/kijun/senkou A+B, cloud shifted 26 bars), linear-regression slope + R² |
| Volatility | ATR(14), ATR%, Bollinger(20,2) with %B and bandwidth, Keltner(20,1.5), TTM squeeze, historical volatility(20), Donchian(20) |
| Volume | OBV (+EMA20), MFI(14), VWAP (session anchor), A/D line, Chaikin MF(20), relative volume(20) |
| Structure | ZigZag (percentage), Williams fractals, pivot points (classic/Fibonacci/Woodie), Fibonacci retracement + extension, volume profile with POC/VAH/VAL (70% value area) |

**ZigZag invariant.** Pivots alternate high/low, their indices strictly
increase, and only the trailing pivot may be `provisional`. A new leg always
starts searching *strictly after* the pivot just confirmed - otherwise a single
wide-range bar (high−low larger than the threshold) makes the scanner ping-pong
on the same bar and emit a pivot pair on every later bar. That bug produced 166
"pivots" for 300 bars and is pinned by `test_zigzag_alternates_and_only_ever_marks_the_last_pivot_provisional`.

**Fibonacci.** `fibonacci_levels(start, end, direction)` treats `start` as the
origin of the move: `span = sign * |end - start|`, levels = `start + span*v`.
Retracement `0.0 → start`, `1.0 → end`; extensions 1.272/1.618/2.0/2.618 fall
beyond the end of the move. Levels always stay inside the swing for `v ≤ 1`.

## Elliott (`elliott.py`)

Pivots come from the ZigZag with an adaptive threshold
(`adaptive_threshold`: ATR-driven, floored at 2%, capped at 15%) and a minimum
separation of 2 bars.

Every 6-pivot window is scored as a 5-wave impulse. **Hard rules** (a violation
invalidates the count, and the reason is reported in Persian):

| Rule | Up impulse |
| --- | --- |
| `wave2_no_full_retrace` | pivot 2 price > pivot 0 price |
| `wave3_not_shortest` | wave 3 length ≥ min(wave 1, wave 5) |
| `wave4_no_overlap` | pivot 4 price > pivot 1 price (top of wave 1) |
| `wave3_beyond_wave1` | pivot 3 price > pivot 1 price |

**Guidelines** (soft score, max 100): wave-3 length near 1.618× wave 1 (12 pts),
wave 5 equal to wave 1 or 0.618× the impulse (10 pts), alternation between waves
2 and 4 (8 pts), wave-2 retrace inside 23.6–78.6% (6 pts), wave-4 retrace inside
15–61.8% (6 pts), −10 pts for a truncated fifth. Valid counts start from 40 pts.

Outputs: the labelled impulse, the A-B-C correction that follows it
(`score_correction`), a nested minor-degree count inside wave 3 when one
exists, `current` (where price is in the count, `next_label`, invalidation,
targets), `patterns`, and chart overlay coordinates.

**Targets.** Wave 3: 1.0 / 1.272 / 1.618 / 2.0 / 2.618 × wave 1 measured from
the end of wave 1. Wave 5: 0.618 / 1.0 × the impulse length, measured from the
end of wave 5 or of the correction.

**Signals.** The count produces a directional bias, and the stop is always
derived from the *recent* extreme buffered by ATR, then clamped to
`price ∓ 0.5 × risk` - never from the impulse invalidation level, which produced
−1896 R stops in an early revision. `allow_short=False` drops counter-trend
signals (TSE equities cannot be shorted); they still reach the user as a risk
line.

## RTM - Read The Market (`rtm.py`)

Zones are built from fractal swings (`strength=2`). A zone is the base of 1–4
candles (`max_base=4`, tightness ≤ 1.1×ATR) followed by a departure of at least
`departure_min_atr=1.2` ATR.

- **Type**: rally-base-rally (RBR), drop-base-rally (DBR) = demand;
  drop-base-drop (DBD), rally-base-drop (RBD) = supply.
- **Levels**: `low/high`, `eq` (equilibrium), `golden` (the 50–79% quartile used
  for entry), `quarts` Q1–Q4, `mpl` (maximum pain level = the zone's low for
  demand, high for supply), `ftr` (failure to return), `depth`, `strength`
  (departure size, compression and volume), `touches`, `target_at_creation`.
- **Mitigation** is judged by a *close* through the MPL: a demand zone dies on a
  close below its low, a supply zone on a close above its high. A re-entry only
  counts as a touch. (Judging mitigation by the departure leg is backwards and
  consumed 33 of 33 demand zones on the first sample symbol.)
- **Guards**: a zone deeper than `MAX_ZONE_DEPTH_ATR = 6` ATR is a range, not a
  zone, and is rejected; an entry further than `MAX_ENTRY_DISTANCE_ATR = 3` ATR
  from the current price is rejected; `min_rr = 1.2`; `max_per_kind = 2` per bar
  so overlapping zones cannot flood one bar with 26 signals.
- **Historical scan** (`historical_zone_signals`) is causal by construction: at
  bar `i` only zones created before `i` and not yet broken at `i` are
  considered, with `cooldown_bars = 12`, `min_strength = 0.35`.

## ICT (`ict.py`)

- **Fair value gap**: three-candle imbalance (`third.low > first.high` for
  bullish). Marked `filled` once price trades back through it, `live` when
  unfilled and younger than `max_age = 40` bars.
- **Order block**: anchored to a BOS/CHoCH event; the move that caused the break
  must measure at least 1.5 ATR (displacement), and the block is the last
  opposite-colour candle inside it. Reported with `live` (not yet mitigated).
- **Liquidity sweep**: a swing high/low taken out intrabar with the close back
  inside the range (stop hunt). A buy-side sweep carries `direction = -1`.
- **Dealing range**: the last two swing highs/lows, with `equilibrium`,
  `position`, `premium`/`discount` and the OTE band at 62–79% of the range.
- **Structure**: BOS (continuation) and CHoCH (change of character) events from
  `patterns.market_structure`.
- `min_rr = 1.5`, `strength = 2`.

## ACT - Accumulation / Compression / Trend (`act.py`)

Phase detection per bar, long and short, using only causal series:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `range_bars` | 25 | bars the range is measured over |
| `range_max_atr` | 6.0 | range depth must be under 6 ATR |
| `adx_max` | 22.0 | ADX below this means "no trend yet" |
| `bandwidth_percentile` | 30.0 | Bollinger bandwidth percentile defining the squeeze |
| `breakout_min_rel_volume` | 1.4 | volume must confirm the break |
| `lookback` | 120 | bars used for the prior trend |
| `min_rr` | 1.5 | minimum reward/risk to emit a signal |

Phase logic:

- `accumulation` - a live range after a prior move, ADX ≤ `adx_max` and a
  non-rising volume slope (quiet, absorbed supply/demand).
- `compression` - TTM squeeze on, or Bollinger bandwidth inside the lowest
  `bandwidth_percentile` of the lookback.
- `trend` - a close through the range edge **while** accumulation or
  compression holds.
- `none` - otherwise.

A plan is emitted for any phase other than `none`, then scored on 7 booleans
(accumulation, absorption via OBV, compression, breakout, relative volume ≥
`breakout_min_rel_volume`, expanding ATR, rising ADX); `confidence =
0.3 + 0.09 × score`, capped at 0.9. Compression phases enter with a **limit**
order at the range edge, breakouts with a market order. Entry never chases more
than 1 ATR from the price, the stop sits beyond the opposite range edge buffered
by 0.25 ATR, and the target is the measured move (range depth projected from the
break), widened to `min_rr` when the measured move is too small.

**Risk floor.** `risk = max(entry − stop, 0.5 × ATR, 0.25% of price)`. A stop
tighter than the noise floor once turned a single tick into −336 R and dragged
the whole backtest to −6.39 R expectancy.

## Setups (`setups.py`)

13 detectors (10 long, 3 short: `supertrend_flip_short`,
`donchian_breakdown_short`, `ma_crossover_short`), each with a Persian name,
family, entry kind and a one-line logic description in `SETUP_LIBRARY`. Engine
setups are named through `EXTRA_SETUP_NAMES`: `rtm_rbr/dbr/dbd/rbd`,
`act_breakout`, `act_breakdown`, `elliott_wave`.

Grading uses the measured statistics of the analysed series:

| Grade | Condition (trades ≥ 20) |
| --- | --- |
| A | win rate ≥ 70% and expectancy ≥ 0.4 R |
| B | win rate ≥ 60% and expectancy ≥ 0.2 R |
| C | win rate ≥ 50% and expectancy ≥ 0 R |
| D | anything else |
| `نمونه کم` | fewer than 20 trades - never rank on it |

## Backtest (`backtest.py`)

`BacktestConfig`: `max_bars=60`, `entry_window=10`, `allow_short=False`,
`fees_bps=6` (TSE commission each side), `slippage_bps=5`, `overlap=False`,
`partial_at_1r=False`.

Pessimistic by construction:

- fills happen on bar `i+1` at the **open** for market entries;
- limit entries fill only if a later bar within `entry_window` trades through
  the price, otherwise the trade is `expired` and excluded;
- if a bar hits both stop and target, the **stop** is assumed;
- a stop tighter than `0.25%` of the entry is skipped (`simulate_signal`
  returns `None`) instead of producing a −300 R artefact;
- positions never overlap inside one setup's run unless `overlap=True`;
- costs are charged in R (`2 × (fees + slippage) / risk`).

Reported per setup and overall: trades, expired, win rate, average win/loss,
expectancy, profit factor, max drawdown in R, average bars held, exit-reason and
direction histograms, equity curve, and `sample_ok = trades ≥ 20`.

## Recommendation (`signals.py`)

Five weighted factors, each scored −100…+100 then weighted:

| Factor | Weight |
| --- | --- |
| trend | 0.30 |
| momentum | 0.22 |
| structure | 0.22 |
| volume | 0.14 |
| volatility | 0.12 |

`BUY_THRESHOLD = +35`, `SELL_THRESHOLD = −35`. Adjustments: RSI ≥ 75 → −0.4,
RSI ≤ 25 → +0.4. A buy/sell is only issued when a matching signal exists; for
long-only markets a bearish score becomes a risk warning instead.

The chosen signal is the one closest to the current price (`_reachable`
penalises entries more than 1.5 ATR away), so the card never advertises a limit
order 200% of an ATR from the market as if it were actionable. `target2` is
always `entry ± 2 × risk`. The payload carries the reasons, the supporting
signals, the top factor scores, the risks (CHoCH, active squeeze, far pending
entry, small samples, suppressed shorts) and the disclaimer.
