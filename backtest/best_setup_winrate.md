# Win rate — best reversal setup (swing 3, 4h EMA50, divergence, volume, MACD)

Per 1 nano contract (0.01 BTC): $0.01 per $1 BTC move, $0.54 fees+slippage per round trip. TRAIN = 2017 to 2024-01-01 (used to choose the setup); TEST = 2024-01-01 to Oct 2026 (unseen). 51 top signals, 54 bottom signals in total.

Break-even win rate: 33% at 2:1, 25% at 3:1 (before costs).

| entry | R:R | side | TRAIN trades | win | net | TEST trades | win | net | test maxDD |
|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 2:1 | short | 28 | 46% | $165 | 12 | 25% | -$45 | $137 |
| at 1h signal | 2:1 | long | 37 | 49% | $59 | 15 | 33% | -$48 | $169 |
| at 1h signal | 2:1 | both | 65 | 48% | $224 | 27 | 30% | -$92 | $179 |
| at 1h signal | 3:1 | short | 22 | 36% | $107 | 12 | 17% | -$95 | $137 |
| at 1h signal | 3:1 | long | 35 | 37% | $150 | 15 | 27% | -$70 | $200 |
| at 1h signal | 3:1 | both | 57 | 37% | $257 | 27 | 22% | -$165 | $257 |
| 15m pullback | 2:1 | short | 19 | 47% | -$15 | 4 | 25% | $2 | $16 |
| 15m pullback | 2:1 | long | 12 | 25% | -$8 | 7 | 43% | $30 | $23 |
| 15m pullback | 2:1 | both | 31 | 39% | -$23 | 11 | 36% | $32 | $39 |
| 15m pullback | 3:1 | short | 19 | 42% | -$8 | 4 | 25% | $13 | $16 |
| 15m pullback | 3:1 | long | 12 | 25% | $1 | 7 | 43% | $59 | $23 |
| 15m pullback | 3:1 | both | 31 | 35% | -$7 | 11 | 36% | $72 | $39 |

Trade log (New York time): backtest/best_setup_trades.csv. Same-bar stop and target touches count as a stop.