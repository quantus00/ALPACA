# BTC stack + perp reversal shorts (start $1,000 of BTC, held forever)

Shorts on top-reversal signals (51 in total), 1 nano perp contract (0.01 BTC, $0.54 fees+slippage per contract round trip), stop above the 1h lower high, 3:1 target. Perp profit buys spot BTC each time it reaches the threshold (spot fee 0.6%). Funding payments not included.

## 3:1, add 1 contract at 50% to stop — start Mar 2017 (full history)

| buy BTC every | trades | win rate | adds (won) | perp net | perp max drawdown | cash needed | BTC buys | BTC: start → end | end value | hold-only value |
|---|---|---|---|---|---|---|---|---|---|---|
| $1,000 profit | 39 | 33% | 36 (10) | $309 | $207 | $13 | 0 | 0.80879 → 0.80879 | $68,121 | $67,811 |
| $100 profit | 39 | 33% | 36 (10) | $309 | $207 | $196 | 3 | 0.80879 → 0.83310 | $69,859 | $67,811 |

## 3:1, add 1 contract at 50% to stop — start Jan 2024 (unseen years only)

| buy BTC every | trades | win rate | adds (won) | perp net | perp max drawdown | cash needed | BTC buys | BTC: start → end | end value | hold-only value |
|---|---|---|---|---|---|---|---|---|---|---|
| $1,000 profit | 12 | 25% | 11 (2) | -$2 | $207 | $207 | 0 | 0.02340 → 0.02340 | $1,960 | $1,962 |
| $100 profit | 12 | 25% | 11 (2) | -$2 | $207 | $207 | 0 | 0.02340 → 0.02340 | $1,960 | $1,962 |

## 3:1, no add — start Mar 2017 (full history)

| buy BTC every | trades | win rate | adds (won) | perp net | perp max drawdown | cash needed | BTC buys | BTC: start → end | end value | hold-only value |
|---|---|---|---|---|---|---|---|---|---|---|
| $1,000 profit | 34 | 29% | 0 (0) | $12 | $137 | $36 | 0 | 0.80879 → 0.80879 | $67,824 | $67,811 |
| $100 profit | 34 | 29% | 0 (0) | $12 | $137 | $130 | 1 | 0.80879 → 0.81260 | $68,043 | $67,811 |

## 3:1, no add — start Jan 2024 (unseen years only)

| buy BTC every | trades | win rate | adds (won) | perp net | perp max drawdown | cash needed | BTC buys | BTC: start → end | end value | hold-only value |
|---|---|---|---|---|---|---|---|---|---|---|
| $1,000 profit | 12 | 17% | 0 (0) | -$95 | $137 | $137 | 0 | 0.02340 → 0.02340 | $1,868 | $1,962 |
| $100 profit | 12 | 17% | 0 (0) | -$95 | $137 | $137 | 0 | 0.02340 → 0.02340 | $1,868 | $1,962 |

Trade log (New York time): backtest/stack_trades.csv. 'Cash needed' = the most the perp account was down in total, i.e. money you would have to keep ready for margin and losses.