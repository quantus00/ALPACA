"""Coinbase adapter (crypto), built on the official `coinbase-advanced-py` SDK.

    pip install coinbase-advanced-py

Auth uses a Coinbase Developer Platform API key (key name + EC private key). Set
COINBASE_API_KEY and COINBASE_API_SECRET in the environment.

NOTE: Coinbase Advanced Trade has no general paper/sandbox, so PAPER mode is
treated as dry-run here (data only, no orders). Only LIVE places real orders.
Verify SDK method signatures against your installed version before going live.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import List, Optional

from ..models import Account, Bar, Order, Side
from .base import Broker

log = logging.getLogger("icc_bot.broker.coinbase")

# icc_bot timeframe -> Coinbase granularity enum
_GRANULARITY = {
    "1m": "ONE_MINUTE",
    "5m": "FIVE_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h": "ONE_HOUR",
    "2h": "TWO_HOUR",
    "6h": "SIX_HOUR",
    "1d": "ONE_DAY",
}
_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "6h": 21600, "1d": 86400,
}


class CoinbaseBroker(Broker):
    name = "coinbase"

    def __init__(self, live: bool = False, api_key: Optional[str] = None,
                 api_secret: Optional[str] = None) -> None:
        from coinbase.rest import RESTClient  # imported lazily

        self.live = live
        key = api_key or os.environ.get("COINBASE_API_KEY")
        secret = api_secret or os.environ.get("COINBASE_API_SECRET")
        if not key or not secret:
            raise RuntimeError(
                "Coinbase credentials missing: set COINBASE_API_KEY and "
                "COINBASE_API_SECRET (Coinbase Developer Platform API key)."
            )
        self.client = RESTClient(api_key=key, api_secret=secret)

    def get_account(self) -> Account:
        # Sum available USD/USDC balance; treat as cash & buying power.
        cash = 0.0
        resp = self.client.get_accounts()
        accounts = getattr(resp, "accounts", None) or resp.get("accounts", [])
        for a in accounts:
            cur = _attr(a, "currency")
            bal = _attr(_attr(a, "available_balance"), "value")
            if cur in ("USD", "USDC") and bal is not None:
                cash += float(bal)
        return Account(equity=cash, cash=cash, buying_power=cash)

    def get_bars(self, symbol: str, timeframe: str, limit: int = 300) -> List[Bar]:
        if timeframe not in _GRANULARITY:
            raise ValueError(f"Coinbase does not support timeframe {timeframe!r}")
        end = int(time.time())
        start = end - _SECONDS[timeframe] * (limit + 1)
        resp = self.client.get_candles(
            product_id=symbol,
            start=str(start),
            end=str(end),
            granularity=_GRANULARITY[timeframe],
        )
        candles = getattr(resp, "candles", None) or resp.get("candles", [])
        bars = [
            Bar(
                ts=int(_attr(c, "start")),
                open=float(_attr(c, "open")),
                high=float(_attr(c, "high")),
                low=float(_attr(c, "low")),
                close=float(_attr(c, "close")),
                volume=float(_attr(c, "volume") or 0.0),
            )
            for c in candles
        ]
        bars.sort(key=lambda b: b.ts)  # Coinbase returns newest-first
        return bars[-limit:]

    def place_order(self, order: Order) -> dict:
        client_id = order.client_id or str(uuid.uuid4())
        if not self.live:
            log.warning("[coinbase:paper->dry] %s %s base_size=%.8f (no sandbox; not sent)",
                        order.side.value, order.symbol, order.qty)
            return {"status": "dry_run", "reason": "coinbase has no paper; use live to trade"}
        base_size = f"{order.qty:.8f}"
        if order.side is Side.BUY:
            return self.client.market_order_buy(
                client_order_id=client_id, product_id=order.symbol, base_size=base_size)
        return self.client.market_order_sell(
            client_order_id=client_id, product_id=order.symbol, base_size=base_size)

    def get_positions(self) -> List[dict]:
        out = []
        resp = self.client.get_accounts()
        accounts = getattr(resp, "accounts", None) or resp.get("accounts", [])
        for a in accounts:
            cur = _attr(a, "currency")
            bal = _attr(_attr(a, "available_balance"), "value")
            if cur not in ("USD", "USDC") and bal and float(bal) > 0:
                out.append({"symbol": f"{cur}-USD", "qty": float(bal)})
        return out


def _attr(obj, name):
    """Read a field whether the SDK returns objects or dicts."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
