/**
 * ARB SCAN — cross-exchange crypto spread scanner
 * ===============================================
 * For each coin it pulls the live spot price from several exchanges, finds the
 * cheapest (BUY) and dearest (SELL), and computes the profit on a trade size
 * you set, AFTER estimated taker fees on both legs. Rows that clear your net
 * target (default $20) are highlighted green.
 *
 * SETUP (one time)
 *   1. Open the ARB SCAN sheet -> Extensions > Apps Script
 *   2. Delete any sample code, paste this whole file, click Save.
 *   3. Choose the function `setup` in the toolbar, click Run, approve the prompt.
 *   4. Done. It rebuilds the sheet and refreshes every 2 minutes.
 *
 * Change the trade size / net target in the yellow cells, then run `refreshArb`
 * (or just wait for the next auto-refresh).
 *
 * REALITY CHECK: a spread that survives fees almost always needs "pre-funded"
 * arbitrage — you already hold USD and the coin on BOTH exchanges and fire two
 * market orders at once (executes in seconds, no blockchain transfer). If you
 * instead move coins between exchanges, subtract the transfer time shown and a
 * network fee, and the price usually moves before you arrive. USDT-quoted
 * venues are treated as ~USD. Prices are last-trade, not order-book depth, so
 * real fills on size will differ. Not financial advice.
 */

// ---- Coins to scan ---------------------------------------------------------
var COINS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'LTC', 'BCH', 'AVAX', 'LINK', 'ADA'];

// ---- Exchanges: public ticker + estimated taker fee (edit to your tier) ----
var EXCHANGES = [
  { id: 'Coinbase',   fee: 0.0060, quote: 'USD',
    url: function (c) { return 'https://api.coinbase.com/v2/prices/' + c + '-USD/spot'; },
    parse: function (j) { return Number(j.data.amount); } },
  { id: 'Kraken',     fee: 0.0026, quote: 'USD',
    url: function (c) { return 'https://api.kraken.com/0/public/Ticker?pair=' + (c === 'BTC' ? 'XBT' : c) + 'USD'; },
    parse: function (j) { var r = j.result; for (var k in r) return Number(r[k].c[0]); return NaN; } },
  { id: 'Gemini',     fee: 0.0040, quote: 'USD',
    url: function (c) { return 'https://api.gemini.com/v1/pubticker/' + c.toLowerCase() + 'usd'; },
    parse: function (j) { return Number(j.last); } },
  { id: 'Bitstamp',   fee: 0.0030, quote: 'USD',
    url: function (c) { return 'https://www.bitstamp.net/api/v2/ticker/' + c.toLowerCase() + 'usd/'; },
    parse: function (j) { return Number(j.last); } },
  { id: 'Binance.US', fee: 0.0010, quote: 'USDT',
    url: function (c) { return 'https://api.binance.us/api/v3/ticker/price?symbol=' + c + 'USDT'; },
    parse: function (j) { return Number(j.price); } },
  { id: 'Bybit',      fee: 0.0010, quote: 'USDT',
    url: function (c) { return 'https://api.bybit.com/v5/market/tickers?category=spot&symbol=' + c + 'USDT'; },
    parse: function (j) { return Number(j.result.list[0].lastPrice); } },
  { id: 'OKX',        fee: 0.0010, quote: 'USDT',
    url: function (c) { return 'https://www.okx.com/api/v5/market/ticker?instId=' + c + '-USDT'; },
    parse: function (j) { return Number(j.data[0].last); } },
  { id: 'KuCoin',     fee: 0.0010, quote: 'USDT',
    url: function (c) { return 'https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=' + c + '-USDT'; },
    parse: function (j) { return Number(j.data.price); } },
];

// ---- Rough transfer time if you move coins between venues (minutes) --------
var TRANSFER_MIN = { BTC: 40, ETH: 5, SOL: 1, XRP: 1, DOGE: 12, LTC: 20, BCH: 20, AVAX: 2, LINK: 5, ADA: 5 };

// ---- Layout ----------------------------------------------------------------
var HDR = ['Coin', 'Buy @', 'Buy $', 'Sell @', 'Sell $', 'Spread %', 'Est. fees $', 'Net $', 'Meets target', 'Est. time'];
var ROW = { title: 1, size: 2, note: 3, colHdr: 4, dataStart: 5 };
var CLR = { head: '#111827', accent: '#1f6feb', band: '#f1f5f9', good: '#0e7c3a',
            bad: '#c0392b', muted: '#64748b', hit: '#dcfce7', input: '#fff7cc' };

// ===========================================================================
function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheets()[0];
  sh.setName('ARB SCAN');
  buildArb(sh);
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'refreshArb') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('refreshArb').timeBased().everyMinutes(2).create();
  refreshArb();
}

function removeTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'refreshArb') ScriptApp.deleteTrigger(t);
  });
}

function buildArb(sh) {
  sh.clear();
  if (sh.getMaxColumns() > 10) sh.deleteColumns(11, sh.getMaxColumns() - 10);

  sh.getRange(ROW.title, 1, 1, 10).merge()
    .setValue('ARB SCAN  —  cross-exchange crypto spread (net after fees)')
    .setFontSize(15).setFontWeight('bold').setFontColor('#ffffff')
    .setBackground(CLR.head).setVerticalAlignment('middle');
  sh.setRowHeight(ROW.title, 38);

  // Inputs
  sh.getRange(ROW.size, 1).setValue('Trade size (USD):').setFontWeight('bold');
  sh.getRange(ROW.size, 2).setValue(1000).setBackground(CLR.input).setNumberFormat('$#,##0');
  sh.getRange(ROW.size, 4).setValue('Net target (USD):').setFontWeight('bold');
  sh.getRange(ROW.size, 5).setValue(20).setBackground(CLR.input).setNumberFormat('$#,##0');

  sh.getRange(ROW.note, 1, 1, 10).merge()
    .setValue('Pre-funded (hold USD + coin on both venues) executes in ~seconds. Transfer time shown is blockchain only. USDT venues ≈ USD. Last-trade prices, not depth.')
    .setFontColor(CLR.muted).setFontSize(9);

  // Header row
  sh.getRange(ROW.colHdr, 1, 1, 10).setValues([HDR])
    .setFontWeight('bold').setBackground(CLR.accent).setFontColor('#ffffff');

  // Coin labels + banding
  for (var i = 0; i < COINS.length; i++) {
    var r = ROW.dataStart + i;
    sh.getRange(r, 1).setValue(COINS[i]).setFontWeight('bold');
    if (i % 2 === 1) sh.getRange(r, 1, 1, 10).setBackground(CLR.band);
  }

  // Number formats
  var n = COINS.length;
  sh.getRange(ROW.dataStart, 3, n, 1).setNumberFormat('$#,##0.00####'); // buy
  sh.getRange(ROW.dataStart, 5, n, 1).setNumberFormat('$#,##0.00####'); // sell
  sh.getRange(ROW.dataStart, 6, n, 1).setNumberFormat('0.00%');          // spread
  sh.getRange(ROW.dataStart, 7, n, 1).setNumberFormat('$#,##0.00');      // fees
  sh.getRange(ROW.dataStart, 8, n, 1).setNumberFormat('$#,##0.00');      // net

  // Fee-assumption + refresh footer
  var footRow = ROW.dataStart + n + 1;
  sh.getRange(footRow, 1, 1, 10).merge()
    .setValue('Taker-fee assumptions (edit in script): ' +
      EXCHANGES.map(function (e) { return e.id + ' ' + (e.fee * 100).toFixed(2) + '%'; }).join('  ·  '))
    .setFontColor(CLR.muted).setFontSize(9);
  sh.getRange(footRow + 1, 1).setValue('Last refresh:').setFontWeight('bold');

  sh.setColumnWidth(1, 70);
  sh.setColumnWidths(2, 4, 105);
  sh.setColumnWidth(6, 80); sh.setColumnWidth(7, 95); sh.setColumnWidth(8, 95);
  sh.setColumnWidth(9, 110); sh.setColumnWidth(10, 150);
  sh.setFrozenRows(ROW.colHdr);
}

// ===========================================================================
function refreshArb() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheets()[0];
  var notional = Number(sh.getRange(ROW.size, 2).getValue()) || 1000;
  var target = Number(sh.getRange(ROW.size, 5).getValue()) || 20;

  for (var i = 0; i < COINS.length; i++) {
    scanCoin(sh, ROW.dataStart + i, COINS[i], notional, target);
  }
  sh.getRange(ROW.dataStart + COINS.length + 2, 2)
    .setValue(Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), 'MMM d  HH:mm:ss'))
    .setFontColor(CLR.muted).setFontSize(9);
  SpreadsheetApp.flush();
}

function scanCoin(sh, row, coin, notional, target) {
  var quotes = [];
  for (var i = 0; i < EXCHANGES.length; i++) {
    var ex = EXCHANGES[i];
    try {
      var res = UrlFetchApp.fetch(ex.url(coin),
        { muteHttpExceptions: true, headers: { 'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0' } });
      if (res.getResponseCode() >= 400) continue;
      var p = ex.parse(JSON.parse(res.getContentText()));
      if (isFinite(p) && p > 0) quotes.push({ id: ex.id, price: p, fee: ex.fee });
    } catch (e) { /* skip this venue */ }
  }

  if (quotes.length < 2) {
    sh.getRange(row, 2, 1, 9).clearContent();
    sh.getRange(row, 2).setValue('not enough venues').setFontColor(CLR.muted);
    return;
  }

  var buy = quotes[0], sell = quotes[0];
  for (var j = 1; j < quotes.length; j++) {
    if (quotes[j].price < buy.price) buy = quotes[j];
    if (quotes[j].price > sell.price) sell = quotes[j];
  }

  var coinsBought = notional / buy.price;
  var proceeds = coinsBought * sell.price;
  var gross = proceeds - notional;
  var fees = notional * buy.fee + proceeds * sell.fee;
  var net = gross - fees;
  var spread = (sell.price - buy.price) / buy.price;
  var meets = net >= target;
  var xfer = TRANSFER_MIN[coin] != null ? TRANSFER_MIN[coin] : '?';

  sh.getRange(row, 2).setValue(buy.id);
  sh.getRange(row, 3).setValue(buy.price);
  sh.getRange(row, 4).setValue(sell.id);
  sh.getRange(row, 5).setValue(sell.price);
  sh.getRange(row, 6).setValue(spread);
  sh.getRange(row, 7).setValue(fees);
  sh.getRange(row, 8).setValue(net).setFontColor(net >= 0 ? CLR.good : CLR.bad);
  sh.getRange(row, 9).setValue(meets ? 'YES ✅' : 'no')
    .setFontColor(meets ? CLR.good : CLR.muted).setFontWeight(meets ? 'bold' : 'normal');
  sh.getRange(row, 10).setValue('~sec pre-funded · ~' + xfer + 'm transfer').setFontSize(9);

  var band = ((row - ROW.dataStart) % 2 === 1) ? CLR.band : '#ffffff';
  sh.getRange(row, 2, 1, 9).setBackground(meets ? CLR.hit : band);
}
