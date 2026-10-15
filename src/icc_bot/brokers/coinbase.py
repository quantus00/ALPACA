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
import math
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


def _make_client(api_key: Optional[str] = None, api_secret: Optional[str] = None):
    """Build a RESTClient from a CDP key file or inline key/secret.

    Precedence: explicit args > COINBASE_KEY_FILE (a downloaded CDP key JSON) >
    COINBASE_API_KEY / COINBASE_API_SECRET.
    """
    from coinbase.rest import RESTClient
    key = api_key or os.environ.get("COINBASE_API_KEY")
    secret = api_secret or os.environ.get("COINBASE_API_SECRET")
    key_file = os.environ.get("COINBASE_KEY_FILE")
    if key and secret:
        return RESTClient(api_key=key, api_secret=secret)
    if key_file:
        if not os.path.exists(os.path.expanduser(key_file)):
            raise RuntimeError(
                f"COINBASE_KEY_FILE points to {key_file!r} but that file does not "
                "exist. Download your CDP API key JSON from portal.cdp.coinbase.com "
                "(API keys -> Create) and save it at that path (chmod 600)."
            )
        return RESTClient(key_file=os.path.expanduser(key_file))
    raise RuntimeError(
        "Coinbase credentials missing: set COINBASE_KEY_FILE to your CDP key "
        "JSON, or COINBASE_API_KEY + COINBASE_API_SECRET."
    )


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

    def place_bracket(self, order: Order) -> dict:
        """Native OCO bracket: entry + take-profit (limit) + stop-loss (trigger)."""
        if order.take_profit is None or order.stop_price is None:
            return self.place_order(order)
        cid = order.client_id or str(uuid.uuid4())
        if not self.live:
            log.warning("[coinbase:spot dry] BRACKET %s %s size=%.8f tp=%s stop=%s (not sent)",
                        order.side.value, order.symbol, order.qty,
                        order.take_profit, order.stop_price)
            return {"status": "dry_run", "bracket": True}
        fn = (self.client.trigger_bracket_order_gtc_buy if order.side is Side.BUY
              else self.client.trigger_bracket_order_gtc_sell)
        return fn(client_order_id=cid, product_id=order.symbol, base_size=f"{order.qty:.8f}",
                  limit_price=_fmt_price(order.take_profit),
                  stop_trigger_price=_fmt_price(order.stop_price))

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
        self.client = _make_client(api_key, api_secret)
        self._resolve_cache: dict = {}   # symbol -> (product_id, contract_size, day)
        if venue == "perp" and not self.portfolio_uuid:
            # Auto-discover the INTX portfolio; fail clearly if perps aren't enabled.
            from ..discover import intx_portfolio_uuid
            self.portfolio_uuid = intx_portfolio_uuid(self.client)
            if not self.portfolio_uuid:
                raise RuntimeError(
                    "perp venue needs a perpetuals portfolio_uuid and none was found "
                    "(INTX not enabled?). Set COINBASE_PORTFOLIO_UUID or use futures.")

    def _resolve(self, symbol: str) -> tuple:
        """Map a config symbol to a live (product_id, contract_size).

        Accepts a full product id (BIT-28NOV25-CDE, BTC-PERP) or a bare root
        (BIT, GOL, NOL) which is resolved to the current front-month contract.
        Cached per day so monthly rolls pick up automatically.
        """
        from datetime import datetime, timezone
        from ..discover import list_futures, perp_multiplier, pick_front_month

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cached = self._resolve_cache.get(symbol)
        if cached and cached[2] == today:
            return cached[0], cached[1]

        up = symbol.upper()
        if self.venue == "perp" or up.endswith("PERP") or "-PERP" in up:
            pid, size = symbol, perp_multiplier(symbol)
        elif "-" in symbol:                       # explicit dated future id
            pid, size = symbol, self._future_size(symbol)
        else:                                     # bare root -> front month
            info = pick_front_month(list_futures(self.client), symbol)
            if not info:
                raise RuntimeError(f"no live futures contract found for root {symbol!r}")
            pid = info["product_id"]
            size = float(info["contract_size"] or 1.0)
        self._resolve_cache[symbol] = (pid, size, today)
        return pid, size

    def _future_size(self, product_id: str) -> float:
        d = _attr(self.client.get_product(product_id), "future_product_details", default={}) or {}
        return float(_attr(d, "contract_size") or 1.0)

    def contract_multiplier(self, symbol: str) -> float:
        return self._resolve(symbol)[1]

    def get_account(self) -> Account:
        if self.venue == "futures":
            bs = _attr(self.client.get_futures_balance_summary(), "balance_summary")
            eq = _money(_v(bs, "total_usd_balance", "cfm_usd_balance", "cbi_usd_balance"))
            bp = _money(_v(bs, "futures_buying_power")) or eq
            return Account(equity=eq, cash=eq, buying_power=bp)
        # perp
        bal = _attr(self.client.get_perps_portfolio_balances(self.portfolio_uuid),
                    "portfolio_balances", "portfolio")
        eq = _money(_v(bal, "total_balance", "total_portfolio_balance", "buying_power"))
        return Account(equity=eq, cash=eq, buying_power=eq)

    def get_bars(self, symbol, timeframe, limit=300):
        product_id, _ = self._resolve(symbol)
        return fetch_bars(self.client, product_id, timeframe, limit)

    def place_order(self, order: Order) -> dict:
        cid = order.client_id or str(uuid.uuid4())
        product_id, size = self._resolve(order.symbol)
        contracts = units_to_contracts(order.qty, size)   # order.qty is underlying units
        if contracts < 1:
            return {"status": "skipped", "reason": "size < 1 contract", "product_id": product_id}
        if not self.live:
            log.warning("[coinbase:%s dry] %s %s (%s) contracts=%d lev=%s (not sent)",
                        self.venue, order.side.value, order.symbol, product_id,
                        contracts, self.leverage)
            return {"status": "dry_run", "venue": self.venue, "product_id": product_id,
                    "contracts": contracts}
        fn = self.client.market_order_buy if order.side is Side.BUY else self.client.market_order_sell
        return fn(**self._order_kwargs(cid, product_id, str(contracts)))

    def place_bracket(self, order: Order) -> dict:
        """Native OCO bracket for futures/perps (contracts), with leverage/margin."""
        if order.take_profit is None or order.stop_price is None:
            return self.place_order(order)
        cid = order.client_id or str(uuid.uuid4())
        product_id, size = self._resolve(order.symbol)
        contracts = units_to_contracts(order.qty, size)   # order.qty is underlying units
        if contracts < 1:
            return {"status": "skipped", "reason": "size < 1 contract", "product_id": product_id}
        if not self.live:
            log.warning("[coinbase:%s dry] BRACKET %s %s (%s) contracts=%d tp=%s stop=%s lev=%s (not sent)",
                        self.venue, order.side.value, order.symbol, product_id, contracts,
                        order.take_profit, order.stop_price, self.leverage)
            return {"status": "dry_run", "bracket": True, "venue": self.venue,
                    "product_id": product_id, "contracts": contracts}
        fn = (self.client.trigger_bracket_order_gtc_buy if order.side is Side.BUY
              else self.client.trigger_bracket_order_gtc_sell)
        kwargs = self._order_kwargs(cid, product_id, str(contracts))
        kwargs["limit_price"] = _fmt_price(order.take_profit)
        kwargs["stop_trigger_price"] = _fmt_price(order.stop_price)
        return fn(**kwargs)

    def _order_kwargs(self, cid: str, product_id: str, base_size: str) -> dict:
        kwargs = dict(client_order_id=cid, product_id=product_id, base_size=base_size)
        if self.leverage:
            kwargs["leverage"] = str(self.leverage)
        if self.margin_type:
            kwargs["margin_type"] = self.margin_type
        return kwargs

    def get_positions(self) -> List[dict]:
        if self.venue == "futures":
            positions = _attr(self.client.list_futures_positions(), "positions", default=[])
        else:
            positions = _attr(self.client.list_perps_positions(self.portfolio_uuid),
                              "positions", default=[])
        out = []
        for p in positions:
            sym = _v(p, "product_id", "symbol")
            qty = _money(_v(p, "number_of_contracts", "net_size", "position"))
            if sym and qty != 0:
                out.append({"symbol": sym, "qty": qty})
        return out


def _v(obj, *names, default=None):
    return _attr(obj, *names, default=default)


def units_to_contracts(units: float, contract_size: float) -> int:
    """Convert underlying units (from risk sizing) to whole contracts."""
    if contract_size <= 0:
        return 0
    return int(math.floor(units / contract_size))


def _money(x, default: float = 0.0) -> float:
    """Coinbase money fields are often {"value": "123.45", "currency": "USD"}.
    Return the numeric value whether given that object, a scalar, or None."""
    if x is None:
        return default
    if isinstance(x, dict):
        v = x.get("value")
        return float(v) if v not in (None, "") else default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _fmt_price(p: float) -> str:
    """Format a price to a reasonable precision. Coinbase enforces per-product
    tick sizes; if a product needs different rounding, adjust here."""
    p = float(p)
    return f"{p:.2f}" if abs(p) >= 100 else f"{p:.6f}"
