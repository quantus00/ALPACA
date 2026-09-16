"""Fetch historical candles from Coinbase for backtesting / the web cockpit.

Paginates the Advanced Trade candles endpoint (max ~300/req) backwards to
assemble `days` of history for any product id and timeframe. Reuses the same
credential resolution as the brokers (COINBASE_KEY_FILE or API key/secret).
"""
from __future__ import annotations

import math
import time
from typing import List

from .models import Bar

# timeframe label -> (Coinbase granularity, seconds)
GRANULARITY = {
    "1m": ("ONE_MINUTE", 60), "5m": ("FIVE_MINUTE", 300),
    "15m": ("FIFTEEN_MINUTE", 900), "30m": ("THIRTY_MINUTE", 1800),
    "1h": ("ONE_HOUR", 3600), "2h": ("TWO_HOUR", 7200),
    "6h": ("SIX_HOUR", 21600), "1d": ("ONE_DAY", 86400),
}

_PER_REQ = 300


def timeframe_seconds(tf: str) -> int:
    if tf not in GRANULARITY:
        raise ValueError(f"unknown timeframe {tf!r}; use one of {list(GRANULARITY)}")
    return GRANULARITY[tf][1]


def fetch_candles(product_id: str, timeframe: str, days: float) -> List[Bar]:
    """Return up to `days` of OHLCV bars for `product_id`, oldest first."""
    from .brokers.coinbase import _attr, _make_client

    gran, step = GRANULARITY[timeframe]
    need = max(1, int(days * 86400 / step))
    client = _make_client()

    end = int(time.time())
    seen: dict[int, Bar] = {}
    max_rounds = math.ceil(need / _PER_REQ) + 3
    for _ in range(max_rounds):
        start = end - step * _PER_REQ
        resp = client.get_candles(product_id=product_id, start=str(start),
                                  end=str(end), granularity=gran)
        candles = _attr(resp, "candles", default=[]) or []
        if not candles:
            break
        for c in candles:
            ts = int(_attr(c, "start"))
            seen[ts] = Bar(ts=ts, open=float(_attr(c, "open")), high=float(_attr(c, "high")),
                           low=float(_attr(c, "low")), close=float(_attr(c, "close")),
                           volume=float(_attr(c, "volume", default=0.0)))
        end = start
        if len(seen) >= need:
            break
    bars = [seen[t] for t in sorted(seen)]
    return bars[-need:]


def fetch_ccxt(symbol: str, timeframe: str, days: float, exchange: str = "coinbase") -> List[Bar]:
    """Deep-history OHLCV via ccxt (e.g. exchange='binanceus'/'kraken' for months
    of 1m data). Accepts 'BTC-USD' or 'BTC/USD'. Public data, no keys needed."""
    import ccxt  # optional dep

    ex = getattr(ccxt, exchange)({"enableRateLimit": True})
    ex.load_markets()
    sym = symbol if "/" in symbol else symbol.replace("-", "/")
    if sym not in ex.markets:
        raise ValueError(f"{sym!r} not listed on {exchange}")
    tf_ms = ex.parse_timeframe(timeframe) * 1000
    end = ex.milliseconds()
    cursor = end - int(days * 86400 * 1000)
    seen: dict[int, Bar] = {}
    reqs = 0
    while cursor < end and reqs < 5000:
        batch = ex.fetch_ohlcv(sym, timeframe, since=cursor, limit=300)
        reqs += 1
        if not batch:
            break
        for ms, o, h, l, c, v in batch:
            if ms <= end:
                seen[ms] = Bar(ts=int(ms // 1000), open=float(o), high=float(h),
                               low=float(l), close=float(c), volume=float(v or 0.0))
        nxt = batch[-1][0] + tf_ms
        if nxt <= cursor:
            break
        cursor = nxt
    return [seen[t] for t in sorted(seen)]
