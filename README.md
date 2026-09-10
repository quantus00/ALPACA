# Multi-Timeframe Trend Structure Bot

Identifies the **4H trend** from market structure and tells you **UPTREND** or
**DOWNTREND**. When the structure breaks (an uptrend's HH/HL flips to LH/LL, or
vice-versa — **both** legs must break) the 4H trend flips. It then **enters**
only when the **5m, 15m, 30m and 1H** timeframes all align with the new 4H
trend.

Two pieces:

| Piece | File(s) | Job |
|-------|---------|-----|
| **Pine Script** | `pinescript/*.pine` | On-chart signals, alerts, and **backtesting** (TradingView Strategy Tester) |
| **Python bot** | `bot/` | Live trend notifications + order routing to Coinbase / Alpaca / Tradovate / Webull |

Toggles: **broker**, **instrument**, and **contract size** — so the same bot can
trade **BTC/USD spot**, **BTC nano perp**, **SPY options**, and **MES futures**.

---

## 1. Pine Script (TradingView)

**Backtesting is done in Pine Script, on TradingView only.**

- `pinescript/multi_timeframe_trend.pine` — **indicator**: draws the 4H trend
  state (UPTREND/DOWNTREND table + background), plots flip/entry markers, and
  fires `alert()` / `alertcondition()` events. Use this for live alerts →
  webhook → Python bot.
- `pinescript/multi_timeframe_trend_strategy.pine` — **strategy**: same logic
  wired to `strategy.entry` / `strategy.close` so you can measure the edge in
  TradingView's **Strategy Tester** (win rate, drawdown, profit factor). Long
  when everything aligns up, short when everything aligns down, exit when the 4H
  trend flips against you. Optional structure stop-loss and date-range filter.

**Install:** open TradingView → *Pine Editor* → paste a file → *Add to chart*.
Run the strategy on a low timeframe (e.g. 5m) so the alignment timeframes are
all ≥ the chart resolution.

**Alerts → Python:** create an alert on the indicator, set the webhook URL to
your bot's `/webhook` endpoint. The entry alert body is JSON:

```json
{"symbol":"BTCUSD","action":"buy","trend":"UPTREND","price":65000,"time":1700000000000}
```

Plain `UPTREND` / `DOWNTREND` alert bodies are treated as notifications only.

---

## 2. Python bot

### Install

```bash
pip install -r requirements.txt      # plus the SDK for your broker (see file)
cp .env.example .env                  # then fill in your toggles + keys
```

### Toggles

Set in `.env` (or via CLI flags):

| Toggle | Values |
|--------|--------|
| `BOT_BROKER` | `coinbase` · `alpaca` · `tradovate` · `webull` |
| `BOT_INSTRUMENT` | `btc_usd_spot` · `btc_nano_perp` · `spy_options` · `mes_futures` |
| `BOT_CONTRACT_SIZE` | BTC qty, option contracts, or futures contracts |

Valid broker ↔ instrument pairings (enforced at startup):

| Instrument | Brokers |
|------------|---------|
| BTC/USD spot | Coinbase |
| BTC nano perp | Coinbase |
| SPY options | Alpaca, Webull |
| MES futures | Tradovate |

### Run

```bash
# Self-contained: analyze on a timer, notify UPTREND/DOWNTREND, trade on alignment
python -m bot.main poll --broker coinbase --instrument btc_usd_spot --size 0.01

# TradingView-driven: run the webhook and let Pine alerts place the orders
python -m bot.main webhook --broker tradovate --instrument mes_futures --size 1

# One-shot: print the current 4H trend + per-timeframe alignment, no order
python -m bot.main once --broker alpaca --instrument spy_options --size 1
```

`--live` sends **real** orders; the default is **dry-run** (simulated, logged).

### Dashboard (the "trader app" page on your server)

Running in `webhook` mode also serves a dashboard at `/` (e.g.
`http://YOUR_DROPLET_IP/`). Its **"Open trading windows"** button opens **two
separate browser windows** side by side — set what they point to in `.env`:

```ini
BOT_WINDOW_1_URL=https://www.tradingview.com/chart/
BOT_WINDOW_2_URL=https://www.coinbase.com/advanced-trade/spot/BTC-USD
BOT_WINDOW_1_TITLE=Chart
BOT_WINDOW_2_TITLE=Broker
```

Allow pop-ups for the site so both windows can open.

### Notifications

The bot logs `4H TREND: UPTREND` / `DOWNTREND` on every confirmed flip, and can
also push to Discord and/or Telegram (`NOTIFY_*` in `.env`).

---

## 3. How the trend logic works

A swing high/low is a pivot (extreme over `pivot_lookback` bars each side). From
the last two swing highs and lows:

```
UPTREND   = Higher High  AND Higher Low
DOWNTREND = Lower  High  AND Lower  Low   (both legs must break to flip)
```

An uptrend is **not** considered broken until a lower high **and** a lower low
both print — this is the "HH and HL / LH and LL both break the trend" rule. The
Python engine (`bot/trend.py`) and both Pine scripts share this definition, so
backtest and live behaviour match.

Entries fire once per 4H flip, when the 4H trend and all four alignment
timeframes agree; the trigger re-arms once alignment is lost.

---

## 4. Prop-firm challenge guard (trailing drawdown)

`bot/risk.py` is a self-contained risk layer for passing a **trailing-drawdown
evaluation** (Topstep / Apex / TPT style). It turns a raw signal into something
that respects the whole rulebook, checked on every tick:

| Rule | Default | Behaviour |
|------|---------|-----------|
| **Profit target** | `+$1,500` | Pass condition — halt and lock the win in |
| **Daily loss limit** | `$500` | Flatten + stand down for the day; re-arms next session |
| **Trailing drawdown** | `$1,000` | High-water mark trails equity up; touch `peak − DD` → account failed |
| **Flat by 5pm ET** | 17:00 | Hard flatten in the 5–6pm CME maintenance gap |
| **No overnight** | RTH only | Entries only inside the RTH window, up to a 16:55 cutoff |

Two trailing modes — **confirm which your firm uses:**

- `intraday` — the trail follows the intraday **peak equity** (unrealized counts
  against you). Harsher.
- `eod` — the trail only ratchets on the **end-of-day closing balance**, so a
  green day you flatten into *permanently* locks the gain into your buffer.

The floor also **freezes at your starting balance** once reached, matching firms
whose trail stops trailing at breakeven.

```python
from bot.risk import ChallengeGuard, ChallengeParams

guard = ChallengeGuard(ChallengeParams(trailing_mode="eod"))
d = guard.update(now, equity=50_000, position=0)   # now = ET datetime
if d.can_enter:
    contracts = guard.size_for(d, stop_points=5, symbol="MES")  # never oversize
elif d.must_flatten:
    ...  # close everything: hit 5pm, daily loss, or a breach
```

`size_for` caps size to the **smaller** of the remaining daily and trailing
buffers, so a single stop-out can never breach a limit.

Enable it in the live bot via `.env` (`BOT_CHALLENGE_ENABLED=true` + the
`BOT_CHALLENGE_*` toggles); the `StrategyRunner` then vetoes entries and forces
flattens automatically.

**Backtest the ruleset** with `pinescript/prop_challenge_strategy.pine` — it
enforces the same profit target, daily loss, trailing drawdown (intraday/EOD)
and flat-by-5pm rules inside TradingView's Strategy Tester, with a live status
table showing peak equity, the trail floor and your remaining buffer.

---

## 5. Opening Range Breakout — the challenge strategy

`bot/orb.py` + `bot/orb_bot.py` implement the **Opening Range Breakout (ORB)**,
the most rigorously documented intraday edge for index products and the one that
fits a trailing-drawdown evaluation best:

- it trades the **RTH open**, where the volume and clean directional moves are;
- risk is **hard-defined** — the stop sits at the opposite edge of the opening
  range, so the guard can size every trade to a *known* max loss;
- it takes **one (or few) trades a day** — the discipline these accounts reward;
- it is **flat by the session close**, dovetailing with the flat-by-5pm rule.

**Rules** (all configurable via `BOT_ORB_*`):

1. Opening range = high/low of the first `BOT_ORB_MINUTES` of RTH (default 15m).
2. Enter on a breakout of the range by `BOT_ORB_BUFFER_TICKS` (long above, short below).
3. Stop = opposite edge of the range (or a fixed `BOT_CHALLENGE_STOP_POINTS`).
4. Target = `BOT_ORB_TARGET_R` × risk (0 → hold to the end-of-day flatten).
5. Skip days whose range is too tight/wide; cap at `BOT_ORB_MAX_TRADES` per day.

**End to end**, `bot/orb_bot.py` runs `ORB → ChallengeGuard → broker`: the guard
sizes each entry to the breakout's own risk *and* the remaining daily/trailing
buffers, force-flattens at 5pm, and halts once the target is banked.

```bash
python -m bot.orb_bot                 # offline selftest: 2 clean ORB days, guard-sized
python -m bot.orb_bot poll --broker tradovate --instrument mes_futures --symbol MES
```

Backtest it in TradingView with `pinescript/opening_range_breakout_strategy.pine`
(ORB entries + the full challenge guardrails + a live buffer/floor table).

**Research it's based on** — the ORB literature:

- Zarattini, Barbon & Aziz, *A Profitable Day Trading Strategy for the U.S.
  Equity Market* (Swiss Finance Institute, 2024) — [SSRN 4729284](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284)
- Zarattini & Aziz, *Can Day Trading Really Be Profitable?* — [SSRN 4416622](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622)

> **Honest note.** No strategy *guarantees* passing an evaluation — if one did,
> the firms couldn't sell them (pass rates are in the single digits). ORB is a
> well-studied edge with positive expectancy *in the studied markets and
> periods*, and independent work also documents its limits on some instruments
> (e.g. a falsification study on MNQ, [arXiv 2605.04004](https://arxiv.org/pdf/2605.04004)).
> What this code gives you is a **disciplined, mechanical, backtestable**
> framework that (a) trades a researched edge and (b) makes it structurally
> impossible to break the daily-loss / trailing-drawdown / flat-by-5pm rules.
> **Backtest on your instrument, then forward-test on the firm's demo/eval
> before risking a fee.** For education/research; trading involves substantial
> risk of loss.

---

## 6. Tests

```bash
python tests/test_trend.py           # market-structure engine
python tests/test_risk.py            # challenge guard (target, DD, session, sizing)
python tests/test_strategy_guard.py  # guard gates the trend runner
python tests/test_orb.py             # ORB engine (breakout, stop, target, filters)
python tests/test_orb_bot.py         # ORB + guard + broker end to end
# or, if installed:  pytest tests/
```

---

## ⚠️ Disclaimer

For education and research. Trading involves substantial risk of loss. Keep
`BOT_DRY_RUN=true` until you have validated behaviour on paper/demo accounts.
The Webull integration relies on an unofficial community API and may break.
