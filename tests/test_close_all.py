"""Offline tests for `close-all` (bot/close_all.py) — fake Coinbase client.

No network, no real orders: verifies position listing, dust skipping,
increment quantization, stablecoin exclusion, and that dry-run sends nothing
while --yes sends one SELL per sellable position.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import close_all as ca  # noqa: E402
from bot.config import Config  # noqa: E402


class FakeClient:
    """Mimics the coinbase-advanced-py RESTClient surface close_all uses."""
    PRODUCTS = {
        "SPX-USD":  {"base_increment": "0.01",       "base_min_size": "0.01",  "price": "1.00"},
        "LOKA-USD": {"base_increment": "0.0001",      "base_min_size": "0.01",  "price": "0.25"},
        "ETH-USD":  {"base_increment": "0.00000001",  "base_min_size": "0.0001","price": "3400"},
        "SUI-USD":  {"base_increment": "0.0001",      "base_min_size": "0.1",   "price": "3.20"},
    }

    def __init__(self):
        self.orders = []

    def get_accounts(self, limit=250):
        return {"accounts": [
            {"currency": "SPX",  "available_balance": {"value": "0.019"}},   # -> 0.01 sellable
            {"currency": "LOKA", "available_balance": {"value": "0.08164"}}, # sellable
            {"currency": "ETH",  "available_balance": {"value": "5.9e-9"}},  # dust -> skip
            {"currency": "SUI",  "available_balance": {"value": "0.0001"}},  # below min_size -> skip
            {"currency": "USD",  "available_balance": {"value": "77.53"}},   # stablecoin -> skip
        ]}

    def get_product(self, product_id):
        return self.PRODUCTS[product_id]

    def create_order(self, client_order_id, product_id, side, order_configuration):
        self.orders.append((product_id, side, order_configuration))
        return {"success": True, "order_id": f"ord-{len(self.orders)}"}


def _patch(monkeypatch, client):
    monkeypatch.setattr("bot.brokers.coinbase.CoinbaseBroker._get_client",
                        lambda self: client)


def test_positions_exclude_stablecoins_and_zero():
    c = FakeClient()
    pos = dict(ca.coinbase_positions(c))
    assert "USD" not in pos                 # stablecoin excluded
    assert set(pos) == {"SPX", "LOKA", "ETH", "SUI"}


def test_quantize_down():
    from decimal import Decimal
    assert ca._quantize_down(Decimal("0.019"), Decimal("0.01")) == Decimal("0.01")
    assert ca._quantize_down(Decimal("0.08164"), Decimal("0.0001")) == Decimal("0.0816")


def test_dry_run_sends_nothing_and_skips_dust(monkeypatch, capsys):
    client = FakeClient()
    _patch(monkeypatch, client)
    plan = ca.plan_and_close(Config(), execute=False)
    products = {o["product_id"] for o in plan}
    assert products == {"SPX-USD", "LOKA-USD"}     # ETH/SUI dust skipped
    assert client.orders == []                     # nothing sent in dry-run
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "use Convert" in out


def test_execute_sends_one_sell_per_position(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client)
    plan = ca.plan_and_close(Config(), execute=True)
    sold = {p for (p, side, _cfg) in client.orders}
    assert sold == {"SPX-USD", "LOKA-USD"}
    assert all(side == "SELL" for (_p, side, _cfg) in client.orders)
    # quantized base_size is a plain decimal string (no sci-notation)
    cfgs = [oc["market_market_ioc"]["base_size"] for (_p, _s, oc) in client.orders]
    assert "0.01" in cfgs and all("e" not in s.lower() for s in cfgs)
    assert all(o["ok"] for o in plan)


def test_only_filter(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client)
    plan = ca.plan_and_close(Config(), execute=True, only=["SPX"])
    assert {o["product_id"] for o in plan} == {"SPX-USD"}
    assert [p for (p, _s, _c) in client.orders] == ["SPX-USD"]


def test_min_usd_skips_small(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client)
    # LOKA ~0.0816*0.25 = $0.02; SPX 0.01*1 = $0.01. min-usd 5 skips both.
    plan = ca.plan_and_close(Config(), execute=True, min_usd=5.0)
    assert plan == []
    assert client.orders == []


if __name__ == "__main__":
    print("run under pytest")
