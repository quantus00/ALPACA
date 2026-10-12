"""Download BTC/USD history (Bitstamp 1-minute, 2012 -> yesterday) and build
1h / 4h / 1d / 1w bars for the reversal backtest.

Source: github.com/ff137/bitstamp-btcusd-minute-data (bulk file + daily updates).
~145 MB download. Output: backtest/data/reversal/BTCUSD_<tf>.csv

    python3 backtest/fetch_btc_history.py [--since 2017-01-01] [--out DIR]
"""
from __future__ import annotations

import argparse
import io
import urllib.request
from pathlib import Path

import pandas as pd

BASE = "https://raw.githubusercontent.com/ff137/bitstamp-btcusd-minute-data/main/data"
FILES = ("historical/btcusd_bitstamp_1min_2012-2025.csv.gz", "updates/btcusd_bitstamp_1min_latest.csv")
RULES = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D", "1w": "W-MON"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2017-01-01")
    ap.add_argument("--out", default=str(Path(__file__).parent / "data" / "reversal"))
    a = ap.parse_args()
    since = int(pd.Timestamp(a.since, tz="UTC").timestamp())
    parts = []
    for f in FILES:
        print("downloading", f)
        raw = urllib.request.urlopen(f"{BASE}/{f}", timeout=600).read()
        d = pd.read_csv(io.BytesIO(raw), compression="gzip" if f.endswith(".gz") else None)
        parts.append(d[d.timestamp >= since])
    d = pd.concat(parts).drop_duplicates("timestamp").sort_values("timestamp")
    d.index = pd.to_datetime(d.timestamp, unit="s", utc=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    for tf, rule in RULES.items():
        b = d[list(agg)].resample(rule, label="left", closed="left").agg(agg).dropna()
        b.to_csv(out / f"BTCUSD_{tf}.csv", index_label="time")
        print(f"{tf}: {len(b)} bars {b.index[0]} -> {b.index[-1]}")


if __name__ == "__main__":
    main()
