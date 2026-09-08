# Google Sheets live-price tools (Webull & Coinbase)

Two Apps Script–powered Google Sheets.

## 1. STREAM — live prices ([`live_prices.gs`](./live_prices.gs))

https://docs.google.com/spreadsheets/d/1f-PdEabZIRoVFlcFGctp2_92q_2VjoGPztAAheBo8Rg/edit

Tracks five instruments, refreshing every minute:

| Row | Source |
|-----|--------|
| BTC Spot — Coinbase | Coinbase official API (reliable) |
| BTC Spot — Webull | Webull unofficial API (best-effort) |
| BTC Perp | OKX → Bybit → Binance perp API |
| Micro Gold **MGC** (per oz) | Stooq COMEX gold `GC` |
| Gold **1 troy oz** | same per-oz gold price, ×1 notional |
| Micro E-mini S&P **MES** | Stooq CME S&P `ES` |

MGC and "1 oz" share the same per-oz gold price — only the *contract value* column differs (MGC = price × 10 oz, 1 oz = price × 1). MES contract value = index × $5.

## 2. ARB SCAN — cross-exchange spread scanner ([`arb_scan.gs`](./arb_scan.gs))

https://docs.google.com/spreadsheets/d/1T4tW-vKiJ9kvsho47-uNofDQj0LokMYPvRfXg0m79AY/edit

For each coin, pulls live spot prices from **9 venues** (Coinbase, Kraken, Gemini,
Bitstamp, Binance.US, Bybit, OKX, KuCoin, **Webull**), finds the cheapest buy and
dearest sell, and computes net profit on your trade size after taker fees. Rows
that clear the net target (default $20) are highlighted. Refreshes every 2 min.

## Setup (each sheet, one time)

1. Open the sheet → **Extensions ▸ Apps Script**.
2. Delete any sample code, paste the matching `.gs` file, click **Save**.
3. Run **`setup`**, approve the permission prompt.

## Pause / resume

Both scripts have **`pause`** and **`resume`** functions:
- Run **`pause`** to stop auto-refresh (values freeze).
- Run **`resume`** to start it again and refresh now.
- Or use the ⏰ **Triggers** panel in Apps Script to delete/re-add the trigger.

## Notes

- Coinbase & the perp/spot exchange APIs are real-time; **Stooq is free but ~15–60 min delayed**, continuous front-month for futures.
- **Webull** is an unofficial endpoint — best-effort, may show `err`. A Webull API token would make it reliable (and unlock MGC futures + order placement).
- **Coinbase has no gold and no metals**; gold comes from Stooq (COMEX). Coinbase nano futures and Webull's futures API have no free live feed.
- Not financial advice.
