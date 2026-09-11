"""Coinbase adapter (crypto spot + derivatives), on `coinbase-advanced-py`.

    pip install coinbase-advanced-py

Auth: Coinbase Developer Platform API key (key name + EC private key) in
COINBASE_API_KEY / COINBASE_API_SECRET.

Venues:
  * "spot"    — Advanced Trade spot (base units).  No paper sandbox.
  * "futures" — Coinbase Financial Markets US futures (dated + nano). Sized in
                whole CONTRACTS. Balances via get_futures_balance_summary.
  * "perp"    — Coinbase International (INTX) perpetuals. Sized in CONTRACTS.
                Requires a perpetuals-enabled portfolio_uuid.

IMPORTANT: product IDs (e.g. the nano BTC perp, a Nov-expiry BTC future, a gold
future) and their contract multipliers are NOT hardcoded — you pass them via
config, and must confirm they exist and your account is enabled for them.
Derivatives are leveraged; only LIVE places real orders.
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

_GRANULARITY = {
    "1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "2h": "TWO_HOUR",
    "6h": "SIX_HOUR", "1d": "ONE_DAY",
}
_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "6h": 21600, "1d": 86400,
}


def _attr(obj, *names, default=None):
    """Read a field whether the SDK returns objects or dicts; tries several names."""
    if obj is None:
        return default
    for name in names:
        if isinstance(obj, dict):
            if obj.get(name) is not None:
                return obj[name]
        elif getattr(obj, name, None) is not None:
            return getattr(obj, name)
    return default


def _make_client(api_key: Optional[str], api_secret: Optional[str]):
    from coinbase.rest import RESTClient
    key = api_key or os.environ.get("COINBASE_API_KEY")
    secret = api_secret or os.environ.get("COINBASE_API_SECRET")
    if not key or not secret:
        raise RuntimeError(
            "Coinbase credentials missing: set COINBASE_API_KEY and "
            "COINBASE_API_SECRET (Coinbase Developer Platform API key)."
        )
    return RESTClient(api_key=key, api_secret=secret)


def fetch_bars(client, symbol: str, timeframe: str, limit: int = 300) -> List[Bar]:
    """Shared candle fetch — works for spot and derivatives product IDs."""
    if timeframe not in _GRANULARITY:
        raise ValueError(f"Coinbase does not support timeframe {timeframe!r}")
    end = int(time.time())
    start = end - _SECONDS[timeframe] * (limit + 1)
    resp = client.get_candles(product_id=symbol, start=str(start), end=str(end),
                              granularity=_GRANULARITY[timeframe])
    candles = _attr(resp, "candles", default=[])
    bars = [
        Bar(ts=int(_attr(c, "start")), open=float(_attr(c, "open")),
            high=float(_attr(c, "high")), low=float(_attr(c, "low")),
            close=float(_attr(c, "close")), volume=float(_attr(c, "volume", default=0.0)))
        for c in candles
    ]
    bars.sort(key=lambda b: b.ts)   # Coinbase returns newest-first
    return bars[-limit:]


class CoinbaseBroker(Broker):
    """Spot crypto (base units)."""

    name = "coinbase"

    def __init__(self, live: bool = False, api_key=None, api_secret=None) -> None:
        self.live = live
        self.client = _make_client(api_key, api_secret)

    def get_account(self) -> Account:
        cash = 0.0
        for a in _attr(self.client.get_accounts(), "accounts", default=[]):
            if _attr(a, "currency") in ("USD", "USDC"):
                bal = _attr(_attr(a, "available_balance"), "value")
                if bal is not None:
                    cash += float(bal)
        return Account(equity=cash, cash=cash, buying_power=cash)

    def get_bars(self, symbol, timeframe, limit=300):
        return fetch_bars(self.client, symbol, timeframe, limit)

    def place_order(self, order: Order) -> dict:
        cid = order.client_id or str(uuid.uuid4())
        if not self.live:
            log.warning("[coinbase:spot dry] %s %s base_size=%.8f (no sandbox; not sent)",
                        order.side.value, order.symbol, order.qty)
            return {"status": "dry_run", "reason": "coinbase spot has no paper account"}
        size = f"{order.qty:.8f}"
        fn = self.client.market_order_buy if order.side is Side.BUY else self.client.market_order_sell
        return fn(client_order_id=cid, product_id=order.symbol, base_size=size)

    def get_positions(self) -> List[dict]:
        out = []
        for a in _attr(self.client.get_accounts(), "accounts", default=[]):
            cur = _attr(a, "currency")
            bal = _attr(_attr(a, "available_balance"), "value")
            if cur not in ("USD", "USDC") and bal and float(bal) > 0:
                out.append({"symbol": f"{cur}-USD", "qty": float(bal)})
        return out


class CoinbaseDerivativesBroker(Broker):
    """Coinbase futures (CFM) or perpetuals (INTX). Orders are in whole contracts."""

    name = "coinbase_deriv"

    def __init__(self, venue: str = "futures", live: bool = False,
                 portfolio_uuid: Optional[str] = None, leverage: Optional[str] = None,
                 margin_type: str = "CROSS", api_key=None, api_secret=None) -> None:
        if venue not in ("futures", "perp"):
            raise ValueError("derivatives venue must be 'futures' or 'perp'")
        self.venue = venue
        self.live = live
        self.leverage = leverage
        self.margin_type = margin_type
        self.portfolio_uuid = portfolio_uuid or os.environ.get("COINBASE_PORTFOLIO_UUID")
        if venue == "perp" and not self.portfolio_uuid:
            raise RuntimeError("perp venue requires a perpetuals portfolio_uuid "
                               "(set COINBASE_PORTFOLIO_UUID).")
        self.client = _make_client(api_key, api_secret)

    def get_account(self) -> Account:
        if self.venue == "futures":
            bs = _attr(self.client.get_futures_balance_summary(), "balance_summary")
            eq = float(_v(bs, "total_usd_balance", "cfm_usd_balance", "cbi_usd_balance", default=0.0))
            bp = float(_v(bs, "futures_buying_power", default=eq))
            return Account(equity=eq, cash=eq, buying_power=bp)
        # perp
        bal = _attr(self.client.get_perps_portfolio_balances(self.portfolio_uuid),
                    "portfolio_balances", "portfolio")
        eq = float(_v(bal, "total_balance", "total_portfolio_balance", "buying_power", default=0.0))
        return Account(equity=eq, cash=eq, buying_power=eq)

    def get_bars(self, symbol, timeframe, limit=300):
        return fetch_bars(self.client, symbol, timeframe, limit)

    def place_order(self, order: Order) -> dict:
        cid = order.client_id or str(uuid.uuid4())
        contracts = int(order.qty)
        if contracts < 1:
            return {"status": "skipped", "reason": "size < 1 contract"}
        if not self.live:
            log.warning("[coinbase:%s dry] %s %s contracts=%d lev=%s (not sent)",
                        self.venue, order.side.value, order.symbol, contracts, self.leverage)
            return {"status": "dry_run", "venue": self.venue, "contracts": contracts}
        fn = self.client.market_order_buy if order.side is Side.BUY else self.client.market_order_sell
        kwargs = dict(client_order_id=cid, product_id=order.symbol, base_size=str(contracts))
        if self.leverage:
            kwargs["leverage"] = str(self.leverage)
        if self.margin_type:
            kwargs["margin_type"] = self.margin_type
        return fn(**kwargs)

    def get_positions(self) -> List[dict]:
        if self.venue == "futures":
            positions = _attr(self.client.list_futures_positions(), "positions", default=[])
        else:
            positions = _attr(self.client.list_perps_positions(self.portfolio_uuid),
                              "positions", default=[])
        out = []
        for p in positions:
            sym = _v(p, "product_id", "symbol")
            qty = _v(p, "number_of_contracts", "net_size", "position", default=0)
            if sym and float(qty or 0) != 0:
                out.append({"symbol": sym, "qty": float(qty)})
        return out


def _v(obj, *names, default=None):
    return _attr(obj, *names, default=default)
