"""Candle data feeds.

Crypto candles come from Coinbase's public Advanced Trade / Exchange API (no
key needed for public market data). Equity / futures underlyings (SPY, MES ->
ES/SPX proxy) fall back to Alpaca or a public source. The goal is a single
``get_candles(symbol, timeframe, limit)`` used by the strategy runner.
"""
from __future__ import annotations

import logging
import time
from typing import Sequence

import requests

from .trend import Candle

log = logging.getLogger(__name__)

# Map our timeframe strings to seconds.
TF_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
}

# Coinbase Exchange granularity is restricted to a fixed set of seconds.
_COINBASE_GRANULARITY = {60, 300, 900, 3600, 21600, 86400}


def timeframe_seconds(tf: str) -> int:
    if tf not in TF_SECONDS:
        raise ValueError(f"Unsupported timeframe {tf!r}")
    return TF_SECONDS[tf]


def _coinbase_candles(symbol: str, tf: str, limit: int) -> list[Candle]:
    """Fetch candles from Coinbase Exchange public API, resampling when the
    requested timeframe is not a native granularity (e.g. 4h, 15m, 30m)."""
    want = timeframe_seconds(tf)
    base = want if want in _COINBASE_GRANULARITY else 3600
    if want in _COINBASE_GRANULARITY:
        base = want
    elif want % 3600 == 0:
        base = 3600
    elif want % 300 == 0:
        base = 300
    else:
        base = 60

    product = symbol.replace("BTC-PERP-INTX", "BTC-USD")  # perp -> use spot for structure
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    need = limit * (want // base) + base
    params = {"granularity": base}
    resp = requests.get(url, params=params, timeout=15,
                        headers={"User-Agent": "mtf-trend-bot/1.0"})
    resp.raise_for_status()
    # Coinbase returns [time, low, high, open, close, volume], newest first.
    rows = sorted(resp.json(), key=lambda r: r[0])
    raw = [
        Candle(time=int(r[0]), low=float(r[1]), high=float(r[2]),
               open=float(r[3]), close=float(r[4]), volume=float(r[5]))
        for r in rows
    ]
    candles = _resample(raw, base, want) if want != base else raw
    return candles[-limit:]


def _resample(candles: Sequence[Candle], base_sec: int, target_sec: int) -> list[Candle]:
    """Aggregate base-granularity candles up to the target timeframe."""
    if target_sec % base_sec != 0:
        return list(candles)
    bucket = target_sec
    out: list[Candle] = []
    cur: Candle | None = None
    for c in candles:
        bstart = c.time - (c.time % bucket)
        if cur is None or bstart != cur.time:
            if cur is not None:
                out.append(cur)
            cur = Candle(time=bstart, open=c.open, high=c.high, low=c.low,
                         close=c.close, volume=c.volume)
        else:
            cur.high = max(cur.high, c.high)
            cur.low = min(cur.low, c.low)
            cur.close = c.close
            cur.volume += c.volume
    if cur is not None:
        out.append(cur)
    return out


def _alpaca_candles(symbol: str, tf: str, limit: int) -> list[Candle]:
    """Fetch equity bars from Alpaca's market-data API (needs API keys)."""
    import os

    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_API_SECRET")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET required for equity data")
    tf_map = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min",
              "1h": "1Hour", "4h": "4Hour", "1d": "1Day"}
    feed = os.getenv("ALPACA_DATA_FEED", "iex")
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    params = {"timeframe": tf_map.get(tf, "5Min"), "limit": limit, "feed": feed}
    resp = requests.get(url, params=params, timeout=15, headers={
        "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    resp.raise_for_status()
    bars = resp.json().get("bars", [])
    return [
        Candle(time=int(time.mktime(time.strptime(b["t"][:19], "%Y-%m-%dT%H:%M:%S"))),
               open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=b.get("v", 0))
        for b in bars
    ]


def get_candles(symbol: str, timeframe: str, limit: int = 300) -> list[Candle]:
    """Return the most recent ``limit`` candles for ``symbol`` at ``timeframe``.

    Routing: crypto symbols (contain 'BTC') -> Coinbase public data; everything
    else (SPY, MES/ES) -> Alpaca. MES structure is derived from SPY as a liquid,
    freely-available proxy for the S&P 500 unless an ES feed is configured.
    """
    sym = symbol.upper()
    if "BTC" in sym:
        return _coinbase_candles(symbol, timeframe, limit)
    proxy = "SPY" if sym in ("MES", "ES", "SPX") else symbol
    return _alpaca_candles(proxy, timeframe, limit)
