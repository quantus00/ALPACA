"""Paper broker: real (live) market data, simulated fills and P&L.

Unlike dry-run (which only logs would-be orders), this keeps a virtual account:
entries fill at the signal price, and each cycle the open position is checked
against the latest bar for stop/target — so you get a real forward-test P&L
without risking money. Works in underlying units (no contract rounding).
"""
from __future__ import annotations

import logging
from typing import List, Optional

from ..models import Account, Bar, Direction, Order, Side
from ..sim import Simulator, stats
from .base import Broker

log = logging.getLogger("icc_bot.broker.paper")


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, data_source: Broker, start_equity: float = 10_000.0) -> None:
        self.data = data_source
        self.sim = Simulator(equity=start_equity)
        self._ltf: dict[str, List[Bar]] = {}
        self._risk = None   # injected by the runner so exits update the throttle

    def set_risk(self, risk) -> None:
        self._risk = risk

    # -- data (delegate to the live source) ------------------------------
    def get_account(self) -> Account:
        eq = self.sim.equity
        return Account(equity=eq, cash=eq, buying_power=eq)

    def get_bars(self, symbol: str, timeframe: str, limit: int = 300) -> List[Bar]:
        bars = self.data.get_bars(symbol, timeframe, limit)
        # run_cycle fetches htf then ltf, so the last write per symbol is the ltf.
        self._ltf[symbol] = bars
        return bars

    def get_positions(self) -> List[dict]:
        t = self.sim.open_trade
        return [{"symbol": t.symbol, "qty": t.qty, "direction": t.direction.value}] if t else []

    # -- simulated execution --------------------------------------------
    def place_order(self, order: Order) -> dict:
        return self._open(order, bracketed=False)

    def place_bracket(self, order: Order) -> dict:
        return self._open(order, bracketed=True)

    def _open(self, order: Order, bracketed: bool) -> dict:
        entry = order.meta.get("entry")
        ts = int(order.meta.get("ts", 0))
        if entry is None or order.stop_price is None or order.take_profit is None:
            return {"status": "paper_rejected", "reason": "missing entry/stop/target"}
        direction = Direction.LONG if order.side is Side.BUY else Direction.SHORT
        ok = self.sim.enter_with(order.symbol, direction, order.qty, entry,
                                 order.stop_price, order.take_profit, ts)
        if not ok:
            return {"status": "paper_skipped", "reason": "a position is already open"}
        log.info("[paper] OPEN %s %s qty=%.6f entry=%.4f stop=%.4f target=%.4f",
                 direction.value, order.symbol, order.qty, entry,
                 order.stop_price, order.take_profit)
        return {"status": "paper_filled", "equity": round(self.sim.equity, 2)}

    # -- called by the runner once per cycle ----------------------------
    def on_cycle(self) -> None:
        t = self.sim.open_trade
        if t is None:
            return
        bars = self._ltf.get(t.symbol)
        if not bars:
            return
        closed = self.sim.update(bars[-1])
        if closed is not None:
            log.info("[paper] CLOSE %s %s @%.4f pnl=%.2f (%s)  equity=%.2f",
                     closed.direction.value, closed.symbol, closed.exit,
                     closed.pnl, closed.outcome, self.sim.equity)
            if self._risk is not None:
                self._risk.register_close(closed.pnl)

    def summary(self) -> dict:
        return stats(self.sim)
