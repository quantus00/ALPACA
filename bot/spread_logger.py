"""Cross-venue spread logger: Coinbase vs. Webull.

Polls BTC-USD and DOGE-USD on both venues every 60s and appends one row per
poll to ``gaps_log.csv`` so a cross-venue spread strategy can be evaluated on
real data *before* any capital is put at risk.

    python -m bot.spread_logger            # run the polling loop
    python -m bot.spread_logger --once     # single poll then exit (handy for cron/tests)

LOGGING ONLY — this module never places a trade.

Why "raw gap" is not profit
---------------------------
A naive strategy would look at the price difference between two venues and call
it edge. It is not:

* Webull's quoted crypto price already bakes in a ~1% embedded spread *per side*
  (you buy above and sell below the true mid).
* Coinbase Advanced entry-tier fees are ~0.6-1.2% *per side*.

A round trip (enter on one venue, exit on the other) therefore eats roughly 3%
in embedded spread + fees before you keep a cent. So every row logs both:

* ``raw_gap_pct``            -- the observed price gap, |A - B| / mid * 100
* ``net_gap_after_costs_pct``-- raw_gap_pct minus ``ROUND_TRIP_COST_PCT`` (~3%)

Only a *net* gap above 0 is potentially tradeable.

Webull has NO official public crypto price API. If the community ``webull``
package and credentials are available we use them; otherwise the Webull fetch is
a clearly-marked stub and the script keeps running on Coinbase alone. The banner
and per-cycle status line always tell you which venues are live.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger("spread_logger")

# --- Tunables ---------------------------------------------------------------
ASSETS = ("BTC-USD", "DOGE-USD")
POLL_SECONDS = 60
CSV_PATH = "gaps_log.csv"
CSV_COLUMNS = [
    "timestamp",
    "asset",
    "coinbase_price",
    "webull_price",
    "raw_gap_pct",
    "net_gap_after_costs_pct",
]

# Total round-trip cost estimate that a raw gap must clear to be real edge:
#   Webull embedded spread ~1%/side (x2) + Coinbase entry-tier fees ~0.6-1.2%/side.
# ~3% total is a deliberately conservative round number; tune to your fee tier.
ROUND_TRIP_COST_PCT = 3.0

COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/{product}/ticker"
HTTP_TIMEOUT = 15
USER_AGENT = "alpaca-spread-logger/1.0"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _log_err(msg: str, exc: Optional[BaseException] = None) -> None:
    """Console error with a timestamp; never raises."""
    if exc is not None:
        log.error("[%s] %s: %s", _now_iso(), msg, exc)
    else:
        log.error("[%s] %s", _now_iso(), msg)


# --- Coinbase (public, no auth) ---------------------------------------------
def fetch_coinbase_price(asset: str) -> Optional[float]:
    """Return the last trade price for ``asset`` from Coinbase's public REST
    API, or ``None`` on any failure (never raises)."""
    try:
        url = COINBASE_TICKER_URL.format(product=asset)
        resp = requests.get(url, timeout=HTTP_TIMEOUT,
                            headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        data = resp.json()
        price = data.get("price")
        if price is None:
            _log_err(f"Coinbase {asset}: no 'price' field in response")
            return None
        return float(price)
    except Exception as exc:  # noqa: BLE001
        _log_err(f"Coinbase {asset} fetch failed", exc)
        return None


# --- Webull (unofficial, login-based) ---------------------------------------
class WebullPriceSource:
    """Wraps the community ``webull`` package for crypto quotes.

    Webull publishes no official public crypto price API, so this depends on the
    unofficial ``webull`` package and login credentials (WEBULL_EMAIL,
    WEBULL_PASSWORD, WEBULL_TRADE_PIN, optional WEBULL_DEVICE_ID). When either
    the package or the credentials are missing, ``available`` is False and every
    ``fetch`` returns None -- the caller then logs a stub/NaN row and carries on
    with Coinbase alone.
    """

    # Webull crypto ticker symbols differ from Coinbase product ids.
    SYMBOL_MAP = {"BTC-USD": "BTCUSD", "DOGE-USD": "DOGEUSD"}

    def __init__(self) -> None:
        self.available = False
        self._wb = None
        self._reason = "not initialised"
        self._try_login()

    def _have_credentials(self) -> bool:
        return all(os.getenv(k) for k in
                   ("WEBULL_EMAIL", "WEBULL_PASSWORD", "WEBULL_TRADE_PIN"))

    def _try_login(self) -> None:
        if not self._have_credentials():
            self._reason = ("Webull credentials not set "
                            "(WEBULL_EMAIL / WEBULL_PASSWORD / WEBULL_TRADE_PIN)")
            return
        try:
            from webull import webull  # type: ignore
        except Exception as exc:  # noqa: BLE001
            self._reason = f"'webull' package not importable ({exc})"
            return
        try:
            wb = webull()
            if os.getenv("WEBULL_DEVICE_ID"):
                wb._did = os.environ["WEBULL_DEVICE_ID"]
            wb.login(os.environ["WEBULL_EMAIL"], os.environ["WEBULL_PASSWORD"])
            wb.get_trade_token(os.environ["WEBULL_TRADE_PIN"])
            self._wb = wb
            self.available = True
            self._reason = "logged in"
        except Exception as exc:  # noqa: BLE001
            self._reason = f"Webull login failed ({exc})"

    @property
    def status(self) -> str:
        return self._reason

    def fetch(self, asset: str) -> Optional[float]:
        """Return Webull's last price for ``asset``, or None on failure/stub."""
        if not self.available:
            # ----------------------------------------------------------------
            # TODO(webull): No official public Webull crypto price API exists.
            # When credentials + the community `webull` package are available
            # this branch is skipped and the real quote below is used. Until
            # then this is a deliberate stub: it returns None so the row logs a
            # blank Webull price and the loop keeps running on Coinbase alone.
            # ----------------------------------------------------------------
            return None
        try:
            symbol = self.SYMBOL_MAP.get(asset, asset.replace("-", ""))
            quote = self._wb.get_quote(stock=symbol)
            # The community API returns strings; field name varies by build.
            raw = None
            if isinstance(quote, dict):
                raw = (quote.get("close") or quote.get("price")
                       or quote.get("pPrice") or quote.get("last"))
            if raw is None:
                _log_err(f"Webull {asset}: no price field in quote {quote!r}")
                return None
            return float(raw)
        except Exception as exc:  # noqa: BLE001
            _log_err(f"Webull {asset} fetch failed", exc)
            return None


# --- Gap math ---------------------------------------------------------------
def compute_gaps(coinbase_price: Optional[float],
                 webull_price: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Return (raw_gap_pct, net_gap_after_costs_pct).

    ``raw_gap_pct`` is the absolute price gap as a percentage of the mid price
    (symmetric, direction-agnostic -- a spread trade takes whichever venue is
    cheaper). ``net`` subtracts the estimated round-trip cost. Both are None
    when either price is missing, since a gap needs both venues.
    """
    if coinbase_price is None or webull_price is None:
        return None, None
    mid = (coinbase_price + webull_price) / 2.0
    if mid <= 0:
        return None, None
    raw = abs(coinbase_price - webull_price) / mid * 100.0
    net = raw - ROUND_TRIP_COST_PCT
    return raw, net


# --- CSV --------------------------------------------------------------------
def ensure_csv_header(path: str) -> None:
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    if not exists:
        with open(path, "a", newline="") as fh:
            csv.writer(fh).writerow(CSV_COLUMNS)


def _fmt(value: Optional[float], places: int) -> str:
    return "" if value is None else f"{value:.{places}f}"


def append_row(path: str, timestamp: str, asset: str,
               coinbase_price: Optional[float], webull_price: Optional[float],
               raw_gap_pct: Optional[float], net_gap_pct: Optional[float]) -> None:
    row = [
        timestamp,
        asset,
        _fmt(coinbase_price, 8),
        _fmt(webull_price, 8),
        _fmt(raw_gap_pct, 4),
        _fmt(net_gap_pct, 4),
    ]
    try:
        with open(path, "a", newline="") as fh:
            csv.writer(fh).writerow(row)
    except Exception as exc:  # noqa: BLE001
        _log_err(f"CSV append failed for {asset}", exc)


# --- Poll cycle -------------------------------------------------------------
def poll_once(webull: WebullPriceSource, csv_path: str) -> None:
    """Run one poll of every asset on both venues and append rows. Wrapped so a
    single failure never crashes the loop."""
    timestamp = _now_iso()
    parts = []
    for asset in ASSETS:
        cb = fetch_coinbase_price(asset)
        wb = webull.fetch(asset)
        raw, net = compute_gaps(cb, wb)
        append_row(csv_path, timestamp, asset, cb, wb, raw, net)

        if raw is None:
            # Which side is missing tells us why there is no gap.
            if cb is None:
                parts.append(f"{asset}: coinbase=ERR")
            else:
                parts.append(f"{asset}: cb={cb:g} wb=n/a")
        else:
            flag = "TRADEABLE" if net > 0 else "no-edge"
            parts.append(f"{asset}: cb={cb:g} wb={wb:g} "
                         f"raw={raw:.3f}% net={net:.3f}% [{flag}]")
    log.info("[%s] %s", timestamp, " | ".join(parts))


def run(csv_path: str = CSV_PATH, poll_seconds: int = POLL_SECONDS,
        once: bool = False) -> None:
    ensure_csv_header(csv_path)
    webull = WebullPriceSource()

    live = "Coinbase=LIVE, Webull=" + ("LIVE" if webull.available else "STUB")
    log.info("=" * 72)
    log.info("Cross-venue spread logger  (LOGGING ONLY -- no trades placed)")
    log.info("Venues:   %s", live)
    if not webull.available:
        log.info("Webull:   %s -> logging blank Webull price; gaps need both venues",
                 webull.status)
    log.info("Assets:   %s", ", ".join(ASSETS))
    log.info("Costs:    subtracting %.1f%% round-trip; only net > 0 is tradeable",
             ROUND_TRIP_COST_PCT)
    log.info("Output:   %s  (every %ds)", csv_path, poll_seconds)
    log.info("=" * 72)

    if once:
        poll_once(webull, csv_path)
        return

    while True:
        cycle_start = time.monotonic()
        try:
            poll_once(webull, csv_path)
        except Exception as exc:  # noqa: BLE001 -- belt-and-suspenders; never crash
            _log_err("Unexpected error in poll cycle", exc)
        # Sleep the remainder of the interval so cadence stays ~poll_seconds.
        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, poll_seconds - elapsed)
        try:
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            log.info("Interrupted -- stopping. CSV saved at %s", csv_path)
            return


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--once", action="store_true",
                        help="run a single poll and exit (no loop)")
    parser.add_argument("--csv", default=CSV_PATH, help="output CSV path")
    parser.add_argument("--interval", type=int, default=POLL_SECONDS,
                        help="seconds between polls (default 60)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        run(csv_path=args.csv, poll_seconds=args.interval, once=args.once)
    except KeyboardInterrupt:
        log.info("Interrupted -- stopping.")


if __name__ == "__main__":
    main()
