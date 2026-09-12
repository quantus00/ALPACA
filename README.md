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

## 4. Webull crypto paper trading (standalone script)

Want to just place a **paper** crypto trade on Webull — pick the coin, pick the
amount — without running the whole bot? Use `webull_crypto_paper_trade.py`. It
runs straight from your terminal or the VS Code **Run** button and only ever
touches your Webull **paper** account.

```bash
pip install webull                    # community, unofficial Webull client
cp .env.example .env                  # fill in WEBULL_EMAIL / PASSWORD / TRADE_PIN

# Preview without logging in or sending anything:
python webull_crypto_paper_trade.py --crypto BTC --amount 100 --dry-run

# Place a real paper order — $100 of Bitcoin:
python webull_crypto_paper_trade.py --crypto BTC --amount 100

# By quantity instead of dollars, on the sell side:
python webull_crypto_paper_trade.py --crypto ETH --qty 0.25 --side sell

# No flags → it asks you for coin, side and amount interactively:
python webull_crypto_paper_trade.py
```

| Flag | Meaning |
|------|---------|
| `--crypto` / `--symbol` | Coin to trade: `BTC`, `ETH`, `DOGE`, `BTC-USD`, `BTCUSD`, … |
| `--amount` | Dollar amount (e.g. `100` = $100 of the coin) |
| `--qty` | Quantity of the coin instead of a dollar amount |
| `--side` | `buy` (default) or `sell` |
| `--limit` | Limit price; omit for a market order |
| `--dry-run` | Validate + preview only — never logs in or sends |
| `--yes` / `-y` | Skip the confirmation prompt |

**VS Code:** open the folder, press **F5** (or the green Run arrow) and pick
*"Webull paper trade (interactive)"* or the BTC dry-run config in
`.vscode/launch.json`.

Credentials come from the environment or `.env` (`WEBULL_EMAIL`,
`WEBULL_PASSWORD`, `WEBULL_TRADE_PIN`, optional `WEBULL_DEVICE_ID`). This uses
the unofficial community `webull` package and its `paper_webull` client, so it
can break if Webull changes their endpoints — keep it on paper.

---

## 5. Tests

```bash
python tests/test_trend.py      # or: pytest tests/
```

---

## ⚠️ Disclaimer

For education and research. Trading involves substantial risk of loss. Keep
`BOT_DRY_RUN=true` until you have validated behaviour on paper/demo accounts.
The Webull integration relies on an unofficial community API and may break.
