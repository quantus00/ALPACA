# STEP 1 — BTC reversal detection accuracy: 1h break vs 15m pullback entry ($ per 1 BTC)

Valid = price then moved the entry's way by at least max(how late it was, 2 x 1h ATR) before closing back beyond the swing top/bottom. Big turns = swings reversing >= 10% on 1h bars (hindsight).

Columns per side: entries | valid | avg $ late vs real top/bottom | avg $ move after | big turns caught | avg $ late on big turns

## 1h setup: structure only — full (from Mar 2017)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 316 | 39% | $1,444 | $1,966 | 104/257 | $2,087 | 319 | 43% | $1,623 | $1,940 | 122/257 | $2,568 |
| 15m pullback | 157 | 50% | $1,252 | $2,359 | 54/257 | $2,020 | 104 | 50% | $1,463 | $2,280 | 48/257 | $4,361 |

## 1h setup: structure only — 3y (from Oct 2023)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 95 | 42% | $2,305 | $3,297 | 20/32 | $4,323 | 90 | 43% | $2,684 | $3,735 | 22/32 | $6,575 |
| 15m pullback | 46 | 57% | $2,049 | $4,633 | 12/32 | $4,593 | 34 | 47% | $2,357 | $3,736 | 11/32 | $11,510 |

## 1h setup: structure only — 12m (from Oct 2025)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 33 | 52% | $2,204 | $4,425 | 7/8 | $2,905 | 30 | 43% | $2,316 | $2,631 | 7/9 | $5,722 |
| 15m pullback | 12 | 83% | $1,999 | $6,997 | 4/8 | $3,877 | 10 | 40% | $1,839 | $2,560 | 4/9 | $7,411 |

## 1h setup: structure + daily filter — full (from Mar 2017)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 142 | 49% | $1,342 | $3,534 | 56/257 | $3,171 | 169 | 64% | $1,657 | $7,073 | 74/257 | $3,136 |
| 15m pullback | 57 | 60% | $1,018 | $2,745 | 29/257 | $2,411 | 58 | 64% | $1,650 | $7,563 | 28/257 | $6,045 |

## 1h setup: structure + daily filter — 3y (from Oct 2023)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 32 | 44% | $2,216 | $5,913 | 11/32 | $7,481 | 54 | 57% | $2,609 | $8,224 | 13/32 | $8,829 |
| 15m pullback | 13 | 54% | $1,896 | $4,971 | 4/32 | $7,902 | 20 | 60% | $2,545 | $7,035 | 6/32 | $18,700 |

## 1h setup: structure + daily filter — 12m (from Oct 2025)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 21 | 52% | $2,165 | $7,873 | 7/8 | $6,434 | 11 | 45% | $1,984 | $5,254 | 2/9 | $6,740 |
| 15m pullback | 8 | 75% | $2,093 | $7,613 | 3/8 | $7,829 | 3 | 33% | $1,639 | $2,861 | 2/9 | $15,594 |
