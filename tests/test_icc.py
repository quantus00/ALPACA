"""Unit tests for the ICC strategy engine, risk manager, and runner cycle.

These run fully offline (no broker, no network).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

import pytest

from icc_bot.brokers.base import Broker, DryRunBroker
from icc_bot.config import BotConfig
from icc_bot.models import Account, Bar, Direction, Mode
from icc_bot.risk import RiskLimits, RiskManager
from icc_bot.runner import run_cycle, size_for_order
from icc_bot.strategy import ICCParams, evaluate
from icc_bot.structure import classify_trend, find_swings


def _bar(ts: float, price: float) -> Bar:
    return Bar(ts=int(ts), open=price, high=price, low=price, close=price, volume=1.0)


def series(pivots: List[float], seg: int = 4) -> List[Bar]:
    """Build a bar series that zig-zags linearly through `pivots`.

    Each turning point becomes a clean fractal pivot; monotonic legs in between
    contain no pivots. Flat OHLC keeps detection about the zig-zag only.
    """
    bars = [_bar(0, pivots[0])]
    ts = 1
    for i in range(1, len(pivots)):
        start, end = pivots[i - 1], pivots[i]
        for k in range(1, seg + 1):
            bars.append(_bar(ts, start + (end - start) * k / seg))
            ts += 1
    return bars


# --- structure -------------------------------------------------------------
def test_find_swings_detects_turning_points():
    bars = series([100, 96, 104, 99, 108, 103])
    swings = find_swings(bars, lookback=2)
    prices = [round(s.price, 2) for s in swings]
    assert 96 in prices and 104 in prices and 99 in prices and 108 in prices


def test_classify_trend_uptrend():
    bars = series([100, 96, 104, 99, 108, 103])
    assert classify_trend(find_swings(bars, 2)) is Direction.LONG


def test_classify_trend_none_on_consolidation():
    bars = series([100, 96, 101, 96, 101, 96])  # equal highs/lows
    assert classify_trend(find_swings(bars, 2)) is None


# --- strategy --------------------------------------------------------------
def test_long_continuation_signal():
    htf = series([100, 96, 104, 99, 108, 103], seg=4)      # HH+HL, new high, now correcting
    ltf = series([106, 100, 103, 100.5, 104], seg=3)       # down-correction then flip up
    sig = evaluate("BTC-USD", htf, ltf, ICCParams(target_rr=3.0))
    assert sig is not None
    assert sig.direction is Direction.LONG
    assert sig.stop < sig.entry < sig.target
    assert sig.rr >= 3.0 - 1e-9


def test_no_signal_when_htf_consolidating():
    htf = series([100, 96, 101, 96, 101, 96])
    ltf = series([106, 100, 103, 100.5, 104], seg=3)
    assert evaluate("BTC-USD", htf, ltf) is None


def test_no_signal_when_ltf_has_not_flipped():
    htf = series([100, 96, 104, 99, 108, 103], seg=4)
    ltf = series([106, 104, 103, 102, 101], seg=3)          # still falling, no break up
    assert evaluate("BTC-USD", htf, ltf) is None


# --- risk ------------------------------------------------------------------
def test_position_size_respects_risk_pct():
    rm = RiskManager(RiskLimits(risk_per_trade_pct=1.0, max_position_pct=100.0))
    acct = Account(equity=10_000, cash=10_000, buying_power=10_000)
    from icc_bot.models import Signal
    sig = Signal(Direction.LONG, "X", entry=100.0, stop=95.0, target=115.0, reason="", ts=0)
    qty = rm.position_size(acct, sig)
    # risk budget = 1% of 10k = $100; risk/unit = 5 -> 20 units.
    assert qty == pytest.approx(20.0)


def test_daily_loss_limit_halts():
    rm = RiskManager(RiskLimits(max_daily_loss_pct=3.0))
    rm.start_day("2026-01-01", equity=10_000)
    ok, _ = rm.can_trade(Account(9_600, 9_600, 9_600))  # -4% > 3% limit
    assert ok is False and rm.state.halted


def test_max_trades_per_day():
    rm = RiskManager(RiskLimits(max_trades_per_day=1, max_open_positions=5))
    rm.start_day("2026-01-01", 10_000)
    rm.register_open()
    ok, why = rm.can_trade(Account(10_000, 10_000, 10_000))
    assert ok is False and "trades per day" in why


# --- runner ----------------------------------------------------------------
class FakeBroker(Broker):
    name = "fake"

    def __init__(self, htf, ltf):
        self._htf, self._ltf = htf, ltf
        self.orders = []

    def get_account(self):
        return Account(10_000, 10_000, 10_000)

    def get_bars(self, symbol, timeframe, limit=300):
        return self._htf if timeframe == "1h" else self._ltf

    def place_order(self, order):
        self.orders.append(order)
        return {"status": "ok"}

    def get_positions(self):
        return []


def test_run_cycle_places_order_on_signal():
    htf = series([100, 96, 104, 99, 108, 103], seg=4)
    ltf = series([106, 100, 103, 100.5, 104], seg=3)
    broker = FakeBroker(htf, ltf)
    cfg = BotConfig(broker="fake", mode=Mode.PAPER, symbols=["BTC-USD"],
                    session_enabled=False)
    rm = RiskManager(cfg.risk)
    actions = run_cycle(cfg, broker, rm, now=datetime(2026, 1, 1, 14, tzinfo=timezone.utc))
    assert any(a["status"] == "ordered" for a in actions)
    assert len(broker.orders) == 1
    # the order carries stop & target so a bracket can enforce exits on-venue
    placed = broker.orders[0]
    assert placed.stop_price is not None and placed.take_profit is not None


def test_run_cycle_skips_out_of_session():
    broker = FakeBroker(series([100, 96, 104, 99, 108, 103]), series([106, 100, 103, 100.5, 104], 3))
    cfg = BotConfig(broker="fake", symbols=["BTC-USD"],
                    session_enabled=True, session_start_utc=13, session_end_utc=16)
    rm = RiskManager(cfg.risk)
    actions = run_cycle(cfg, broker, rm, now=datetime(2026, 1, 1, 3, tzinfo=timezone.utc))
    assert actions == [{"status": "skip", "reason": "out of session"}]
    assert broker.orders == []


def test_size_for_order_spot_is_units():
    cfg = BotConfig(broker="coinbase", venue="spot")
    assert size_for_order(cfg, "BTC-USD", 0.375) == 0.375


def test_size_for_order_derivatives_floors_to_contracts():
    # nano-style multiplier 0.01 BTC/contract: 0.375 BTC -> 37 contracts
    cfg = BotConfig(broker="coinbase", venue="futures",
                    contract_specs={"BTC-NOV-FUT": 0.01})
    assert size_for_order(cfg, "BTC-NOV-FUT", 0.375) == 37.0


def test_size_for_order_below_one_contract_is_zero():
    cfg = BotConfig(broker="coinbase", venue="perp",
                    contract_specs={"BTC-PERP": 1.0})
    assert size_for_order(cfg, "BTC-PERP", 0.4) == 0.0


def test_discover_parse_expiry_and_front_month():
    from datetime import datetime, timezone
    from icc_bot.discover import parse_expiry, perp_multiplier, pick_front_month

    assert parse_expiry("GOL-25NOV26-CDE") == datetime(2026, 11, 25, tzinfo=timezone.utc)
    assert parse_expiry("BTC-PERP") is None

    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    products = [
        {"product_id": "GOL-25OCT26-CDE", "future_product_details": {"contract_size": "1"}},
        {"product_id": "GOL-25NOV26-CDE", "future_product_details": {"contract_size": "1"}},
        {"product_id": "GOL-25AUG26-CDE", "future_product_details": {"contract_size": "1"}},  # expired
        {"product_id": "BIT-28NOV26-CDE", "future_product_details": {"contract_size": "0.01"}},
    ]
    front = pick_front_month(products, "GOL", now=now)
    assert front["product_id"] == "GOL-25OCT26-CDE"      # nearest not-yet-expired
    assert pick_front_month(products, "NOL", now=now) is None
    assert perp_multiplier("BTC-PERP") == 1.0
    assert perp_multiplier("1000SHIB-PERP") == 1000.0


def test_dry_run_broker_never_sends():
    b = DryRunBroker(equity=5_000)
    from icc_bot.models import Order, Side
    res = b.place_order(Order(symbol="BTC-USD", side=Side.BUY, qty=1.0))
    assert res["status"] == "dry_run" and len(b.placed) == 1


def test_dry_run_broker_bracket_records_exits():
    b = DryRunBroker(equity=5_000)
    from icc_bot.models import Order, Side
    res = b.place_bracket(Order(symbol="BTC-USD", side=Side.BUY, qty=1.0,
                                stop_price=95.0, take_profit=115.0))
    assert res["status"] == "dry_run" and res["bracket"] is True
    assert b.placed[0].stop_price == 95.0 and b.placed[0].take_profit == 115.0
