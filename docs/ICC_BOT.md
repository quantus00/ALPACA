# ICC Trading Bot (Webull + Coinbase)

A mechanical implementation of the SCI **ICC** price-action method
(*Indication → Correction → Continuation*), able to drive **Webull** (equities)
or **Coinbase** (crypto) through a shared strategy engine.

> ⚠️ **Read this first.** ICC as taught is a *discretionary* method — roughly
> 70% visual judgment. This bot is a deterministic **approximation**, not the
> trader's intuition. It can and will be wrong, and automated trading can lose
> money fast. It is **not financial advice**. Run it in **dry-run**, then
> **paper**, for a long time before you ever consider live.

## How it maps the course to code

| ICC concept | Mechanical rule in this bot |
|---|---|
| Structure / trend (1H, 4H) | Fractal **pivot** swings; trend = HH+HL (long) / LH+LL (short); anything else = no-trade |
| Indication (new high/low) | The fresh break implied by the trend; origin = last opposing swing ("previous push") |
| Correction | Pullback that stays on the trend side of the origin level |
| Continuation (entry, 15m/5m) | Lower-timeframe break back in the trend direction |
| Stop | Below the higher-low (long) / above the lower-high (short) — structure invalidation |
| Target | Opposing HTF level **or** a projected R multiple, whichever is further (guarantees the R:R) |
| Risk:reward | `ICC_TARGET_RR` (default **3.0**) |
| Session | Only trade inside a UTC window (default ≈ NY open) |

Full rationale is in each module's docstring: `strategy.py`, `structure.py`, `risk.py`.

## Run it 24/7 (systemd)

To keep the bot running after you log out, with auto-restart:

```bash
cd ~/ALPACA
cp deploy/icc-bot.env.example /root/icc-bot.env      # then edit settings/keys
sudo cp deploy/icc-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now icc-bot                  # start now + on every boot
journalctl -u icc-bot -f                             # watch the live heartbeat
```

Change settings by editing `/root/icc-bot.env` then `sudo systemctl restart icc-bot`.
Stop with `sudo systemctl stop icc-bot`. (Alternatively, for a quick background
run without systemd: `tmux new -s icc` then run `icc-bot`, detach with Ctrl-b d.)

## Coinbase derivatives (futures + perps)

Set `ICC_VENUE`:

| Venue | What | Sizing | Notes |
|---|---|---|---|
| `spot` | Advanced Trade spot | base units | no paper sandbox |
| `futures` | Coinbase Financial Markets US futures (dated + nano) | **whole contracts** | balances via futures endpoints |
| `perp` | Coinbase International (INTX) perpetuals | **whole contracts** | needs `COINBASE_PORTFOLIO_UUID` |

### IDs roll monthly — so don't hardcode them

Expiring futures change IDs every month (`ROOT-DDMMMYY-CDE`). Two ways to feed
the bot, both avoiding stale IDs:

1. **Bare roots (recommended)** — pass a root and the bot resolves the current
   **front-month** contract each day and reads its `contract_size` from the API:
   ```bash
   ICC_BROKER=coinbase ICC_VENUE=futures ICC_SYMBOLS=BIT,GOL ICC_MODE=dry_run icc-bot
   ```
   Roots: `BIT` nano BTC, `BIP` nano-BTC perp-style, `GOL` gold, `NOL` oil.
2. **Explicit product IDs** — pass full IDs and (optionally) pin multipliers:
   ```bash
   ICC_VENUE=perp ICC_SYMBOLS=BTC-PERP COINBASE_PORTFOLIO_UUID=... \
   ICC_CONTRACT_SPECS=BTC-PERP=1.0 ICC_LEVERAGE=2 ICC_MODE=dry_run icc-bot
   ```

Discover live products, contract sizes, and your INTX `portfolio_uuid`:
```bash
icc-discover          # prints PORTFOLIOS + FUTURES tables
```
Confirmed multipliers: gold `GOL` = 1 troy oz, oil `NOL` = 10 barrels, nano BTC
`BIT` = 0.01 BTC, INTX perps = 1 (except `1000XXX-PERP` = 1000). If no INTX
portfolio appears, perps aren't enabled on your account (US retail gets the
`BIP` perp-style futures instead) — the bot says so rather than guessing.

Credentials: point `COINBASE_KEY_FILE` at your CDP key JSON (or set
`COINBASE_API_KEY` + `COINBASE_API_SECRET`).

⚠️ **Leverage magnifies losses.** Sizing is still by stop-distance (correct and
venue-agnostic), and the notional cap acts as a leverage guard — but a wrong
multiplier means a wrong position size. Verify sizes in dry-run first.

## Three ways to run (separate launchers)

All three share the same ICC engine. From `~/ALPACA` (venv active):

### 1. Backtest — replay history, no keys needed
```bash
scripts/backtest.sh --ltf-csv "COINBASE_BTCUSD, 15.csv" --htf-seconds 3600 \
  --equity 10000 --risk-pct 1 --out trades.csv
```
Feed a TradingView/Coinbase candle CSV (your entry timeframe, e.g. 15m). It
resamples to the structure timeframe, replays bar-by-bar through `evaluate()` +
the simulator, and prints win rate, avg R, profit factor, return %, max
drawdown (and writes each trade to `--out`).

### 2. Paper — live data, simulated fills + P&L, no real orders
```bash
scripts/run_paper.sh            # loops; add --once for a single cycle
```
Keeps a virtual account (`ICC_PAPER_EQUITY`, default 10k). Logs OPEN/CLOSE with
running equity — a true forward-test with zero risk.

### 3. Live — real orders, real money
```bash
ICC_I_UNDERSTAND_LIVE_RISK=yes ICC_RISK_PCT=0.5 scripts/run_live.sh
```
Refuses to start unless `ICC_I_UNDERSTAND_LIVE_RISK=yes`. Places native bracket
orders on Coinbase (stop + target enforced on-venue). For 24/7 use the systemd
unit above with `ICC_MODE=live` in `/root/icc-bot.env`.

Override any setting via env vars, e.g. `ICC_SYMBOLS=BIT,GOL scripts/run_paper.sh`.

## Safety model (three modes)

- **`dry_run`** (default) — fetches live data, computes signals, logs the order it
  *would* place, but **places nothing** and keeps no P&L.
- **`paper`** — live data, **simulated fills + running P&L** via the built-in
  simulator (Coinbase has no real sandbox, so this is how you forward-test).
- **`live`** — real orders with real money. Requires **both** `ICC_MODE=live` **and**
  `ICC_I_UNDERSTAND_LIVE_RISK=yes`; otherwise it falls back to dry-run.

Hard guardrails enforced every cycle (see `risk.py`): risk-per-trade sizing,
max daily loss (kill switch), max open positions, max trades/day, notional cap.

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .                 # core
pip install -e '.[coinbase]'     # + Coinbase SDK
pip install -e '.[webull]'       # + Webull package
```

## Configure (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `ICC_BROKER` | `coinbase` | `coinbase` or `webull` |
| `ICC_MODE` | `dry_run` | `dry_run` / `paper` / `live` |
| `ICC_SYMBOLS` | `BTC-USD` (cb) / `QQQ` (wb) | comma-separated |
| `ICC_HTF` / `ICC_LTF` | `1h` / `15m` | structure / entry timeframes |
| `ICC_TARGET_RR` | `3.0` | reward:risk |
| `ICC_RISK_PCT` | `1.0` | % equity risked per trade |
| `ICC_MAX_DAILY_LOSS_PCT` | `3.0` | daily kill-switch |
| `ICC_MAX_TRADES_PER_DAY` | `3` | throttle |
| `ICC_SESSION_START_UTC` / `_END_UTC` | `13` / `16` | trading window (UTC) |
| `ICC_I_UNDERSTAND_LIVE_RISK` | — | must be `yes` to arm live |

Credentials: **Coinbase** → `COINBASE_API_KEY`, `COINBASE_API_SECRET`
(Coinbase Developer Platform key). **Webull** → `WEBULL_EMAIL`,
`WEBULL_PASSWORD`, `WEBULL_TRADE_PIN` (MFA may be required on first login).

## Run

```bash
# Coinbase, crypto, watch-only (safe):
ICC_BROKER=coinbase ICC_SYMBOLS=BTC-USD,SOL-USD ICC_MODE=dry_run icc-bot

# Webull paper account, equities:
ICC_BROKER=webull ICC_SYMBOLS=QQQ ICC_MODE=paper icc-bot
```

`icc-bot` is the console script (equivalently `python -m icc_bot`). It logs a
one-line heartbeat every cycle (`symbol=status@price`) and, on a setup, the full
bracket it would place. In dry-run nothing is sent.

Run a single cycle and exit (testing / cron) instead of looping:

```bash
icc-bot --once
```

## Recommended path

1. **dry-run** on your droplet for days; compare its signals to what you'd take by eye.
2. Tune `ICC_*` params so the flagged setups match your intent.
3. Move to **paper** (Webull) and watch fills/PnL.
4. Only then consider a tiny **live** allocation — and keep the daily-loss kill switch tight.

## Calibration from the wider SCI material

Beyond the 14 core lessons, the trader's second (more granular) course and vlogs
were reviewed. They re-teach the same method; the concrete settings worth pinning:

- **Swing sensitivity:** *Pivot Points High Low* length ≈ **5** → `ICC_HTF_LOOKBACK=5`, `ICC_LTF_LOOKBACK=3` (now the defaults).
- **Frequency:** **1–2 trades/week** → `ICC_MAX_TRADES_PER_DAY=2` default.
- **R:R:** **1:3–1:4** → `ICC_TARGET_RR=3.0` (raise to 4.0 to match his upper end).
- **Alignment:** he wants 4H + 1H (± 30m) all agreeing before entry.
- **No trailing stop**; partials at the first target, hold the rest while structure holds.

### His primary instrument isn't on these venues ⚠️

He trades mostly **spot gold (XAUUSD)** and **NASDAQ**, neither of which Coinbase
(crypto only) or Webull (US equities) offers as-is. Closest proxies:
- Gold → **PAXG-USD** (tokenized gold) on Coinbase, or **GLD/IAU** ETF on Webull.
- Nasdaq → **QQQ** ETF on Webull.
These track his instruments but are **not identical** (hours, spreads, gaps differ).

## Limitations

- Deterministic pivots won't always agree with a human's eye for swings.
- On-venue exits: Coinbase entries use a **native OCO bracket**
  (`trigger_bracket_order_gtc_*`) so the stop-loss and take-profit are enforced by
  the exchange even if the bot goes offline. Webull has no native bracket here yet,
  so it places the entry only and warns (wire OTOCO before live).
- Coinbase has no paper sandbox; Webull login/MFA is fragile and version-specific.
- Timeframes are limited to what each venue's API exposes (e.g., no 4h on Coinbase).
- Not backtested (the course discourages it); validate forward on paper instead.
