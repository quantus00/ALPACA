/**
 * STREAM — Live prices: BTC (spot + perp), Gold (MGC + 1 oz), S&P (MES)
 * ====================================================================
 * One script builds the formatted sheet and refreshes it every minute.
 *
 * Instruments:
 *   • BTC Spot   — Coinbase (official) and Webull (unofficial, best-effort)
 *   • BTC Perp   — perpetual future, public API (OKX -> Bybit -> Binance)
 *   • MGC        — Micro Gold future (10 oz), priced per oz via Stooq
 *   • Gold 1 oz  — same per-oz gold price, 1-ounce notional
 *   • MES        — Micro E-mini S&P 500 future, S&P index via Stooq
 *
 * SETUP (one time)
 *   1. Open the STREAM sheet -> Extensions > Apps Script
 *   2. Delete any sample code, paste this whole file, click Save.
 *   3. Choose `setup` in the toolbar, click Run, approve the prompt.
 *
 * PAUSE / RESUME
 *   Run `pause`  to stop auto-refresh (prices freeze).
 *   Run `resume` to start it again (and refresh now).
 *   `refreshPrices` does a single manual update.
 *
 * Coinbase is reliable; Stooq is reliable but ~15-60 min delayed; Webull is
 * best-effort and may show "err". Not financial advice.
 */

// ---- Instruments (edit here to add/remove rows) ----------------------------
// kind: cbSpot | wbCrypto | perp | stooq ; mult = contract multiplier for the
// "Contract value" column (per-oz x oz, or index x $/point).
var INSTRUMENTS = [
  { label: 'BTC Spot — Coinbase',        kind: 'cbSpot',   sym: 'BTC-USD', mult: 1,  src: 'Coinbase' },
  { label: 'BTC Spot — Webull',          kind: 'wbCrypto', sym: 'BTCUSD',  mult: 1,  src: 'Webull (best-effort)' },
  { label: 'BTC Perp',                   kind: 'perp',     sym: 'BTC',     mult: 1,  src: 'OKX/Bybit/Binance' },
  { label: 'Micro Gold — MGC (per oz)',  kind: 'stooq',    sym: 'gc.f',    mult: 10, src: 'Stooq (COMEX)' },
  { label: 'Gold — 1 troy oz',           kind: 'stooq',    sym: 'gc.f',    mult: 1,  src: 'Stooq (COMEX)' },
  { label: 'Micro E-mini S&P — MES',     kind: 'stooq',    sym: 'es.f',    mult: 5,  src: 'Stooq (CME)' },
];

var COL = { label: 1, price: 2, chg: 3, contract: 4, src: 5, updated: 6 };
var ROW = { title: 1, sub: 2, hdr: 4, dataStart: 5 };
var CLR = { head: '#111827', accent: '#1f6feb', band: '#f1f5f9',
            good: '#0e7c3a', bad: '#c0392b', muted: '#64748b', ink: '#0b1220' };

// ===========================================================================
function setup() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
  sh.setName('STREAM');
  buildLayout(sh);
  resume();                 // installs the trigger + first refresh
}

function pause() {
  killTriggers();
  toast_('Auto-refresh paused. Run "resume" to restart.');
}

function resume() {
  killTriggers();
  ScriptApp.newTrigger('refreshPrices').timeBased().everyMinutes(1).create();
  refreshPrices();
  toast_('Auto-refresh on (every minute).');
}

function installTrigger() { resume(); }   // backward-compatible alias
function removeTrigger() { pause(); }     // backward-compatible alias

function killTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'refreshPrices') ScriptApp.deleteTrigger(t);
  });
}

function toast_(msg) {
  try { SpreadsheetApp.getActiveSpreadsheet().toast(msg, 'STREAM', 5); } catch (e) {}
}

// ===========================================================================
function refreshPrices() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheets()[0];
  var stamp = Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), 'MMM d  HH:mm:ss');

  for (var i = 0; i < INSTRUMENTS.length; i++) {
    writeRow(sh, ROW.dataStart + i, INSTRUMENTS[i], stamp);
  }
  sh.getRange(ROW.dataStart + INSTRUMENTS.length + 1, COL.price)
    .setValue(stamp).setFontColor(CLR.muted).setFontSize(9);
  SpreadsheetApp.flush();
}

function writeRow(sh, row, inst, stamp) {
  try {
    var q = fetchQuote(inst);                 // { price, chg }
    if (!q || !isFinite(q.price) || q.price <= 0) throw new Error('no price');
    sh.getRange(row, COL.price).setValue(q.price).setFontColor(CLR.ink);
    if (q.chg === '' || q.chg == null || !isFinite(q.chg)) {
      sh.getRange(row, COL.chg).setValue('');
    } else {
      sh.getRange(row, COL.chg).setValue(q.chg).setFontColor(q.chg >= 0 ? CLR.good : CLR.bad);
    }
    sh.getRange(row, COL.contract).setValue(q.price * (inst.mult || 1));
    sh.getRange(row, COL.updated).setValue(stamp).setFontColor(CLR.muted).setFontSize(9);
  } catch (err) {
    sh.getRange(row, COL.price).setValue('err').setFontColor(CLR.bad);
    sh.getRange(row, COL.chg).setValue('');
    sh.getRange(row, COL.contract).setValue('');
    sh.getRange(row, COL.updated).setValue(String(err).slice(0, 60)).setFontColor(CLR.muted).setFontSize(9);
  }
}

function fetchQuote(inst) {
  switch (inst.kind) {
    case 'cbSpot':   return coinbaseQuote(inst.sym);
    case 'wbCrypto': return { price: webullCryptoPrice(inst.sym), chg: '' };
    case 'perp':     return perpQuote(inst.sym);
    case 'stooq':    return stooqQuote(inst.sym);
  }
  throw new Error('unknown kind');
}

// ===========================================================================
// Layout
// ===========================================================================
function buildLayout(sh) {
  sh.clear();
  if (sh.getMaxColumns() > 6) sh.deleteColumns(7, sh.getMaxColumns() - 6);

  sh.getRange(ROW.title, 1, 1, 6).merge()
    .setValue('STREAM  —  BTC · Gold · S&P  (live)')
    .setFontSize(16).setFontWeight('bold').setFontColor('#ffffff')
    .setBackground(CLR.head).setVerticalAlignment('middle');
  sh.setRowHeight(ROW.title, 40);
  sh.getRange(ROW.sub, 1, 1, 6).merge()
    .setValue('BTC spot (Coinbase + Webull) & perp, Micro Gold MGC + 1 oz, Micro E-mini S&P MES. Auto-refresh every minute — run "pause" to stop.')
    .setFontColor(CLR.muted).setFontSize(10);

  var heads = ['Instrument', 'Price (USD)', '24h %', 'Contract value', 'Source', 'Updated'];
  sh.getRange(ROW.hdr, 1, 1, 6).setValues([heads])
    .setFontWeight('bold').setBackground(CLR.accent).setFontColor('#ffffff');

  for (var i = 0; i < INSTRUMENTS.length; i++) {
    var r = ROW.dataStart + i;
    sh.getRange(r, COL.label).setValue(INSTRUMENTS[i].label).setFontWeight('bold');
    sh.getRange(r, COL.src).setValue(INSTRUMENTS[i].src).setFontColor(CLR.muted).setFontSize(9);
    if (i % 2 === 1) sh.getRange(r, 1, 1, 6).setBackground(CLR.band);
  }

  var n = INSTRUMENTS.length;
  sh.getRange(ROW.dataStart, COL.price, n, 1).setNumberFormat('$#,##0.00####');
  sh.getRange(ROW.dataStart, COL.chg, n, 1).setNumberFormat('0.00%;-0.00%');
  sh.getRange(ROW.dataStart, COL.contract, n, 1).setNumberFormat('$#,##0.00');

  var lastRow = ROW.dataStart + n + 1;
  sh.getRange(lastRow, COL.label).setValue('Last refresh:').setFontWeight('bold');
  sh.getRange(lastRow + 2, 1, 1, 6).merge()
    .setValue('MGC & "1 oz" share the same per-oz gold price; only the contract value differs (MGC = price×10 oz, 1 oz = price×1). MES contract value = index × $5. Not financial advice.')
    .setFontColor(CLR.muted).setFontSize(9);

  sh.setColumnWidth(COL.label, 220);
  sh.setColumnWidth(COL.price, 130);
  sh.setColumnWidth(COL.chg, 80);
  sh.setColumnWidth(COL.contract, 130);
  sh.setColumnWidth(COL.src, 160);
  sh.setColumnWidth(COL.updated, 130);
  sh.setFrozenRows(ROW.hdr);
}

// ===========================================================================
// Price sources
// ===========================================================================

/** Coinbase last price + 24h change (Exchange stats, spot fallback). */
function coinbaseQuote(productId) {
  try {
    var res = UrlFetchApp.fetch('https://api.exchange.coinbase.com/products/' + productId + '/stats',
      { muteHttpExceptions: true });
    var j = JSON.parse(res.getContentText());
    var last = Number(j.last), open = Number(j.open);
    if (isFinite(last) && last > 0) return { price: last, chg: open ? (last - open) / open : '' };
  } catch (e) {}
  var r2 = UrlFetchApp.fetch('https://api.coinbase.com/v2/prices/' + productId + '/spot',
    { muteHttpExceptions: true });
  return { price: Number(JSON.parse(r2.getContentText()).data.amount), chg: '' };
}

/** Perpetual-future last price + 24h change from public venues. */
function perpQuote(coin) {
  var sources = [
    { url: 'https://www.okx.com/api/v5/market/ticker?instId=' + coin + '-USDT-SWAP',
      parse: function (j) { var d = j.data && j.data[0]; if (!d) return null;
        var last = Number(d.last), o = Number(d.open24h); return { price: last, chg: o ? (last - o) / o : '' }; } },
    { url: 'https://api.bybit.com/v5/market/tickers?category=linear&symbol=' + coin + 'USDT',
      parse: function (j) { var d = j.result && j.result.list && j.result.list[0]; if (!d) return null;
        return { price: Number(d.lastPrice), chg: Number(d.price24hPcnt) }; } },
    { url: 'https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=' + coin + 'USDT',
      parse: function (j) { return { price: Number(j.lastPrice), chg: Number(j.priceChangePercent) / 100 }; } },
  ];
  for (var i = 0; i < sources.length; i++) {
    try {
      var res = UrlFetchApp.fetch(sources[i].url,
        { muteHttpExceptions: true, headers: { 'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0' } });
      if (res.getResponseCode() >= 400) continue;
      var q = sources[i].parse(JSON.parse(res.getContentText()));
      if (q && isFinite(q.price) && q.price > 0) return q;
    } catch (e) {}
  }
  throw new Error('no perp feed');
}

/** Stooq CSV: last close + intraday change. sym e.g. 'gc.f', 'es.f'. */
function stooqQuote(sym) {
  var res = UrlFetchApp.fetch('https://stooq.com/q/l/?s=' + sym + '&f=sd2t2ohlc&h&e=csv',
    { muteHttpExceptions: true });
  var lines = res.getContentText().trim().split('\n');
  if (lines.length < 2) throw new Error('stooq empty');
  var c = lines[1].split(',');            // Symbol,Date,Time,Open,High,Low,Close
  var open = Number(c[3]), close = Number(c[6]);
  if (!isFinite(close) || close <= 0) throw new Error('stooq n/d');
  return { price: close, chg: open ? (close - open) / open : '' };
}

// ===========================================================================
// Webull crypto (unofficial, best-effort)
// ===========================================================================
var WEBULL_KNOWN_IDS = { BTCUSD: 950160802 };

function webullCryptoPrice(symbol) {
  try {
    return webullTry(webullCryptoUrls(webullId(symbol)));
  } catch (e) {
    PropertiesService.getScriptProperties().deleteProperty('WBID_' + symbol);
    return webullTry(webullCryptoUrls(webullResolve(symbol)));
  }
}

function webullCryptoUrls(id) {
  return [
    'https://quotes-gw.webullfintech.com/api/crypto/quote/tickerRealTimes?ids=' + id + '&more=1',
    'https://quotes-gw.webullfintech.com/api/bgw/quote/realtime?ids=' + id,
    'https://quotes-gw.webullfintech.com/api/quote/tickerRealTimes/full?ids=' + id,
  ];
}

function webullTry(urls) {
  var last = 'no data';
  for (var i = 0; i < urls.length; i++) {
    try {
      var res = UrlFetchApp.fetch(urls[i], { muteHttpExceptions: true, headers: webullHeaders() });
      if (res.getResponseCode() >= 400) { last = 'http ' + res.getResponseCode(); continue; }
      var p = webullExtractPrice(JSON.parse(res.getContentText()));
      if (isFinite(p) && p > 0) return p;
      last = 'no price field';
    } catch (e) { last = String(e); }
  }
  throw new Error(last);
}

function webullExtractPrice(json) {
  var q = json;
  if (q && q.data != null) q = q.data;
  if (Array.isArray(q)) q = q[0];
  if (!q || typeof q !== 'object') return NaN;
  var keys = ['close', 'price', 'pPrice', 'tradePrice', 'lastPrice', 'latestPrice', 'deal', 'last'];
  for (var i = 0; i < keys.length; i++) {
    if (q[keys[i]] != null) { var v = Number(q[keys[i]]); if (isFinite(v) && v > 0) return v; }
  }
  return NaN;
}

function webullId(symbol) {
  if (WEBULL_KNOWN_IDS[symbol]) return WEBULL_KNOWN_IDS[symbol];
  var cached = PropertiesService.getScriptProperties().getProperty('WBID_' + symbol);
  return cached ? cached : webullResolve(symbol);
}

function webullResolve(symbol) {
  var base = symbol.replace('USD', '');
  var url = 'https://quotes-gw.webullfintech.com/api/search/pc/tickers?keyword=' +
            encodeURIComponent(base) + '&pageIndex=1&pageSize=20&regionId=6';
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: webullHeaders() });
  var list = (JSON.parse(res.getContentText()).data) || [];
  var hit = null;
  for (var i = 0; i < list.length; i++) {
    var t = list[i];
    var tmpl = (t.template || t.type || '').toLowerCase();
    var sym = (t.symbol || t.disSymbol || '').toUpperCase();
    if (tmpl.indexOf('crypto') >= 0 && (sym === symbol || sym.indexOf(base) >= 0)) { hit = t; break; }
  }
  if (!hit && list.length) hit = list[0];
  if (!hit || !hit.tickerId) throw new Error('id not found: ' + symbol);
  PropertiesService.getScriptProperties().setProperty('WBID_' + symbol, String(hit.tickerId));
  return hit.tickerId;
}

function webullHeaders() {
  var props = PropertiesService.getScriptProperties();
  var did = props.getProperty('WB_DID');
  if (!did) { did = Utilities.getUuid().replace(/-/g, ''); props.setProperty('WB_DID', did); }
  return { 'did': did, 'App-Group': 'broker', 'Accept': 'application/json',
           'User-Agent': 'Mozilla/5.0', 'Content-Type': 'application/json' };
}
