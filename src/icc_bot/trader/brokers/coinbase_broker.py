"""Coinbase adapter for the Trader cockpit — thin wrapper over the ICC engine's
own Coinbase brokers (spot + derivatives).

mode -> ICC live flag:
  * "paper"  -> live=False  (ICC's brokers refuse to send; they return dry_run)
  * "live"   -> live=True   (real orders; still gated by the app's CONFIRM + the
                             ICC_I_UNDERSTAND_LIVE_RISK env opt-in)

Instrument routing: stock/spot -> CoinbaseBroker; future -> CoinbaseDerivatives
(venue=futures); perp -> CoinbaseDerivatives (venue=perp).
"""
from __future__ import annotations

from .base import Broker, Order


class CoinbaseBroker(Broker):
    name = "coinbase"

    def __init__(self, mode: str = "paper") -> None:
        super().__init__(mode)
        self._live = (mode == "live")
        self._spot = None
        self._deriv: dict = {}

    # -- lazy ICC broker construction (each needs credentials) --------------
    def _spot_broker(self):
        if self._spot is None:
            from ...brokers.coinbase import CoinbaseBroker as ICCSpot
            self._spot = ICCSpot(live=self._live)
        return self._spot

    def _deriv_broker(self, venue: str):
        if venue not in self._deriv:
            from ...brokers.coinbase import CoinbaseDerivativesBroker as ICCDeriv
            self._deriv[venue] = ICCDeriv(venue=venue, live=self._live)
        return self._deriv[venue]

    def _route(self, instrument: str):
        if instrument == "future":
            return self._deriv_broker("futures")
        if instrument == "perp":
            return self._deriv_broker("perp")
        return self._spot_broker()

    # -- read-only views ----------------------------------------------------
    def get_accounts(self):
        out = []
        try:
            a = self._spot_broker().get_account()
            out.append({"account_id": "coinbase-spot", "label": "Coinbase spot",
                        "mode": self.mode, "equity": round(a.equity, 2)})
        except Exception as e:  # noqa - surface, don't crash the page
            out.append({"account_id": "coinbase-spot", "label": "Coinbase spot",
                        "mode": self.mode, "error": str(e)})
        try:
            a = self._deriv_broker("futures").get_account()
            out.append({"account_id": "coinbase-cfm", "label": "Coinbase futures (CFM)",
                        "mode": self.mode, "equity": round(a.equity, 2)})
        except Exception:  # noqa - futures may not be enabled; skip quietly
            pass
        return out

    def get_positions(self, account_id=None):
        if account_id == "coinbase-cfm":
            return self._deriv_broker("futures").get_positions()
        return self._spot_broker().get_positions()

    def get_quote(self, symbol):
        broker = self._deriv_broker("perp") if symbol.upper().endswith("PERP") \
            else self._spot_broker()
        bars = broker.get_bars(symbol, "1m", limit=2)
        if not bars:
            return {"symbol": symbol, "error": "no candles"}
        return {"symbol": symbol, "price": bars[-1].close, "source": "coinbase"}

    # -- orders -------------------------------------------------------------
    def place_order(self, order: Order, dry_run: bool = True):
        if dry_run:
            return self.order_preview(order)

        from ...models import Order as ICCOrder, Side
        broker = self._route(order.instrument)

        qty = order.qty
        if (qty is None or qty <= 0) and order.fractional and order.notional:
            q = self.get_quote(order.symbol)
            price = q.get("price")
            if not price:
                return {"preview": order.describe(), "sent": False,
                        "error": "could not price notional order"}
            qty = float(order.notional) / float(price)
        if qty is None or qty <= 0:
            return {"preview": order.describe(), "sent": False, "error": "no quantity"}

        icc_order = ICCOrder(
            symbol=order.symbol,
            side=Side.BUY if order.side == "buy" else Side.SELL,
            qty=float(qty),
            type=order.type,
            limit_price=order.price if order.type == "limit" else None,
            meta={"source": "trader-cockpit"},
        )
        result = broker.place_order(icc_order)
        sent = str(result.get("status")) not in ("dry_run", "skipped", None)
        return {"preview": order.describe(), "sent": sent, "result": result}
