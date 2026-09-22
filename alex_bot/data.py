"""Candle data for the Alex bot — keyless for crypto (Coinbase public API).

Equities/options data (for the Webull side) will plug in here later via the
cockpit's data source; for now crypto works with no credentials.
"""
from __future__ import annotations

import logging

import requests

from .strategy import Candle

log = logging.getLogger(__name__)

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
              "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400}
_CB_GRAN = {60, 300, 900, 3600, 21600, 86400}


def tf_seconds(tf: str) -> int:
    if tf not in TF_SECONDS:
        raise ValueError(f"unsupported timeframe {tf!r}")
    return TF_SECONDS[tf]


def _resample(candles: list[Candle], target: int) -> list[Candle]:
    out: list[Candle] = []
    cur: Candle | None = None
    for c in candles:
        b = c.time - (c.time % target)
        if cur is None or b != cur.time:
            if cur is not None:
                out.append(cur)
            cur = Candle(time=b, open=c.open, high=c.high, low=c.low,
                         close=c.close, volume=c.volume)
        else:
            cur.high = max(cur.high, c.high)
            cur.low = min(cur.low, c.low)
            cur.close = c.close
            cur.volume += c.volume
    if cur is not None:
        out.append(cur)
    return out


def coinbase_candles(symbol: str, tf: str, limit: int = 300) -> list[Candle]:
    want = tf_seconds(tf)
    base = want if want in _CB_GRAN else (3600 if want % 3600 == 0 else 300)
    product = symbol.upper().replace("BTC-PERP-INTX", "BTC-USD")
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    resp = requests.get(url, params={"granularity": base}, timeout=15,
                        headers={"User-Agent": "alex-bot/1.0"})
    resp.raise_for_status()
    rows = sorted(resp.json(), key=lambda r: r[0])
    raw = [Candle(time=int(r[0]), low=float(r[1]), high=float(r[2]),
                  open=float(r[3]), close=float(r[4]), volume=float(r[5]))
           for r in rows]
    candles = raw if want == base else _resample(raw, want)
    return candles[-limit:]


def get_candles(symbol: str, tf: str, limit: int = 300) -> list[Candle]:
    """Crypto only for now (Coinbase public). Stocks/options come later via
    the cockpit's data source."""
    s = symbol.upper()
    if "-" in s or any(t in s for t in ("BTC", "ETH", "SOL", "USD")):
        return coinbase_candles(symbol, tf, limit)
    raise RuntimeError(
        f"{symbol}: only crypto (Coinbase) data is wired up so far; "
        "equities/options will route through the cockpit data source.")
