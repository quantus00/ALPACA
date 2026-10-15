"""Fetch 12 months of 1h bars for the hedge backtest (stdlib only).

    python3 backtest/fetch_hedge_data.py            # last 365 days
    python3 backtest/fetch_hedge_data.py --days 400

Writes backtest/data/hedge/:
    BTCUSD_1h.csv       Coinbase Exchange public candles (BTC-USD spot)
    EURUSD_1h.csv       Yahoo Finance chart API (EURUSD=X, 60m)
    USDCHF_1h.csv       Yahoo Finance chart API (USDCHF=X, 60m)
    BTC_PERP_funding.csv  Coinbase International BTC-PERP funding history (best effort;
                          the backtest falls back to default_funding_rate_pct if absent)

CSV columns: time (unix seconds, bar open, UTC), open, high, low, close, volume.
You can also drop in your own CSVs (e.g. a TradingView export) with the same
names; the loader accepts unix-second or ISO-8601 times.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "hedge")
UA = {"User-Agent": "Mozilla/5.0 (hedge-backtest)"}


def _get(url: str, params: dict | None = None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _write(name: str, rows: list[tuple]) -> None:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    print(f"wrote {len(rows):>6} rows -> {path}")


def fetch_btc(days: int) -> None:
    end = int(time.time()) // 3600 * 3600
    start = end - days * 86400
    rows: dict[int, tuple] = {}
    t = start
    while t < end:
        t2 = min(t + 300 * 3600, end)
        data = _get("https://api.exchange.coinbase.com/products/BTC-USD/candles", {
            "granularity": 3600,
            "start": dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(),
            "end": dt.datetime.fromtimestamp(t2, dt.timezone.utc).isoformat()})
        for r in data:   # [time, low, high, open, close, volume]
            if start <= r[0] < end:
                rows[int(r[0])] = (int(r[0]), r[3], r[2], r[1], r[4], r[5])
        t = t2
        time.sleep(0.35)
    _write("BTCUSD_1h.csv", [rows[k] for k in sorted(rows)])


def fetch_yahoo(ticker: str, name: str, days: int) -> None:
    end = int(time.time())
    data = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}", {
        "interval": "60m", "period1": end - days * 86400, "period2": end})
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(res["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        rows.append((int(t) // 3600 * 3600, o, h, l, c, q.get("volume", [0] * len(res["timestamp"]))[i] or 0))
    _write(name, rows)


def fetch_funding(days: int) -> None:
    """Coinbase International public funding history for BTC-PERP (hourly)."""
    since = time.time() - days * 86400
    out: dict[int, float] = {}
    offset = 0
    try:
        while True:
            data = _get("https://api.international.coinbase.com/api/v1/instruments/BTC-PERP/funding",
                        {"result_limit": 100, "result_offset": offset})
            results = data.get("results", data if isinstance(data, list) else [])
            if not results:
                break
            oldest = None
            for r in results:
                ts = dt.datetime.fromisoformat(r["event_time"].replace("Z", "+00:00")).timestamp()
                out[int(ts) // 3600 * 3600] = float(r["funding_rate"]) * 100.0   # -> percent
                oldest = ts if oldest is None else min(oldest, ts)
            if oldest is not None and oldest < since:
                break
            offset += len(results)
            time.sleep(0.25)
    except Exception as exc:  # noqa: BLE001
        print(f"funding history unavailable ({exc}); backtest will use default_funding_rate_pct")
        return
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "BTC_PERP_funding.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "rate_pct"])
        for k in sorted(out):
            if k >= since:
                w.writerow([k, out[k]])
    print(f"wrote funding -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args()
    fetch_btc(a.days)
    fetch_yahoo("EURUSD=X", "EURUSD_1h.csv", a.days)
    fetch_yahoo("USDCHF=X", "USDCHF_1h.csv", a.days)
    fetch_funding(a.days)


if __name__ == "__main__":
    main()
