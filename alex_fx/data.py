"""Candle data for the forex bot.

Backtesting: load historical OHLC from a CSV (columns: time,open,high,low,close
[,volume]; time is a unix seconds int or ISO-8601 string). Get free FX history
from Dukascopy, HistData.com, Twelve Data, or FOREX.com's own price-history
endpoint once API access is set up.

Live / paper: FOREX.com's REST price-history endpoint (see broker.py). Kept
separate so the backtester runs with zero network / credentials.
"""
from __future__ import annotations

import csv
import time as _time

from alex_bot.strategy import Candle


def _parse_time(v: str) -> int:
    v = v.strip()
    if v.isdigit():
        n = int(v)
        return n // 1000 if n > 10_000_000_000 else n  # ms -> s
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y.%m.%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(_time.mktime(_time.strptime(v[:19], fmt)))
        except ValueError:
            continue
    raise ValueError(f"unrecognized time value {v!r}")


def load_csv(path: str) -> list[Candle]:
    """Read OHLC candles from a CSV file (header row auto-detected)."""
    out: list[Candle] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return out
    start = 0
    header = [c.strip().lower() for c in rows[0]]
    idx = {"time": 0, "open": 1, "high": 2, "low": 3, "close": 4, "volume": 5}
    if any(h in ("time", "date", "timestamp", "open") for h in header):
        start = 1
        def col(name, alts):
            for n in (name, *alts):
                if n in header:
                    return header.index(n)
            return idx[name]
        idx = {"time": col("time", ("date", "timestamp", "datetime")),
               "open": col("open", ()), "high": col("high", ()),
               "low": col("low", ()), "close": col("close", ()),
               "volume": col("volume", ("vol",)) if "volume" in header or "vol" in header else -1}
    for r in rows[start:]:
        if len(r) <= idx["close"]:
            continue
        vol = float(r[idx["volume"]]) if idx["volume"] >= 0 and idx["volume"] < len(r) else 0.0
        out.append(Candle(time=_parse_time(r[idx["time"]]),
                          open=float(r[idx["open"]]), high=float(r[idx["high"]]),
                          low=float(r[idx["low"]]), close=float(r[idx["close"]]),
                          volume=vol))
    out.sort(key=lambda c: c.time)
    return out


def synthetic_uptrend_with_pullback(pair: str = "EUR/USD") -> list[Candle]:
    """Deterministic series with a clean bullish setup that resolves to a win —
    for tests/demos. Uptrend + three touches of ~1.1100 support (builds a valid
    AOI), then a 4th pullback (entry) followed by a rally that hits take-profit.
    """
    seq = [1.1000, 1.1050, 1.0980, 1.1100, 1.1020, 1.1200]
    for _ in range(3):                       # three touches build the 1.1100 zone
        seq += [1.1300, 1.1180, 1.1100, 1.1170, 1.1260]
    # 4th pullback into the now-valid zone, then a rally up through take-profit
    seq += [1.1280, 1.1150, 1.1100, 1.1200, 1.1300, 1.1380, 1.1460, 1.1520]
    return [Candle(time=i * 900, open=p, high=p + 0.001, low=p - 0.001, close=p)
            for i, p in enumerate(seq)]
