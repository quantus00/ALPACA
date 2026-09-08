# STREAM — Live Prices Google Sheet (Webull & Coinbase)

A formatted Google Sheet dashboard showing **live crypto and Micro Gold
Futures prices** from **Coinbase** and **Webull**, side by side.

**Your sheet:** https://docs.google.com/spreadsheets/d/1f-PdEabZIRoVFlcFGctp2_92q_2VjoGPztAAheBo8Rg/edit

## What it shows

| Section | Rows | Coinbase | Webull |
|---------|------|----------|--------|
| CRYPTO | BTC, ETH, SOL, DOGE, XRP | price + 24h % | price + 24h % |
| FUTURES | Micro Gold (MGC) | n/a (no gold on Coinbase) | price + 24h % |
| Reference | COMEX Gold `GC=F` | live via Stooq (always works) | — |

One `setup()` run builds the whole formatted layout (title, colored headers,
zebra rows, `$`/`%` number formats, green/red change colors, frozen headers)
and turns on a once-a-minute auto-refresh.

## One-time setup (about 2 minutes)

1. Open the sheet → menu **Extensions ▸ Apps Script**.
2. Delete any sample code, paste the contents of [`live_prices.gs`](./live_prices.gs), click **Save**.
3. In the toolbar pick the function **`setup`** and click **Run**. Approve the
   permission prompt (it fetches prices and edits this sheet).
4. Done. The dashboard is built and refreshes every minute. Run
   **`refreshPrices`** any time for an instant update; **`removeTrigger`** stops
   the auto-refresh.

## Add/remove coins

Edit the `CRYPTOS` array at the top of `live_prices.gs` (name, Coinbase product
id like `ADA-USD`, Webull symbol like `ADAUSD`), then run `setup` again.

## Honest limitations

- **Coinbase has no gold futures** — it's a crypto-only exchange, so that cell
  is `n/a`; the COMEX reference row covers the live gold price.
- **Coinbase** uses the official public API (reliable). **Webull** has no
  official API — the script tries several unofficial endpoints and auto-resolves
  ticker ids, but if Webull blocks the request from Google's servers a Webull
  cell will show `err`. That's a Webull-side limitation, not a bug in the sheet.
- Not financial advice.
