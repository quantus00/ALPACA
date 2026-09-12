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
