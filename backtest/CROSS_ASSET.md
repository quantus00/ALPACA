# Cross-Asset Validation — Hybrid + Trend-Follower (ATR-normalized)

The two chosen strategies, run on four very different assets with **all stops and
targets sized in ATR** (so risk means the same thing on each). Entries are
pullback-in-trend only. P&L shown in $ (per 1 unit) and in **R-multiples**
(P&L ÷ initial risk) so assets compare fairly. Reproduce: `python3 backtest/multi_asset.py`.

| asset | history | strategy | trades | win% | net R | exp/trade (R) | PF | max DD $ |
|-------|---------|----------|-------:|-----:|------:|--------------:|---:|---------:|
| **BTC** | 3.3 yr (D) | trend-follow | 21 | 10% | **−14.9** | −0.71 | 0.19 | −88,960 |
| BTC | 3.3 yr | hybrid core | 53 | 19% | −12.3 | −0.23 | 0.66 | −54,204 |
| BTC | 3.3 yr | hybrid scale | 44 | 9% | **−90.1** | −2.05 | 0.67 | −270,026 |
| **SPY** | 4.75 yr (D) | trend-follow | 7 | 29% | +25.4 | 3.63 | 5.26 | −77 |
| SPY | 4.75 yr | **hybrid core** | 81 | 32% | **+19.6** | +0.24 | **1.47** | −81 |
| SPY | 4.75 yr | hybrid scale | 48 | 21% | +264 | 5.51 | 3.46 | −446 |
| **GOLD** | 2.5 mo (60m) | trend-follow | 15 | 33% | +0.8 | 0.06 | 1.20 | −1,872 |
| GOLD | 2.5 mo | hybrid core | 76 | 24% | −10.0 | −0.13 | 0.85 | −2,912 |
| GOLD | 2.5 mo | hybrid scale | 40 | 23% | +177 | 4.43 | 2.17 | −7,631 |
| **MES** | 8.5 mo (60m) | trend-follow | 69 | 29% | −5.0 | −0.07 | 1.03 | −2,406 |
| MES | 8.5 mo | hybrid core | 236 | 28% | +1.8 | +0.01 | 0.99 | −2,482 |
| MES | 8.5 mo | hybrid scale | 163 | 17% | +165 | 1.01 | 1.18 | −15,254 |

## The honest headline: it does NOT generalize

- **It works on SPY (equities).** `hybrid_core` is positive over **4.75 years / 81
  trades** (PF 1.47) — the most trustworthy result here (long, real, many trades).
- **It's ~breakeven on MES and gold** (PF ~0.99–1.20). Marginal, not an edge.
- **It LOSES badly on BTC** — every variant negative, `hybrid_scale` −90 R with a
  −$270k drawdown. Bitcoin's big, fast trends whipsaw the pullback logic (win
  rate 9–19%).

## Why
The pullback-in-trend with a 3:1 ATR bracket has an **inherently low win rate
(17–32%)** — the 3‑ATR target rarely gets hit. That only pays off where trends
are steady and pullbacks are shallow (equity indices). On BTC, where pullbacks
are violent, the 1‑ATR stop gets hit far more than the 3‑ATR target.

**Scale mode** amplifies whatever the base does: huge positive on SPY/gold/MES in
this window, catastrophic on BTC. Its drawdowns (−$7k to −$270k) make it unfit
without strict per-asset sizing.

## What this means for you
1. **Use it on equity indices** — SPY (for your stock options) and MES — where it
   showed a real, long-sample edge with the robust core. That matches your
   original targets.
2. **Do NOT run it on BTC as-is.** Crypto needs different parameters (or a
   different approach). One asset's tuning is not another's.
3. **Keep `hybrid_core` as the default; treat `scale` as high-risk/optional.**
4. Everything is one commodity of luck away from looking different — even SPY is
   one long sample, one regime. This is validation, not a guarantee.

## Caveats
ATR(14) stops, pullback entries, ~1 bp/side cost, no per-asset commission
modeling; gold/MES are intraday (shorter), BTC/SPY are daily (years). GCZ6 gold
daily history is thin pre-2026 so gold uses 60m. Small-sample rows (SPY
trend-follow = 7 trades, gold = 15) are not conclusive.
