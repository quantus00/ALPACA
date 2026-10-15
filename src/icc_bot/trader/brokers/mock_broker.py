"""A no-network mock broker — always works, for safe UI testing."""
from __future__ import annotations

from .base import Broker, Order


class MockBroker(Broker):
    name = "mock"

    def get_accounts(self):
        return [{"account_id": "MOCK-0001", "label": "Mock Cash",
                 "mode": self.mode, "equity": 10000.0}]

    def get_positions(self, account_id=None):
        return [{"symbol": "BTC-USD", "qty": 0.1234, "avg_entry": 50000.0}]

    def get_quote(self, symbol):
        return {"symbol": symbol, "price": 50000.0, "source": "mock"}

    def place_order(self, order: Order, dry_run: bool = True):
        if dry_run:
            return self.order_preview(order)
        # Mock "fills" instantly but never touches a real venue.
        return {"preview": order.describe(), "sent": True,
                "result": {"status": "mock_filled", "order": order.describe()}}
