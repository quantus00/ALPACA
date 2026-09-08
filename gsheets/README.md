# Live Prices Google Sheet — Webull & Coinbase

A Google Sheet that shows **live BTC and Micro Gold Futures prices** from
**Webull** and **Coinbase**, side by side.

**Your sheet:** https://docs.google.com/spreadsheets/d/1f-PdEabZIRoVFlcFGctp2_92q_2VjoGPztAAheBo8Rg/edit

## What's in it

The sheet has two blocks:

- **① Works now (no setup):** BTC and front-month gold future (COMEX `GC=F`,
  same price as Micro Gold `MGC`) via built-in `IMPORTDATA` formulas from
  Stooq. These are live the moment you open the sheet.
- **② Exact Webull & Coinbase prices:** the specific exchange feeds you asked
  for. Google Sheets can't read these APIs with a plain formula (they return
  JSON), so a small Apps Script fetches them and refreshes every minute.

## One-time setup for Block ② (about 2 minutes)

1. Open the sheet → menu **Extensions ▸ Apps Script**.
2. Delete the sample code, paste the contents of [`live_prices.gs`](./live_prices.gs), click **Save**.
3. In the toolbar, pick the function **`installTrigger`** and click **Run**.
   Approve the permission prompt (it fetches prices and writes them to the sheet).
4. Done. Block ② refreshes automatically once a minute. Run **`refreshPrices`**
   for an instant update.

## Notes / honest limitations

- **Coinbase gold doesn't exist.** Coinbase is a crypto-only exchange — there
  is no Coinbase gold-futures price anywhere, so that cell is marked `n/a`.
  Block ①'s COMEX gold reference covers the live gold price instead.
- **Coinbase BTC** uses the official public API — reliable.
- **Webull** has no official public API. The script uses an unofficial endpoint,
  so Webull cells are best-effort and may show `err` if Webull blocks the
  request. The Webull **Micro Gold Futures** ticker id can change when the
  front-month contract rolls; update `WEBULL_MGC_TICKER_ID` in the script if
  that cell reads `err`.
- Not financial advice.
