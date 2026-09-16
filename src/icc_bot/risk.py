"""Risk management: position sizing and hard safety limits.

These limits are the bot's seatbelts. They are enforced before any order is
sent, in every mode. Defaults are conservative.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import Account, Signal


@dataclass
class RiskLimits:
    risk_per_trade_pct: float = 1.0     # % of equity risked per trade
    max_daily_loss_pct: float = 3.0     # stop trading for the day past this drawdown
    max_open_positions: int = 1         # concurrent positions (course: focus, few trades)
    max_trades_per_day: int = 3         # course: ~2 good trades a day at most
    max_position_pct: float = 25.0      # cap notional as % of equity (leverage guard)


@dataclass
class RiskState:
    day: str = ""                       # YYYY-MM-DD of the current session
    start_equity: float = 0.0
    realized_pnl_today: float = 0.0
    trades_today: int = 0
    open_positions: int = 0
    halted: bool = False                # kill switch / daily-loss tripped
    halt_reason: str = ""


class RiskManager:
    def __init__(self, limits: Optional[RiskLimits] = None) -> None:
        self.limits = limits or RiskLimits()
        self.state = RiskState()

    # -- session bookkeeping ---------------------------------------------
    def start_day(self, day: str, equity: float) -> None:
        self.state = RiskState(day=day, start_equity=equity)

    def kill(self, reason: str) -> None:
        self.state.halted = True
        self.state.halt_reason = reason

    # -- gates ------------------------------------------------------------
    def can_trade(self, account: Account) -> tuple[bool, str]:
        s, lim = self.state, self.limits
        if s.halted:
            return False, f"halted: {s.halt_reason}"
        if s.open_positions >= lim.max_open_positions:
            return False, "max open positions reached"
        if s.trades_today >= lim.max_trades_per_day:
            return False, "max trades per day reached"
        if s.start_equity > 0:
            dd_pct = (s.start_equity - account.equity) / s.start_equity * 100.0
            if dd_pct >= lim.max_daily_loss_pct:
                self.kill(f"daily loss limit hit ({dd_pct:.2f}%)")
                return False, self.state.halt_reason
        return True, "ok"

    def position_size(self, account: Account, signal: Signal) -> float:
        """Units to trade so that hitting the stop loses risk_per_trade_pct of equity.

        Also capped by max_position_pct notional. Returns 0.0 if no valid size.
        """
        risk_per_unit = signal.risk_per_unit
        if risk_per_unit <= 0 or account.equity <= 0:
            return 0.0
        risk_budget = account.equity * (self.limits.risk_per_trade_pct / 100.0)
        qty = risk_budget / risk_per_unit

        notional_cap = account.equity * (self.limits.max_position_pct / 100.0)
        if signal.entry > 0:
            qty = min(qty, notional_cap / signal.entry)
        return max(qty, 0.0)

    # -- fills ------------------------------------------------------------
    def register_open(self) -> None:
        self.state.trades_today += 1
        self.state.open_positions += 1

    def register_close(self, realized_pnl: float) -> None:
        self.state.open_positions = max(0, self.state.open_positions - 1)
        self.state.realized_pnl_today += realized_pnl
