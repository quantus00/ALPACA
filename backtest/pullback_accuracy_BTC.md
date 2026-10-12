# STEP 1 — BTC reversal detection accuracy: 1h break vs 15m pullback entry ($ per 1 BTC)

Valid = price then moved the entry's way by at least max(how late it was, 2 x 1h ATR) before closing back beyond the swing top/bottom. Big turns = swings reversing >= 10% on 1h bars (hindsight).

Columns per side: entries | valid | avg $ late vs real top/bottom | avg $ move after | big turns caught | avg $ late on big turns

## 1h setup: structure only — full (from Mar 2017)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 316 | 39% | $14 | $20 | 104/257 | $21 | 319 | 43% | $16 | $19 | 122/257 | $26 |
| 15m pullback | 157 | 50% | $13 | $24 | 54/257 | $20 | 104 | 50% | $15 | $23 | 48/257 | $44 |

## 1h setup: structure only — 3y (from Oct 2023)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 95 | 42% | $23 | $33 | 20/32 | $43 | 90 | 43% | $27 | $37 | 22/32 | $66 |
| 15m pullback | 46 | 57% | $20 | $46 | 12/32 | $46 | 34 | 47% | $24 | $37 | 11/32 | $115 |

## 1h setup: structure only — 12m (from Oct 2025)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 33 | 52% | $22 | $44 | 7/8 | $29 | 30 | 43% | $23 | $26 | 7/9 | $57 |
| 15m pullback | 12 | 83% | $20 | $70 | 4/8 | $39 | 10 | 40% | $18 | $26 | 4/9 | $74 |

## 1h setup: structure + daily filter — full (from Mar 2017)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 142 | 49% | $13 | $35 | 56/257 | $32 | 169 | 64% | $17 | $71 | 74/257 | $31 |
| 15m pullback | 57 | 60% | $10 | $27 | 29/257 | $24 | 58 | 64% | $17 | $76 | 28/257 | $60 |

## 1h setup: structure + daily filter — 3y (from Oct 2023)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 32 | 44% | $22 | $59 | 11/32 | $75 | 54 | 57% | $26 | $82 | 13/32 | $88 |
| 15m pullback | 13 | 54% | $19 | $50 | 4/32 | $79 | 20 | 60% | $25 | $70 | 6/32 | $187 |

## 1h setup: structure + daily filter — 12m (from Oct 2025)

| entry | TOPS: entries | valid | late | move | big caught | late on big | BOTTOMS: entries | valid | late | move | big caught | late on big |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| at 1h signal | 21 | 52% | $22 | $79 | 7/8 | $64 | 11 | 45% | $20 | $53 | 2/9 | $67 |
| 15m pullback | 8 | 75% | $21 | $76 | 3/8 | $78 | 3 | 33% | $16 | $29 | 2/9 | $156 |
