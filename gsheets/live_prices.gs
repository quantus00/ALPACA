/**
 * Live BTC & Micro Gold Futures — Webull & Coinbase
 * =================================================
 * Fills "Block ②" of the sheet and keeps it refreshing every minute.
 *
 * ONE-TIME SETUP
 *   1. Open the sheet, then: Extensions ▸ Apps Script
 *   2. Delete any sample code, paste this whole file, click Save (disk icon).
 *   3. In the toolbar, choose the function `installTrigger` and click Run.
 *      Approve the permission prompt (it needs to fetch prices from the web
 *      and write them into this sheet).
 *   4. Done. Block ② now updates automatically once a minute.
 *
 * Run `refreshPrices` any time you want an instant update.
 *
 * Coinbase uses the official public API and is reliable. Webull has no public
 * API, so it uses an unofficial endpoint — best-effort. If a Webull cell shows
 * "err", Webull blocked the request; Block ① (Stooq) still gives you a live
 * gold/BTC price. Nothing here is financial advice.
 */

// ---- Cell layout — must match Block ② of the sheet -------------------------
var LAYOUT = {
  webullBtc:    { price: 'D11', ts: 'E11' },
  webullGold:   { price: 'D12', ts: 'E12' },
  coinbaseBtc:  { price: 'D13', ts: 'E13' },
  lastRefresh:  'B16',
};

// Webull internal ticker ids. BTCUSD is stable; the MGC futures id can change
// (front-month rolls), so that cell is the most likely to show "err".
var WEBULL_BTC_TICKER_ID  = 950160802;   // BTC / USD (crypto)
var WEBULL_MGC_TICKER_ID  = 461460684;   // Micro Gold Futures — update if wrong

// ---------------------------------------------------------------------------
// Public entry points
// ---------------------------------------------------------------------------

/** Install the once-a-minute auto-refresh trigger, then refresh immediately. */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'refreshPrices') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('refreshPrices').timeBased().everyMinutes(1).create();
  refreshPrices();
}

/** Remove the auto-refresh trigger. */
function removeTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'refreshPrices') ScriptApp.deleteTrigger(t);
  });
}

/** Fetch every price and write it into the sheet. Safe to run manually. */
function refreshPrices() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheets()[0];
  var stamp = Utilities.formatDate(
    new Date(), ss.getSpreadsheetTimeZone(), 'MMM d  HH:mm:ss');

  writePrice(sh, LAYOUT.coinbaseBtc, stamp, function () {
    return coinbaseSpot('BTC-USD');
  });
  writePrice(sh, LAYOUT.webullBtc, stamp, function () {
    return webullQuote(WEBULL_BTC_TICKER_ID, 'crypto');
  });
  writePrice(sh, LAYOUT.webullGold, stamp, function () {
    return webullQuote(WEBULL_MGC_TICKER_ID, 'futures');
  });

  sh.getRange(LAYOUT.lastRefresh).setValue(stamp);
  SpreadsheetApp.flush();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function writePrice(sh, cells, stamp, fetchFn) {
  try {
    var value = fetchFn();
    if (!isFinite(value) || value <= 0) throw new Error('no price');
    sh.getRange(cells.price).setValue(value);
    sh.getRange(cells.ts).setValue(stamp);
  } catch (err) {
    sh.getRange(cells.price).setValue('err');
    sh.getRange(cells.ts).setValue(String(err).slice(0, 90));
  }
}

/** Coinbase official public spot price, e.g. coinbaseSpot('BTC-USD'). */
function coinbaseSpot(pair) {
  var url = 'https://api.coinbase.com/v2/prices/' + pair + '/spot';
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  var body = JSON.parse(res.getContentText());
  return Number(body.data.amount);
}

/**
 * Webull unofficial real-time quote. `kind` is 'crypto' or 'futures' — they
 * live behind slightly different endpoints. Returns the last/close price.
 */
function webullQuote(tickerId, kind) {
  var base = 'https://quotes-gw.webullfintech.com/api';
  var url = (kind === 'crypto')
    ? base + '/crypto/quote/tickerRealTimes?ids=' + tickerId + '&more=1'
    : base + '/quote/tickerRealTimes/full?ids=' + tickerId;

  var res = UrlFetchApp.fetch(url, {
    muteHttpExceptions: true,
    headers: { 'did': webullDeviceId(), 'App-Group': 'broker', 'Accept': 'application/json' },
  });
  var data = JSON.parse(res.getContentText());
  var q = Array.isArray(data) ? data[0] : data;
  if (!q) throw new Error('empty quote');
  return Number(q.close || q.price || q.pPrice || q.lastPrice);
}

/** A stable per-script device id keeps Webull's endpoint happy. */
function webullDeviceId() {
  var props = PropertiesService.getScriptProperties();
  var did = props.getProperty('WB_DID');
  if (!did) {
    did = Utilities.getUuid().replace(/-/g, '');
    props.setProperty('WB_DID', did);
  }
  return did;
}
