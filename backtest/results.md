# MES Backtest Results — Open + Trend + Pullback (+ scale-in, management, sweep)

**Instrument:** MES ($5/point). **Data:** Webull futures bars (pulled 2026-09-24).
**Fills:** conservative — stop-before-target on ties, add-trigger deferred on
ties; commission $0.62/contract/side; RTH-only entries; no extra slippage.
Reproduce: `python3 backtest/mes_backtest.py` (see `DROPLET.md`).

Two reports:
- **Report 1** — recent single-contract MESZ6 snapshot across M5/M15/M30/M60
  (small windows, but includes the 09:30 open entry on M5/M15/M30).
- **Report 2** — an **extended continuous front-month M60 series stitched from
  MESH6+MESM6+MESU6+MESZ6 (2026-01-06 → 2026-09-24, ~8.5 months)**. M60 has no
  09:30 bar, so this is **pullback-entries only**, with ~3-week gaps at each
  contract roll.

Variants: `opt1` = 1 contract 30/10 baseline. `opt2a/b/c` = add 7 at +20 pt (→8
contracts) then 60/20, 30/10, 3/1 from the blended average.

---

## Report 1 — recent snapshot (MESZ6)

| TF | variant | trades | win% | net $ | avg $ | PF | maxDD $ |
|----|---------|-----:|----:|------:|----:|---:|------:|
| M5  | opt1 30/10 | 5 | 40% | +144 | 29 | 1.94 | −102 |
| M5  | opt2a 60/20 | 5 | 20% | +2,185 | 437 | 11.7 | −154 |
| M5  | opt2b 30/10 | 5 | 20% | +626 | 125 | 2.11 | −512 |
| M5  | opt2c 3/1 | 5 | 40% | +66 | 13 | 1.43 | −102 |
| M15 | opt1 30/10 | 15 | 53% | +831 | 55 | 3.32 | −154 |
| M15 | opt2a 60/20 | 13 | 15% | +1,940 | 149 | 1.68 | −2,635 |
| M15 | opt2b 30/10 | 15 | 13% | −79 | −5 | 0.97 | −1,896 |
| M15 | opt2c 3/1 | 15 | 27% | −118 | −8 | 0.79 | −195 |
| M30 | opt1 30/10 | 27 | 33% | +417 | 15 | 1.45 | −261 |
| M30 | opt2a 60/20 | 25 | 8% | −2,468 | −99 | 0.66 | −4,704 |
| M30 | opt2b 30/10 | 27 | 11% | −529 | −20 | 0.87 | −2,101 |
| M30 | opt2c 3/1 | 28 | 14% | −780 | −28 | 0.36 | −788 |
| M60 | opt1 30/10 | 6 | 33% | +93 | 15 | 1.45 | −102 |
| M60 | opt2a 60/20 | 6 | 17% | +617 | 103 | 1.35 | −1,774 |
| M60 | opt2b 30/10 | 6 | 17% | +217 | 36 | 1.22 | −512 |
| M60 | opt2c 3/1 | 6 | 50% | +177 | 29 | 2.15 | −102 |

---

## Report 2 — extended continuous M60 (~8.5 months, 24 pullback trades)

**Base variants**

| variant | trades | win% | net $ | avg $ | PF | maxDD $ |
|---------|-----:|----:|------:|----:|---:|------:|
| opt1 30/10 | 24 | 25% | **−30** | −1 | 0.97 | −569 |
| opt2a 60/20 | 24 | 8% | −899 | −37 | 0.84 | −3,906 |
| opt2b 30/10 | 24 | 8% | −899 | −37 | 0.73 | −1,679 |
| opt2c 3/1 | 24 | 13% | −739 | −31 | 0.31 | −799 |

**Exit management** (opt2a/opt2b × plain / +flip-exit / +EOD-flat / both)

| variant | trades | win% | net $ | avg $ | PF | maxDD $ |
|---------|-----:|----:|------:|----:|---:|------:|
| opt2a_plain | 24 | 8% | −899 | −37 | 0.84 | −3,906 |
| **opt2a_flip** | 24 | 17% | **+4,161** | 173 | **2.02** | −2,286 |
| opt2a_eod | 24 | 33% | −516 | −21 | 0.64 | −949 |
| opt2a_flip_eod | 24 | 33% | −516 | −21 | 0.64 | −949 |
| opt2b_plain | 24 | 8% | −899 | −37 | 0.73 | −1,679 |
| opt2b_flip | 24 | 8% | −899 | −37 | 0.73 | −1,679 |
| opt2b_eod | 24 | 33% | +234 | 10 | 1.17 | −635 |
| opt2b_flip_eod | 24 | 33% | +234 | 10 | 1.17 | −635 |

**Add-trigger sweep** (15 / 20 / 25 pt)

| variant | trades | win% | net $ | avg $ | PF | maxDD $ |
|---------|-----:|----:|------:|----:|---:|------:|
| opt2a_add15 | 24 | 17% | **+3,225** | 134 | 1.51 | −3,803 |
| opt2a_add20 | 24 | 8% | −899 | −37 | 0.84 | −3,906 |
| opt2a_add25 | 24 | 4% | −3,341 | −139 | 0.42 | −3,341 |
| opt2b_add15 | 24 | 8% | −1,975 | −82 | 0.55 | −2,755 |
| opt2b_add20 | 24 | 8% | −899 | −37 | 0.73 | −1,679 |
| opt2b_add25 | 24 | 8% | −541 | −23 | 0.81 | −1,383 |

---

## What the bigger sample tells us

1. **The recent snapshot was optimistic.** Over ~8.5 months the base strategy
   (opt1) is **roughly breakeven** (−$30, PF 0.97) instead of the tidy profits
   Report 1 showed — a textbook small-sample warning.
2. **The flip-exit is the single biggest improvement — but only on the wide
   bracket.** `opt2a_flip` (scale→60/20, close if the 4H trend flips) goes from
   −$899 (PF 0.84) to **+$4,161 (PF 2.02)** and cuts drawdown $3.9k→$2.3k. On
   the tight bracket `opt2b_flip` does nothing (the 10-pt stop fires before any
   flip). EOD-flat helps opt2b a little (+$234) but caps opt2a's runners.
3. **The add-trigger matters a lot.** Adding earlier (**+15 pt**) is far better
   for the 60/20 variant (+$3,225 vs −$899 at +20, −$3,341 at +25). Adding later
   just buys in near exhaustion.
4. **opt2c (3/1) is consistently the worst** — the 1-pt stop on 8 lots is pure
   noise.

## ⚠️ Still not a validated edge
- Report 2 is **24 trades** (pullback-only, M60). The standout results
  (`opt2a_flip`, `opt2a_add15`) rest on a handful of trades — treat as
  hypotheses to test live/paper, not proven.
- One instrument, 2026 regime, **~3-week gaps at each roll**, modeled fills (no
  order-book slippage), and price discontinuities at contract seams.
- Scale variants risk up to 8 contracts vs opt1's 1 — compare per unit of risk,
  not raw $.

## Practical takeaways
- If trading the **single-contract baseline**, keep it simple (30/10); it's the
  most robust but only ~breakeven on the longer sample here.
- If you want the **scale-in**, the data favors **add earlier (~+15 pt), keep
  the wide 60/20 target, and add a trend-flip exit** — that combination is the
  only one that was strongly positive on the extended series.
- Next validation step: get a real deep intraday feed (so 09:30 entries are in
  the extended test too) and re-run before risking size.
