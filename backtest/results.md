# MES Backtest Results — Open + Trend + Pullback with scale-in variants

**Instrument:** MESZ6 (Micro E-mini S&P 500, $5/point).
**Data:** Webull futures bars, pulled 2026-09-24. 4H trend derived from the M60
series (2026-07-13 → 2026-09-24) for all timeframes.
**Fills:** conservative — stop-before-target on ties, add-trigger deferred on
ties; commission $0.62/contract/side; no extra slippage. Entries RTH only.

Reproduce: `python3 backtest/mes_backtest.py`

## Variants
- **opt1_30_10** — baseline: 1 contract, +30 / −10 pt bracket. No scaling.
- **opt2a_60_20** — at +$100 (+20 pt) add 7 (→8), then +60 / −20 from the blend.
- **opt2b_30_10** — same scale-in, then +30 / −10 from the blend.
- **opt2c_3_1** — same scale-in, then +3 / −1 from the blend.

## Results

| TF | variant | trades | scaled | win% | net $ | avg $/trade | PF | max DD $ |
|----|---------|-------:|-------:|-----:|------:|------------:|---:|---------:|
| M5  | opt1_30_10  | 5  | 0  | 40.0% | **+143.80**  | 28.76  | 1.94 | −102 |
| M5  | opt2a_60_20 | 5  | 1  | 20.0% | **+2185.12** | 437.02 | 11.66| −154 |
| M5  | opt2b_30_10 | 5  | 2  | 20.0% | +626.44      | 125.29 | 2.11 | −512 |
| M5  | opt2c_3_1   | 5  | 2  | 40.0% | +66.44       | 13.29  | 1.43 | −102 |
| M15 | opt1_30_10  | 15 | 0  | 53.3% | **+831.40**  | 55.43  | 3.32 | −154 |
| M15 | opt2a_60_20 | 13 | 5  | 15.4% | **+1940.48** | 149.27 | 1.68 | −2635 |
| M15 | opt2b_30_10 | 15 | 7  | 13.3% | −79.36       | −5.29  | 0.97 | −1896 |
| M15 | opt2c_3_1   | 15 | 8  | 26.7% | −118.04      | −7.87  | 0.79 | −195 |
| M30 | opt1_30_10  | 27 | 0  | 33.3% | **+416.52**  | 15.43  | 1.45 | −261 |
| M30 | opt2a_60_20 | 25 | 10 | 8.0%  | **−2467.80** | −98.71 | 0.66 | −4704 |
| M30 | opt2b_30_10 | 27 | 11 | 11.1% | −528.96      | −19.59 | 0.87 | −2101 |
| M30 | opt2c_3_1   | 28 | 11 | 14.3% | −780.20      | −27.86 | 0.36 | −788 |
| M60 | opt1_30_10  | 6  | 0  | 33.3% | +92.56       | 15.43  | 1.45 | −102 |
| M60 | opt2a_60_20 | 6  | 3  | 16.7% | +616.52      | 102.75 | 1.35 | −1774 |
| M60 | opt2b_30_10 | 6  | 3  | 16.7% | +216.52      | 36.09  | 1.22 | −512 |
| M60 | opt2c_3_1   | 6  | 3  | 50.0% | +176.52      | 29.42  | 2.15 | −102 |

## What the numbers say

1. **The baseline (opt1, single 1 contract 30/10) is the only variant that is
   consistently positive on every timeframe** (+$93 to +$831) with small
   drawdowns (~$100–260) and profit factors 1.45–3.32. It's the steady one.

2. **Scaling to 8 contracts multiplies variance, not edge.** opt2a can be
   spectacular when a 60-pt runner hits on 8 contracts (+$2,185 on M5, +$1,940
   on M15) but is brutal when they don't (−$2,468 on M30) with drawdowns of
   **$1,800–$4,700**. Win rates collapse to 8–20% because the wide 60-pt target
   is rarely reached and the 20-pt stop on 8 lots (−$800) hits often.

3. **opt2b (scale→30/10) and opt2c (scale→3/1) are mostly flat-to-negative.**
   Once you're holding 8 contracts, a 10-pt (−$400) or even 1-pt stop gets
   taken out by noise before the target; the scale-in turns good 1-lot entries
   into larger losers.

4. **Reward:risk is not realized in practice.** A 3:1 bracket only pays if the
   target is actually hit; on 8 contracts the wider stops mean a single loss
   erases several wins.

## ⚠️ Read this before trusting any of it
- **Sample sizes are tiny** (6–28 trades). M60 has just 6 trades; M5 just 5.
  None of this is statistically significant — it's a mechanics check on real
  recent data, not a validated edge.
- **Only ~1 week to ~10 weeks of history** was available from the feed (no deep
  history), all within one contract (MESZ6) in one market regime.
- **Fills are modeled, not real** — conservative bar-range assumptions, no
  order-book slippage. Live results on 8-lot MES would differ.
- Absolute $ across variants isn't apples-to-apples: opt1 risks 1 contract,
  opt2* risk up to 8. Per unit of risk, opt1 is far more efficient.

**Bottom line:** on this data, the single-contract 30/10 (opt1) is the robust
choice; the +20 pt scale-in to 8 contracts mainly amplifies swings and, except
for the occasional big 60-pt runner (opt2a), hurt more than it helped.
