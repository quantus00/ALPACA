"""Download BTC perpetual funding-rate history for the reversal funding filter.

Run this on your server (this needs normal internet access). It tries each
source in order and keeps the first that works:
  1. Binance USDT-M  BTCUSDT   (from Sep 2019, every 8h)
  2. Bybit linear    BTCUSDT   (from 2020, every 8h)
  3. Deribit         BTC-PERPETUAL  (from 2019; 8h-equivalent rate, hourly)
Binance and Bybit block some regions (including US IPs); Deribit is the fallback.

Output: backtest/data/reversal/BTC_funding.csv  (time UTC, funding = rate per 8h)

    python3 backtest/fetch_funding.py [--since 2019-01-01] [--source binance|bybit|deribit]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent / "data" / "reversal" / "BTC_funding.csv"
DAY_MS = 86_400_000


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "alpaca-reversal/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def parse_binance(rows) -> list[tuple[int, float]]:
    return [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]


def parse_bybit(payload) -> list[tuple[int, float]]:
    return [(int(r["fundingRateTimestamp"]), float(r["fundingRate"])) for r in payload["result"]["list"]]


def parse_deribit(payload) -> list[tuple[int, float]]:
    return [(int(r["timestamp"]), float(r["interest_8h"])) for r in payload["result"]]


def binance(start_ms: int) -> list[tuple[int, float]]:
    out, t = [], start_ms
    while True:
        rows = parse_binance(_get("https://fapi.binance.com/fapi/v1/fundingRate"
                                  f"?symbol=BTCUSDT&startTime={t}&limit=1000"))
        if not rows:
            return out
        out += rows
        if len(rows) < 1000:
            return out
        t = rows[-1][0] + 1
        time.sleep(0.3)


def bybit(start_ms: int) -> list[tuple[int, float]]:
    out, end = [], int(time.time() * 1000)
    while end > start_ms:
        rows = parse_bybit(_get("https://api.bybit.com/v5/market/funding/history"
                                f"?category=linear&symbol=BTCUSDT&endTime={end}&limit=200"))
        if not rows:
            break
        out += rows
        oldest = min(r[0] for r in rows)
        if oldest >= end:
            break
        end = oldest - 1
        time.sleep(0.3)
    return [r for r in out if r[0] >= start_ms]


def deribit(start_ms: int) -> list[tuple[int, float]]:
    out, t, now = [], start_ms, int(time.time() * 1000)
    while t < now:
        e = min(t + 30 * DAY_MS, now)
        out += parse_deribit(_get("https://www.deribit.com/api/v2/public/get_funding_rate_history"
                                  f"?instrument_name=BTC-PERPETUAL&start_timestamp={t}&end_timestamp={e}"))
        t = e + 1
        time.sleep(0.3)
    return out


SOURCES = {"binance": binance, "bybit": bybit, "deribit": deribit}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2019-01-01")
    ap.add_argument("--source", choices=list(SOURCES), default=None, help="force one source")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    start = int(pd.Timestamp(a.since, tz="UTC").timestamp() * 1000)
    for name in ([a.source] if a.source else list(SOURCES)):
        try:
            print(f"trying {name} ...")
            rows = SOURCES[name](start)
        except Exception as e:                          # region block, outage, etc.
            print(f"  {name} failed: {e}")
            continue
        if len(rows) < 100:
            print(f"  {name} returned only {len(rows)} rows, trying next source")
            continue
        df = pd.DataFrame(rows, columns=["ms", "funding"]).drop_duplicates("ms").sort_values("ms")
        df["time"] = pd.to_datetime(df.ms, unit="ms", utc=True)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        df[["time", "funding"]].to_csv(a.out, index=False)
        print(f"  saved {len(df)} rows from {name}: {df.time.iloc[0]} -> {df.time.iloc[-1]}  ({a.out})")
        return
    raise SystemExit("No funding source worked from this machine.")


if __name__ == "__main__":
    main()
