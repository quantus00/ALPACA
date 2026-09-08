/**
 * STREAM — Live Crypto & Micro Gold Futures dashboard (Webull & Coinbase)
 * ======================================================================
 * One script builds the whole formatted sheet, fills it with live prices from
 * Coinbase (official public API) and Webull (unofficial, best-effort), and
 * refreshes every minute.
 *
 * SETUP (one time)
 *   1. Open the STREAM sheet -> Extensions > Apps Script
 *   2. Delete any sample code, paste this whole file, click Save.
 *   3. In the toolbar choose the function `setup` and click Run.
 *      Approve the permission prompt (fetch prices + edit this sheet).
 *   4. Done. The dashboard is built and auto-refreshes every minute.
 *
 * Re-run `setup` any time to rebuild the layout. Run `refreshPrices` for an
 * instant price update without touching the formatting.
 *
 * Coinbase is reliable. Webull has no public API, so Webull cells are
 * best-effort and may show "err" if blocked. Not financial advice.
 */

// ---- What to track ---------------------------------------------------------
var CRYPTOS = [
  { name: 'Bitcoin',  cb: 'BTC-USD',  wb: 'BTCUSD'  },
  { name: 'Ethereum', cb: 'ETH-USD',  wb: 'ETHUSD'  },
  { name: 'Solana',   cb: 'SOL-USD',  wb: 'SOLUSD'  },
  { name: 'Dogecoin', cb: 'DOGE-USD', wb: 'DOGEUSD' },
  { name: 'XRP',      cb: 'XRP-USD',  wb: 'XRPUSD'  },
];

// Known Webull crypto ticker ids (used first; the rest are auto-resolved).
var WEBULL_KNOWN_IDS = { BTCUSD: 950160802 };

// ---- Fixed layout ----------------------------------------------------------
var COL = { asset: 1, cb: 2, wb: 3, cbChg: 4, wbChg: 5, updated: 6 };
var R = {
  title: 1, sub: 2,
  cryptoHdr: 4, cryptoCols: 5, cryptoStart: 6,          // 5 coins -> rows 6..10
  futHdr: 12, futCols: 13, gold: 14, goldRef: 15,
  lastRefresh: 17, note1: 19, note2: 20,
};
var COLORS = {
  ink: '#0b1220', head: '#111827', accent: '#1f6feb',
  band: '#f1f5f9', good: '#0e7c3a', bad: '#c0392b', muted: '#64748b',
};

// ===========================================================================
// Entry points
// ===========================================================================

/** Build/rebuild the formatted dashboard and turn on auto-refresh. */
function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheets()[0];
  sh.setName('STREAM');
  buildLayout(sh);
  installTrigger();          // also does the first refresh
}

/** Alias, in case you followed the older instructions. */
function installTriggerAndBuild() { setup(); }

/** Install the once-a-minute refresh trigger, then refresh now. */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'refreshPrices') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('refreshPrices').timeBased().everyMinutes(1).create();
  refreshPrices();
}

/** Stop the auto-refresh. */
function removeTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'refreshPrices') ScriptApp.deleteTrigger(t);
  });
}

/** Fetch every price and write it in. Safe to run any time. */
function refreshPrices() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheets()[0];
  var stamp = Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), 'MMM d  HH:mm:ss');

  for (var i = 0; i < CRYPTOS.length; i++) {
    var row = R.cryptoStart + i;
    var c = CRYPTOS[i];
    writeQuote(sh, row, stamp, function () { return coinbaseQuote(c.cb); },
                                function () { return webullCrypto(c.wb); });
  }

  // Micro Gold Futures: Coinbase has none; Webull best-effort.
  sh.getRange(R.gold, COL.cb).setValue('n/a');
  writeOne(sh, R.gold, COL.wb, COL.wbChg, R.gold, stamp, function () { return webullFutures(); });

  sh.getRange(R.lastRefresh, COL.cb).setValue(stamp);
  SpreadsheetApp.flush();
}

// ===========================================================================
// Layout + formatting
// ===========================================================================

function buildLayout(sh) {
  sh.clear();
  sh.getDataRange().clearFormat();
  if (sh.getMaxColumns() > 6) sh.deleteColumns(7, sh.getMaxColumns() - 6);

  // Title
  sh.getRange(R.title, 1, 1, 6).merge()
    .setValue('STREAM  —  Live Crypto & Micro Gold Futures')
    .setFontSize(16).setFontWeight('bold').setFontColor('#ffffff')
    .setBackground(COLORS.head).setVerticalAlignment('middle');
  sh.setRowHeight(R.title, 40);
  sh.getRange(R.sub, 1, 1, 6).merge()
    .setValue('Coinbase (official API) vs Webull (unofficial, best-effort). Auto-refreshes every minute.')
    .setFontColor(COLORS.muted).setFontSize(10);

  headerBlock(sh, R.cryptoHdr, R.cryptoCols, 'CRYPTO');
  headerBlock(sh, R.futHdr, R.futCols, 'FUTURES');

  // Crypto asset names + section rows
  for (var i = 0; i < CRYPTOS.length; i++) {
    sh.getRange(R.cryptoStart + i, COL.asset).setValue(CRYPTOS[i].name).setFontWeight('bold');
  }
  sh.getRange(R.gold, COL.asset).setValue('Micro Gold Futures (MGC)').setFontWeight('bold');

  // COMEX reference — native formula, always live, no fetch needed.
  sh.getRange(R.goldRef, COL.asset).setValue('COMEX Gold (GC=F) reference').setFontColor(COLORS.muted);
  sh.getRange(R.goldRef, COL.cb).setFormula(
    '=IFERROR(INDEX(IMPORTDATA("https://stooq.com/q/l/?s=gc.f&f=sd2t2ohlc&h&e=csv"),2,7),"—")');
  sh.getRange(R.goldRef, COL.updated).setValue('Stooq, ~hourly').setFontColor(COLORS.muted).setFontSize(9);

  // Last refresh + notes
  sh.getRange(R.lastRefresh, COL.asset).setValue('Last auto-refresh:').setFontWeight('bold');
  sh.getRange(R.note1, 1, 1, 6).merge()
    .setValue('Coinbase is crypto-only, so it has no gold-futures price — the COMEX reference row covers gold.')
    .setFontColor(COLORS.muted).setFontSize(9);
  sh.getRange(R.note2, 1, 1, 6).merge()
    .setValue('If a Webull cell shows "err", Webull blocked the request. Not financial advice.')
    .setFontColor(COLORS.muted).setFontSize(9);

  // Number formats
  var priceFmt = '$#,##0.00####';
  var pctFmt = '0.00%;-0.00%';
  sh.getRange(R.cryptoStart, COL.cb, CRYPTOS.length, 1).setNumberFormat(priceFmt);
  sh.getRange(R.cryptoStart, COL.wb, CRYPTOS.length, 1).setNumberFormat(priceFmt);
  sh.getRange(R.cryptoStart, COL.cbChg, CRYPTOS.length, 2).setNumberFormat(pctFmt);
  sh.getRange(R.gold, COL.cb, 2, 2).setNumberFormat(priceFmt);
  sh.getRange(R.gold, COL.cbChg, 1, 2).setNumberFormat(pctFmt);

  // Zebra banding on data rows
  band(sh, R.cryptoStart, CRYPTOS.length);
  band(sh, R.gold, 2);

  // Column widths + freeze
  sh.setColumnWidth(COL.asset, 210);
  sh.setColumnWidth(COL.cb, 130); sh.setColumnWidth(COL.wb, 130);
  sh.setColumnWidth(COL.cbChg, 95); sh.setColumnWidth(COL.wbChg, 95);
  sh.setColumnWidth(COL.updated, 130);
  sh.setFrozenRows(R.cryptoCols);
  sh.setHiddenGridlines ? sh.setHiddenGridlines(true) : null;
}

function headerBlock(sh, hdrRow, colRow, label) {
  sh.getRange(hdrRow, 1, 1, 6).merge().setValue(label)
    .setFontWeight('bold').setFontColor('#ffffff').setBackground(COLORS.accent);
  var heads = ['Asset', 'Coinbase (USD)', 'Webull (USD)', 'CB 24h %', 'WB 24h %', 'Updated'];
  sh.getRange(colRow, 1, 1, 6).setValues([heads])
    .setFontWeight('bold').setBackground(COLORS.band).setFontColor(COLORS.ink);
}

function band(sh, startRow, n) {
  for (var i = 0; i < n; i++) {
    if (i % 2 === 1) sh.getRange(startRow + i, 1, 1, 6).setBackground(COLORS.band);
  }
}

// ===========================================================================
// Writers
// ===========================================================================

function writeQuote(sh, row, stamp, cbFn, wbFn) {
  writeOne(sh, row, COL.cb, COL.cbChg, row, stamp, cbFn);
  writeOne(sh, row, COL.wb, COL.wbChg, row, stamp, wbFn);
  sh.getRange(row, COL.updated).setValue(stamp).setFontColor(COLORS.muted).setFontSize(9);
}

function writeOne(sh, row, priceCol, chgCol, tsRow, stamp, fn) {
  try {
    var q = fn();                              // { price, chg }
    if (!q || !isFinite(q.price) || q.price <= 0) throw new Error('no price');
    sh.getRange(row, priceCol).setValue(q.price).setFontColor(COLORS.ink);
    if (q.chg === '' || q.chg == null || !isFinite(q.chg)) {
      sh.getRange(row, chgCol).setValue('');
    } else {
      sh.getRange(row, chgCol).setValue(q.chg)
        .setFontColor(q.chg >= 0 ? COLORS.good : COLORS.bad);
    }
    sh.getRange(tsRow, COL.updated).setValue(stamp).setFontColor(COLORS.muted).setFontSize(9);
  } catch (err) {
    sh.getRange(row, priceCol).setValue('err').setFontColor(COLORS.bad);
    sh.getRange(row, chgCol).setValue('');
  }
}

// ===========================================================================
// Price sources
// ===========================================================================

/** Coinbase last price + 24h change from the Exchange public stats endpoint. */
function coinbaseQuote(productId) {
  try {
    var res = UrlFetchApp.fetch('https://api.exchange.coinbase.com/products/' + productId + '/stats',
                                { muteHttpExceptions: true });
    var j = JSON.parse(res.getContentText());
    var last = Number(j.last), open = Number(j.open);
    if (isFinite(last) && last > 0) return { price: last, chg: open ? (last - open) / open : '' };
  } catch (e) {}
  // Fallback: plain spot price, no change.
  var r2 = UrlFetchApp.fetch('https://api.coinbase.com/v2/prices/' + productId + '/spot',
                             { muteHttpExceptions: true });
  return { price: Number(JSON.parse(r2.getContentText()).data.amount), chg: '' };
}

/** Webull crypto real-time quote (best-effort, multi-endpoint + retry). */
function webullCrypto(symbol) {
  try {
    return webullTry(cryptoUrls(webullId(symbol, 'crypto')));
  } catch (e) {
    // A cached/known id may be stale — force a fresh search once, then retry.
    PropertiesService.getScriptProperties().deleteProperty('WBID_' + symbol);
    return webullTry(cryptoUrls(webullResolve(symbol, 'crypto')));
  }
}

/** Webull Micro Gold Futures (best-effort; front-month id auto-resolved). */
function webullFutures() {
  var id = webullId('MGC', 'futures');
  return webullTry([
    'https://quotes-gw.webullfintech.com/api/quote/tickerRealTimes/full?ids=' + id,
    'https://quotes-gw.webullfintech.com/api/bgw/quote/realtime?ids=' + id,
  ]);
}

function cryptoUrls(id) {
  return [
    'https://quotes-gw.webullfintech.com/api/crypto/quote/tickerRealTimes?ids=' + id + '&more=1',
    'https://quotes-gw.webullfintech.com/api/bgw/quote/realtime?ids=' + id,
    'https://quotes-gw.webullfintech.com/api/quote/tickerRealTimes/full?ids=' + id,
  ];
}

/** Try each endpoint; return the first response that yields a real price. */
function webullTry(urls) {
  var lastErr = 'no data';
  for (var i = 0; i < urls.length; i++) {
    try {
      var res = UrlFetchApp.fetch(urls[i], { muteHttpExceptions: true, headers: webullHeaders() });
      if (res.getResponseCode() >= 400) { lastErr = 'http ' + res.getResponseCode(); continue; }
      var q = extractQuote(JSON.parse(res.getContentText()));
      if (q) return q;
      lastErr = 'no price field';
    } catch (e) { lastErr = String(e); }
  }
  throw new Error(lastErr);
}

/** Pull a price (+24h change) out of whatever shape Webull returned. */
function extractQuote(json) {
  var q = json;
  if (q && q.data != null) q = q.data;
  if (Array.isArray(q)) q = q[0];
  if (!q || typeof q !== 'object') return null;
  var priceKeys = ['close', 'price', 'pPrice', 'tradePrice', 'lastPrice', 'latestPrice', 'deal', 'last'];
  var chgKeys = ['changeRatio', 'pChRatio', 'chRatio', 'changeRatioValue'];
  var price = NaN;
  for (var i = 0; i < priceKeys.length; i++) {
    if (q[priceKeys[i]] != null) { var v = Number(q[priceKeys[i]]); if (isFinite(v) && v > 0) { price = v; break; } }
  }
  if (!isFinite(price)) return null;
  var chg = '';
  for (var j = 0; j < chgKeys.length; j++) {
    if (q[chgKeys[j]] != null) { var c = Number(q[chgKeys[j]]); if (isFinite(c)) { chg = c; break; } }
  }
  return { price: price, chg: chg };
}

/** Known/cached ticker id, else resolve via search. */
function webullId(symbol, kind) {
  if (WEBULL_KNOWN_IDS[symbol]) return WEBULL_KNOWN_IDS[symbol];
  var cached = PropertiesService.getScriptProperties().getProperty('WBID_' + symbol);
  return cached ? cached : webullResolve(symbol, kind);
}

/** Always hit Webull search to (re)resolve and cache a ticker id. */
function webullResolve(symbol, kind) {
  var url = 'https://quotes-gw.webullfintech.com/api/search/pc/tickers?keyword=' +
            encodeURIComponent(symbol.replace('USD', '')) +
            '&pageIndex=1&pageSize=20&regionId=6';
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: webullHeaders() });
  var list = (JSON.parse(res.getContentText()).data) || [];
  var want = (kind === 'crypto') ? 'crypto' : 'futures';
  var base = symbol.replace('USD', '');
  var hit = null;
  for (var i = 0; i < list.length; i++) {
    var t = list[i];
    var tmpl = (t.template || t.type || '').toLowerCase();
    var sym = (t.symbol || t.disSymbol || '').toUpperCase();
    if (tmpl.indexOf(want) >= 0 && (sym === symbol || sym.indexOf(base) >= 0)) { hit = t; break; }
  }
  if (!hit && list.length) hit = list[0];
  if (!hit || !hit.tickerId) throw new Error('id not found: ' + symbol);
  PropertiesService.getScriptProperties().setProperty('WBID_' + symbol, String(hit.tickerId));
  return hit.tickerId;
}

/** A stable device id keeps Webull's endpoint happier. */
function webullHeaders() {
  var props = PropertiesService.getScriptProperties();
  var did = props.getProperty('WB_DID');
  if (!did) { did = Utilities.getUuid().replace(/-/g, ''); props.setProperty('WB_DID', did); }
  return { 'did': did, 'App-Group': 'broker', 'Accept': 'application/json',
           'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json' };
}
