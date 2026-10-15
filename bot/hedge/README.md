# ICC hedge module (paper by default)

"Hedge when the trade goes against me", layered on the existing ICC entry. The
module never changes how entries are decided, and it never sends a hedge order
to a broker.

| File | Job |
|---|---|
| `hedge_config.toml` | **Every number** (trigger, gate, lift, cut, limits, fees, sizing). |
| `features.py` | 1h ATR(14), 1h/4h structure + swings, rolling correlation. Closed bars only, no look-ahead. |
| `engine.py` | The rules: trigger → hedge / gate skip / normal stop, lift, cut + trail, hard limits, one hedge per primary. |
| `paper.py` | Paper-trading manager for the live bot. Logs every event with its reason to `logs/hedge_events.jsonl`. |
| `hook.py` | The only seam into the bot (`bot/main.py` poll loop). |
| `../../backtest/hedge_backtest.py` | 12-month, 3-variant backtest using the same engine. |
| `../../pinescript/icc_hedge_strategy.pine` | TradingView version (Strategy Tester plus alert() paper log). |

## Broker check (done before coding)

| Need | What's wired in this repo | Result |
|---|---|---|
| BTC spot long | `btc_usd_spot` → **Coinbase** only. The Alpaca adapter only trades SPY options. | Primary uses the bot's existing BTC spot path. Moving BTC spot to Alpaca means editing `config.py` and `brokers/alpaca.py`, so it's waiting on your OK. |
| BTC short hedge | The bot does **not** trade gold. GC appears only as backtest data. The only perp/futures venue wired for BTC is the **Coinbase nano BTC perp** (`BTC-PERP-INTX`). CME micro BTC isn't wired: the Tradovate adapter is MES-only. | Hedge = Coinbase nano BTC perp, short, 1:1 USD notional. |
| EUR/USD + USD/CHF | Alpaca: no FX. Webull: no FX. Tradovate: adapter is MES-only (CME FX futures exist there but aren't wired). Coinbase: `coinbase_fx` is a placeholder id. Coinbase has no USD/CHF spot market. The bot has no FX candle feed. | **No connected broker trades both legs, so forex is backtest-only** (Python + Pine). |

## How each rule is implemented

- **Trigger** (1h close): `close <= entry − 1.0×ATR(14)` (long; mirrored for short),
  OR a close beyond the **ICC correction level**. That level is the last confirmed 1h swing low
  (long) or swing high (short) before entry. ATR is frozen at entry (`atr_reference`).
- **Normal stop** (variant a, failed corr gate, or a 2nd trigger after a lift) is the
  trigger itself: the primary closes at that 1h close.
- **Correlation gate** (forex): 30-day rolling Pearson corr of 1h returns, EURUSD vs
  USDCHF. Hedge only if `< -0.80`. Otherwise `HEDGE_SKIPPED` is logged and the normal stop applies.
- **Hedge size**: crypto, perp short notional = 1.0 × primary notional at the trigger
  price. Forex, USD/CHF in the same direction with USD units = EUR units × EURUSD, which
  offsets the EUR/USD position's USD exposure.
- **Lift**: the 1h structure trend agrees with the primary (toggle
  `continuation_requires_1h_trend`), the close breaks the last confirmed 1h swing in the
  primary's direction, and the close reclaims the trigger level. The hedge closes and the primary stays.
- **Cut**: the 4h HH/HL ↔ LH/LL structure flips against the primary (same engine as
  `bot/trend.py`). The primary closes and the hedge trails at 1×ATR(1h). The stop is checked intrabar.
- **Limits** (both legs close): setup P&L ≤ −2% of `equity_usd`; hedge open ≥ 72h
  (crypto) or ≥ 5×24 trading bars (forex); crypto cumulative funding *paid* > 0.5% of
  hedge notional. Funding is logged every settlement.
- **One hedge per primary**: a second trigger after a lift is a normal stop, and
  `open_primary` refuses a second setup.
- **Existing ICC exit kept**: an unhedged primary exits on a 4h flip against it.

## Paper trading

```bash
BOT_HEDGE_PAPER=true python -m bot.main poll --broker coinbase --instrument btc_usd_spot --size 0.01
tail -f logs/hedge_events.jsonl
```

Keep the bot in its default dry-run mode. The hedge module simulates both legs itself, at
1h closes, and writes one JSON line per event (`PRIMARY_OPEN`, `HEDGE_OPEN`,
`HEDGE_SKIPPED`, `HEDGE_LIFT`, `PRIMARY_CUT`, `HEDGE_TRAIL_EXIT`, `LIMIT_CLOSE`,
`PRIMARY_STOP`, `PRIMARY_EXIT`, `FUNDING`, `SETUP_CLOSED`). Each line carries its reason.
Discord/Telegram get the same lines if `NOTIFY_*` is set.
## Live hedge orders (off by default)

Live trading needs **both** switches on: `safety.live_trading_approved = true` in
`hedge_config.toml`, **and** the bot started with `--live`. If either one is off, everything stays paper.
When live, `live.py` turns each simulated fill into a real order. Primary closes
(stop / cut / limit) go out as spot sells, and hedge opens/closes go to `crypto.hedge_symbol` on Coinbase.
The primary entry is never re-sent. Guards: `max_hedge_btc` caps any single hedge order,
`hedge_contract_btc` / `hedge_size_increment` round the order size, and the module **halts**
(sends nothing more, logs CRITICAL, notifies) on the first failed order.

Before going live, check that `hedge_symbol` matches your account's real perp product id.
`BTC-PERP-INTX` is Coinbase International; US accounts use a nano perp like `BIP-…-CDE`,
sized at 0.01 BTC per contract, which means setting `hedge_contract_btc = 0.01` and
`hedge_size_increment = 1`.

## Backtest

```bash
python3 backtest/fetch_hedge_data.py     # needs internet: Coinbase, Yahoo, Coinbase INTX funding
python3 backtest/hedge_backtest.py       # -> backtest/hedge_results.md, backtest/hedge_events.csv
```

Entries in the Python backtest use 4H + 1H alignment only, because 1h data can't give 5m/15m/30m.
The Pine version can include those timeframes if your TradingView history goes back far enough.
All three variants share identical entries.
