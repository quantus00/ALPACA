"""Webull adapter for the Trader cockpit — thin wrapper over the ICC engine's
WebullBroker (equities, whole shares).

NOTE: this uses ICC's unofficial-`webull`-package broker (WEBULL_EMAIL /
WEBULL_PASSWORD / WEBULL_TRADE_PIN). It does NOT place options — ICC has no
options path. mode "paper" -> ICC paper account; "live" -> real account.
"""
from __future__ import annotations

from .base import Broker, Order


class WebullBroker(Broker):
    name = "webull"

    def __init__(self, mode: str = "paper") -> None:
        super().__init__(mode)
        self._live = (mode == "live")
        self._wb = None

    def _broker(self):
        if self._wb is None:
            from ...brokers.webull import WebullBroker as ICCWebull
            self._wb = ICCWebull(live=self._live)
        return self._wb

    def get_accounts(self):
        a = self._broker().get_account()
        return [{"account_id": f"webull-{self.mode}", "label": "Webull",
                 "mode": self.mode, "equity": round(a.equity, 2)}]

    def get_positions(self, account_id=None):
        return self._broker().get_positions()

    def get_quote(self, symbol):
        bars = self._broker().get_bars(symbol, "1m", limit=2)
        if not bars:
            return {"symbol": symbol, "error": "no candles"}
        return {"symbol": symbol, "price": bars[-1].close, "source": "webull"}

    def place_order(self, order: Order, dry_run: bool = True):
        if dry_run:
            return self.order_preview(order)
        if order.instrument == "option":
            return {"preview": order.describe(), "sent": False,
                    "error": "options are not supported by the ICC Webull broker"}
        from ...models import Order as ICCOrder, Side
        icc_order = ICCOrder(
            symbol=order.symbol,
            side=Side.BUY if order.side == "buy" else Side.SELL,
            qty=float(order.qty or 0),
            type=order.type,
            limit_price=order.price if order.type == "limit" else None,
            meta={"source": "trader-cockpit"},
        )
        result = self._broker().place_order(icc_order)
        sent = str(result.get("status")) not in ("skipped", "dry_run", None)
        return {"preview": order.describe(), "sent": sent, "result": result}
