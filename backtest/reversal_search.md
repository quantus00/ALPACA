# Reversal-rate search (BTC) — target 70% on tops AND bottoms

Tuned on TRAIN = 2017 to 2024-01-01; TEST = 2024-01-01 to Oct 2026 was never used to choose. Rate = share of signals graded VALID (moved the signal's way by at least max(how late it was, 2 x 1h ATR) before closing back beyond the swing top/bottom). Cells: rate of N signals.

Stage A tested 358 combinations at the 1h signal; stage B tested 36 15m-pullback versions of the 12 best.

### Starting point (current detector, 1h signal)

| setup | TRAIN tops | TRAIN bottoms | TEST tops | TEST bottoms | test big turns caught (tops / bottoms) |
|---|---|---|---|---|---|
| swing 5, no trend filter | 38% of 235 | 44% of 245 | 45% of 85 | 43% of 79 | 20/31 / 21/31 |

### Stage A — best 12 by the WORSE of train tops/bottoms (min 25 train signals each)

| setup | TRAIN tops | TRAIN bottoms | TEST tops | TEST bottoms | test big turns caught (tops / bottoms) |
|---|---|---|---|---|---|
| swing 3, daily EMA50, divergence, volume, MACD | 68% of 25 | 66% of 32 | 56% of 9 | 55% of 11 | 5/31 / 6/31 |
| swing 3, 4h EMA50, divergence, volume, MACD | 69% of 39 | 64% of 39 | 58% of 12 | 60% of 15 | 7/31 / 10/31 |
| swing 3, 4h EMA50, divergence, volume | 69% of 39 | 62% of 40 | 50% of 14 | 50% of 16 | 8/31 / 10/31 |
| swing 3, daily EMA50, divergence, volume | 65% of 26 | 62% of 34 | 56% of 9 | 54% of 13 | 5/31 / 7/31 |
| swing 3, 4h EMA50, divergence | 63% of 65 | 61% of 75 | 44% of 34 | 46% of 37 | 15/31 / 15/31 |
| swing 3, 4h EMA50, divergence, MACD | 62% of 65 | 61% of 74 | 45% of 31 | 50% of 36 | 14/31 / 15/31 |
| swing 3, daily EMA50, divergence, MACD | 60% of 48 | 64% of 59 | 41% of 22 | 62% of 29 | 8/31 / 10/31 |
| swing 3, daily EMA50, divergence | 61% of 49 | 59% of 68 | 40% of 25 | 59% of 32 | 8/31 / 11/31 |
| swing 5, 4h EMA50, divergence, volume | 59% of 34 | 71% of 38 | 38% of 13 | 50% of 12 | 6/31 / 7/31 |
| swing 5, daily EMA50, volume | 58% of 67 | 64% of 66 | 35% of 17 | 64% of 14 | 7/31 / 5/31 |
| swing 5, 4h EMA50, divergence, volume, MACD | 58% of 33 | 71% of 38 | 45% of 11 | 50% of 12 | 6/31 / 7/31 |
| swing 5, daily EMA50, volume, MACD | 57% of 60 | 62% of 63 | 38% of 16 | 64% of 14 | 7/31 / 5/31 |

### Stage B — best 15m pullback versions (min 15 train entries each side)

| setup | TRAIN tops | TRAIN bottoms | TEST tops | TEST bottoms | test big turns caught (tops / bottoms) |
|---|---|---|---|---|---|
| swing 3, 4h EMA50, divergence, 15m pullback 0.382 | 59% of 32 | 68% of 28 | 42% of 12 | 53% of 15 | 4/31 / 10/31 |
| swing 3, 4h EMA50, divergence, MACD, 15m pullback 0.382 | 58% of 31 | 69% of 29 | 40% of 10 | 53% of 15 | 4/31 / 10/31 |
| swing 5, daily EMA50, volume, 15m pullback 0.382 | 58% of 26 | 62% of 24 | 43% of 7 | 50% of 4 | 2/31 / 2/31 |
| swing 3, daily EMA50, divergence, 15m pullback 0.382 | 50% of 18 | 59% of 27 | 38% of 8 | 57% of 14 | 2/31 / 7/31 |
| swing 5, daily EMA50, volume, MACD, 15m pullback 0.382 | 50% of 20 | 58% of 24 | 43% of 7 | 50% of 4 | 2/31 / 2/31 |
| swing 3, daily EMA50, divergence, MACD, 15m pullback 0.382 | 47% of 17 | 64% of 28 | 33% of 6 | 62% of 13 | 2/31 / 7/31 |

Setups reaching 70% on BOTH sides in TRAIN: 0.