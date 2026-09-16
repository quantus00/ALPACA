"""Fill simulation shared by paper trading and backtesting.

Works in **underlying units** with risk-%-of-equity sizing, so it measures the
strategy's edge in dollars without contract-rounding noise. One position at a
time (matching the course's focused, few-trades style). Exits are checked
against each bar's range; if a bar could hit both stop and target, the stop is
assumed first (conservative).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .models import Bar, Direction, Signal


@dataclass
class Trade:
    symbol: str
    direction: Direction
    qty: float
    entry: float
    stop: float
    target: float
    entry_ts: int
    exit: Optional[float] = None
    exit_ts: Optional[int] = None
    pnl: float = 0.0
    outcome: str = ""      # "target" | "stop" | "eod"

    @property
    def r_multiple(self) -> float:
        risk = abs(self.entry - self.stop)
        return (self.pnl / (risk * self.qty)) if risk and self.qty else 0.0


@dataclass
class Simulator:
    equity: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    start_equity: float = field(init=False)
    open_trade: Optional[Trade] = None
    trades: List[Trade] = field(default_factory=list)
    peak: float = field(init=False)
    max_drawdown_pct: float = 0.0

    def __post_init__(self) -> None:
        self.start_equity = self.equity
        self.peak = self.equity

    def size(self, signal: Signal) -> float:
        r = signal.risk_per_unit
        if r <= 0 or self.equity <= 0:
            return 0.0
        return (self.equity * self.risk_per_trade_pct / 100.0) / r

    def enter(self, signal: Signal) -> bool:
        """Auto-size from equity and open (used by the backtester)."""
        return self.enter_with(signal.symbol, signal.direction, self.size(signal),
                               signal.entry, signal.stop, signal.target, signal.ts)

    def enter_with(self, symbol: str, direction: Direction, qty: float,
                   entry: float, stop: float, target: float, ts: int) -> bool:
        """Open at a caller-supplied qty (used by paper trading, where the risk
        manager sizes against virtual equity)."""
        if self.open_trade is not None or qty <= 0:
            return False
        self.open_trade = Trade(symbol=symbol, direction=direction, qty=qty,
                                entry=entry, stop=stop, target=target, entry_ts=ts)
        return True

    def update(self, bar: Bar) -> Optional[Trade]:
        """Feed one bar; close the open trade if it hit stop or target."""
        t = self.open_trade
        if t is None:
            return None
        if t.direction is Direction.LONG:
            if bar.low <= t.stop:
                return self._close(t.stop, "stop", bar.ts)
            if bar.high >= t.target:
                return self._close(t.target, "target", bar.ts)
        else:
            if bar.high >= t.stop:
                return self._close(t.stop, "stop", bar.ts)
            if bar.low <= t.target:
                return self._close(t.target, "target", bar.ts)
        return None

    def close_at(self, price: float, ts: int) -> Optional[Trade]:
        """Force-close at a price (e.g. end of data)."""
        return self._close(price, "eod", ts) if self.open_trade else None

    def _close(self, price: float, outcome: str, ts: int) -> Trade:
        t = self.open_trade
        assert t is not None
        t.exit, t.exit_ts, t.outcome = price, ts, outcome
        move = (price - t.entry) if t.direction is Direction.LONG else (t.entry - price)
        t.pnl = move * t.qty
        self.equity += t.pnl
        self.peak = max(self.peak, self.equity)
        if self.peak > 0:
            self.max_drawdown_pct = max(self.max_drawdown_pct,
                                        (self.peak - self.equity) / self.peak * 100.0)
        self.trades.append(t)
        self.open_trade = None
        return t


def stats(sim: Simulator) -> dict:
    ts = sim.trades
    n = len(ts)
    wins = [t for t in ts if t.pnl > 0]
    losses = [t for t in ts if t.pnl < 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / n * 100, 1) if n else 0.0,
        "avg_r": round(sum(t.r_multiple for t in ts) / n, 2) if n else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "start_equity": round(sim.start_equity, 2),
        "end_equity": round(sim.equity, 2),
        "return_pct": round((sim.equity / sim.start_equity - 1) * 100, 2) if sim.start_equity else 0.0,
        "max_drawdown_pct": round(sim.max_drawdown_pct, 2),
    }
