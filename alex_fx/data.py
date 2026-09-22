"""Candle data for the forex bot — KEYLESS.

Everything here works with NO API key:
  * load_csv(path)         — your own downloaded OHLC (Dukascopy/HistData/etc.)
  * yahoo_candles(pair,tf) — Yahoo Finance chart endpoint (intraday, keyless)
  * stooq_daily(pair)      — Stooq CSV download (daily, keyless)
  * get_candles(pair,tf,source) — dispatcher used by backtest + local paper sim

No credentials, no app key. (Real broker order execution is separate — see
broker.py — but data, backtest, and paper simulation never need a key.)
"""
from __future__ import annotations

import csv
import time as _time

import requests

from alex_bot.strategy import Candle

# "EUR/USD" -> Yahoo "EURUSD=X" / Stooq "eurusd".
_YF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
                "1h": "60m", "4h": "60m", "1d": "1d"}
_YF_RANGE = {"1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
             "1h": "730d", "4h": "730d", "1d": "5y"}


def _yahoo_symbol(pair: str) -> str:
    return pair.replace("/", "").upper() + "=X"


def _stooq_symbol(pair: str) -> str:
    return pair.replace("/", "").lower()


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
    with open(path, newline="") as f:
        return _parse_csv_text(f.read())


def _parse_csv_text(text: str) -> list[Candle]:
    out: list[Candle] = []
    rows = list(csv.reader(text.splitlines()))
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


def parse_yahoo(payload: dict) -> list[Candle]:
    """Turn a Yahoo chart JSON payload into candles (keyless, pure)."""
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        return []
    ts = result.get("timestamp") or []
    q = (result.get("indicators", {}).get("quote") or [{}])[0]
    o, h, l, c = (q.get("open", []), q.get("high", []), q.get("low", []),
                  q.get("close", []))
    out: list[Candle] = []
    for i, t in enumerate(ts):
        if None in (o[i], h[i], l[i], c[i]):
            continue
        out.append(Candle(time=int(t), open=float(o[i]), high=float(h[i]),
                          low=float(l[i]), close=float(c[i])))
    return out


def yahoo_candles(pair: str, tf: str, limit: int = 300) -> list[Candle]:
    """Intraday/daily FX candles from Yahoo Finance — no API key."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{_yahoo_symbol(pair)}"
    params = {"interval": _YF_INTERVAL.get(tf, "15m"),
              "range": _YF_RANGE.get(tf, "60d")}
    resp = requests.get(url, params=params, timeout=20,
                        headers={"User-Agent": "Mozilla/5.0 alex-fx/1.0"})
    resp.raise_for_status()
    candles = parse_yahoo(resp.json())
    if tf == "4h":                              # Yahoo has no 4h; roll 60m -> 4h
        candles = _resample_4h(candles)
    return candles[-limit:]


def _resample_4h(candles: list[Candle]) -> list[Candle]:
    out: list[Candle] = []
    cur: Candle | None = None
    for c in candles:
        b = c.time - (c.time % 14400)
        if cur is None or b != cur.time:
            if cur is not None:
                out.append(cur)
            cur = Candle(b, c.open, c.high, c.low, c.close, c.volume)
        else:
            cur.high = max(cur.high, c.high)
            cur.low = min(cur.low, c.low)
            cur.close = c.close
    if cur is not None:
        out.append(cur)
    return out


def stooq_daily(pair: str, limit: int = 300) -> list[Candle]:
    """Daily FX candles from Stooq CSV download — no API key."""
    url = f"https://stooq.com/q/d/l/?s={_stooq_symbol(pair)}&i=d"
    resp = requests.get(url, timeout=20, headers={"User-Agent": "alex-fx/1.0"})
    resp.raise_for_status()
    return _parse_csv_text(resp.text)[-limit:]


def get_candles(pair: str, tf: str, source: str = "yahoo",
                limit: int = 300) -> list[Candle]:
    """Keyless dispatcher: 'yahoo' (intraday) | 'stooq' (daily)."""
    if source == "stooq":
        return stooq_daily(pair, limit)
    return yahoo_candles(pair, tf, limit)


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
