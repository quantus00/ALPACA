"""Broker abstraction: every venue implements this so the app is broker-agnostic.

Ported from the Drive cockpit. Adapters must be safe to call with dry_run and
must NEVER return secrets.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Order:
    broker: str
    mode: str                      # "paper" | "live"
    account_id: str
    symbol: str
    side: str                      # "buy" | "sell"
    qty: Optional[float] = None
    notional: Optional[float] = None   # dollar amount (fractional)
    type: str = "market"           # "market" | "limit"
    price: Optional[float] = None
    instrument: str = "stock"      # stock | option | future | perp
    fractional: bool = False

    def describe(self) -> str:
        size = f"${self.notional}" if (self.fractional and self.notional) else self.qty
        px = f" @ {self.price}" if self.type == "limit" else " @ market"
        return (f"[{self.mode.upper()}] {self.side.upper()} {size} {self.symbol} "
                f"({self.instrument}) {self.type}{px} on {self.broker}")


class Broker:
    """Base class. Adapters override these."""

    name = "base"

    def __init__(self, mode: str = "paper") -> None:
        self.mode = mode

    def get_accounts(self):
        raise NotImplementedError

    def get_positions(self, account_id=None):
        raise NotImplementedError

    def get_quote(self, symbol):
        raise NotImplementedError

    def place_order(self, order: "Order", dry_run: bool = True):
        """Return {'preview': str, 'sent': bool, ...}. When dry_run is True,
        `sent` must be False and no order may reach the venue."""
        raise NotImplementedError

    @staticmethod
    def order_preview(order: "Order"):
        return {"preview": order.describe(), "sent": False, "order": asdict(order)}
