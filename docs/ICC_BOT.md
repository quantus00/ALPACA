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

## Safety model (three modes)

- **`dry_run`** (default) — fetches live data, computes signals, **places nothing**. Start here.
- **`paper`** — routes orders to the broker's paper account (Webull) — Coinbase has no
  sandbox, so `paper` behaves as dry-run there.
- **`live`** — real orders with real money. Requires **both** `ICC_MODE=live` **and**
  `ICC_I_UNDERSTAND_LIVE_RISK=yes`; otherwise it silently falls back to dry-run.

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

`icc-bot` is the console script (equivalently `python -m icc_bot`). It logs every
signal (direction, entry, stop, target, R:R, size). In dry-run it prints the
order it *would* have placed.

## Recommended path

1. **dry-run** on your droplet for days; compare its signals to what you'd take by eye.
2. Tune `ICC_*` params so the flagged setups match your intent.
3. Move to **paper** (Webull) and watch fills/PnL.
4. Only then consider a tiny **live** allocation — and keep the daily-loss kill switch tight.

## Limitations

- Deterministic pivots won't always agree with a human's eye for swings.
- No native bracket orders yet — stop/target are computed and logged; wire them to
  the broker's bracket endpoints (Coinbase `trigger_bracket_order_*`) before live.
- Coinbase has no paper sandbox; Webull login/MFA is fragile and version-specific.
- Timeframes are limited to what each venue's API exposes (e.g., no 4h on Coinbase).
- Not backtested (the course discourages it); validate forward on paper instead.
