"""Prop-firm evaluation guard: trailing drawdown + daily loss + profit target.

This is the risk layer that turns a raw trading signal into something that can
actually *pass* a funded-account challenge (Topstep / Apex / TPT style) instead
of blowing it. It enforces, on every tick, the full rule set of a
trailing-drawdown evaluation:

* **Profit target** — the pass condition. Once total P&L reaches the target the
  guard halts: no new risk, lock the win in.
* **Daily loss limit** — a soft, per-day cap. Breach it and the guard flattens
  and locks out *for that trading day only*; it re-arms at the next session.
* **Trailing drawdown** — the account-killer. A high-water mark trails your
  equity up; if equity falls to ``peak - trailing_drawdown`` the account is
  failed (terminal). Supports the two ways firms compute it:
    - ``"intraday"`` : the trail follows the intraday peak *equity* (unrealized
      counts against you) — the harsher, high-water-mark rule.
    - ``"eod"``      : the trail only ratchets up on the **end-of-day closing
      balance**, so a green day you flatten into permanently locks the gain
      into your buffer.
  The floor optionally stops trailing once it reaches the starting balance
  (``lock_floor_at``), matching firms whose trail freezes at breakeven.
* **Flat by 5pm ET / no overnight** — no entries outside the configured RTH
  window, a hard flatten during the 5:00-6:00pm ET settlement/maintenance gap,
  and the thin 6pm+ session left alone unless explicitly enabled.
* **Anti-oversizing** — :func:`max_contracts` / :meth:`ChallengeGuard.size_for`
  cap position size to the *smaller* of the remaining daily and trailing
  buffers, so a single stop-out can never breach a limit.

The guard is pure and broker-agnostic: feed it a timezone-aware (or ET-naive)
``datetime``, the current account **equity in dollars** (realized + unrealized)
and the open position size; it returns a :class:`RiskDecision`. Converting
instrument P&L to dollars is :func:`pnl_dollars` (point values below).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

try:  # stdlib on 3.9+, but degrade gracefully if the tz database is missing.
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - very old runtimes
    ZoneInfo = None  # type: ignore


# ---------------------------------------------------------------------------
# Instrument point values (USD per 1.00 point of price move, per contract).
# ---------------------------------------------------------------------------
POINT_VALUES: dict[str, float] = {
    "MES": 5.0,     # Micro E-mini S&P 500
    "ES": 50.0,     # E-mini S&P 500
    "MNQ": 2.0,     # Micro E-mini Nasdaq-100
    "NQ": 20.0,     # E-mini Nasdaq-100
    "MYM": 0.5,     # Micro E-mini Dow
    "YM": 5.0,      # E-mini Dow
    "M2K": 5.0,     # Micro E-mini Russell 2000
    "RTY": 50.0,    # E-mini Russell 2000
    "MGC": 10.0,    # Micro Gold (0.10 tick = $1.00/tick)
    "GC": 100.0,    # Full-size Gold (0.10 tick = $10.00/tick)
}


def point_value(symbol: str) -> float:
    """USD value of a 1.0 point move for one contract of ``symbol``."""
    sym = symbol.upper()
    if sym not in POINT_VALUES:
        raise KeyError(f"Unknown point value for {symbol!r}; add it to POINT_VALUES")
    return POINT_VALUES[sym]


def pnl_dollars(entry: float, exit_: float, side: str, contracts: int,
                symbol: str) -> float:
    """Signed dollar P&L for a round-trip trade.

    ``side`` is the direction of the *entry* ('buy'/'long' or 'sell'/'short').
    """
    pv = point_value(symbol)
    points = exit_ - entry
    if side in ("sell", "short"):
        points = -points
    return points * pv * contracts


def max_contracts(stop_points: float, symbol: str, risk_dollars: float) -> int:
    """Largest whole contract count whose worst-case loss stays within
    ``risk_dollars`` given a ``stop_points`` protective stop. Never negative."""
    if stop_points <= 0:
        raise ValueError("stop_points must be > 0")
    if risk_dollars <= 0:
        return 0
    risk_per_contract = stop_points * point_value(symbol)
    return max(0, math.floor(risk_dollars / risk_per_contract))


# ---------------------------------------------------------------------------
# Challenge parameters.
# ---------------------------------------------------------------------------
@dataclass
class ChallengeParams:
    """The rulebook. Defaults mirror the challenge described by the user:
    +$1,500 target, $500 daily loss, $1,000 trailing drawdown, flat by 5pm ET."""

    starting_balance: float = 50_000.0
    profit_target: float = 1_500.0
    daily_loss_limit: float = 500.0
    trailing_drawdown: float = 1_000.0

    #: "intraday" (high-water mark on equity) or "eod" (trails on closing balance).
    trailing_mode: str = "intraday"

    #: Cap the trailing floor here once reached (firms whose trail freezes at
    #: breakeven). ``None`` = trail forever. Defaults to the starting balance.
    lock_floor_at: float | None = None

    #: Fraction of the *smaller* remaining buffer to risk on any one trade.
    risk_fraction: float = 0.5

    # ---- Session clock (all times ET) ---------------------------------------
    timezone: str = "America/New_York"
    #: Windows in which NEW entries are allowed. Default: RTH up to the no-new
    #: cutoff. Overnight (6pm+) is intentionally excluded.
    entry_windows: tuple[tuple[time, time], ...] = field(
        default_factory=lambda: ((time(9, 30), time(16, 55)),)
    )
    force_flat_time: time = time(17, 0)      # hard flat by 5pm ET
    reopen_time: time = time(18, 0)          # CME maintenance ends, session reopens
    #: The trading day rolls over at this ET time (futures session open).
    day_reset_time: time = time(18, 0)
    flatten_on_target: bool = True           # lock the win in when target is hit

    def __post_init__(self) -> None:
        if self.trailing_mode not in ("intraday", "eod"):
            raise ValueError("trailing_mode must be 'intraday' or 'eod'")
        if self.lock_floor_at is None:
            # By default the trail freezes at the starting balance (you can never
            # be forced below breakeven once you've banked the buffer).
            self.lock_floor_at = self.starting_balance
        for lo, hi in self.entry_windows:
            if lo >= hi:
                raise ValueError(f"entry window {lo}-{hi} is not ordered")


@dataclass
class RiskDecision:
    """What the guard permits on this tick, plus observability fields."""

    can_enter: bool
    must_flatten: bool
    halted: bool             # terminal: account failed OR target reached
    day_locked: bool         # daily loss hit; clears at the next session
    target_reached: bool
    reason: str

    equity: float
    total_pnl: float         # equity - starting_balance
    daily_pnl: float         # equity - day_start_equity
    peak: float
    floor: float             # trailing-drawdown liquidation level
    buffer_to_floor: float   # equity - floor  (points/$ left before failure)
    buffer_to_daily: float   # daily_loss_limit + daily_pnl (left before lockout)


class ChallengeGuard:
    """Stateful, tick-by-tick enforcer of a trailing-drawdown evaluation.

    Call :meth:`update` once per tick (or per bar) with the current time,
    account equity and open position size. Read the returned
    :class:`RiskDecision` before acting on any strategy signal.
    """

    def __init__(self, params: ChallengeParams | None = None):
        self.p = params or ChallengeParams()
        self._tz = ZoneInfo(self.p.timezone) if ZoneInfo else None

        self.peak: float = self.p.starting_balance
        self.day_start_equity: float = self.p.starting_balance
        self._day: date | None = None
        self._eod_locked_today: bool = False   # EOD peak already bumped this session

        # Terminal / soft states.
        self.failed: bool = False              # trailing drawdown breached
        self.target_reached: bool = False
        self.day_locked: bool = False

    # -- time helpers ---------------------------------------------------------
    def _to_et(self, now: datetime) -> datetime:
        if now.tzinfo is not None and self._tz is not None:
            return now.astimezone(self._tz)
        return now  # naive input is assumed to already be ET

    def _trading_day(self, now_et: datetime) -> date:
        """The session date. The evening session (>= day_reset_time) belongs to
        the *next* calendar day's trading day."""
        d = now_et.date()
        if now_et.time() >= self.p.day_reset_time:
            d = d + timedelta(days=1)
        return d

    def _in_entry_window(self, t: time) -> bool:
        return any(lo <= t < hi for lo, hi in self.p.entry_windows)

    def _in_flat_window(self, t: time) -> bool:
        """The forced-flat / maintenance gap: [force_flat_time, reopen_time).

        Handles a window that does not wrap midnight (the normal 17:00-18:00)."""
        return self.p.force_flat_time <= t < self.p.reopen_time

    # -- floor ----------------------------------------------------------------
    def _floor(self) -> float:
        floor = self.peak - self.p.trailing_drawdown
        if self.p.lock_floor_at is not None:
            floor = min(floor, self.p.lock_floor_at)
        return floor

    # -- main tick ------------------------------------------------------------
    def update(self, now: datetime, equity: float, position: float = 0.0) -> RiskDecision:
        now_et = self._to_et(now)
        day = self._trading_day(now_et)
        t = now_et.time()

        # --- session rollover: reset the per-day soft state. ------------------
        if self._day is None or day != self._day:
            self._day = day
            self.day_start_equity = equity
            self.day_locked = False
            self._eod_locked_today = False

        # --- trailing high-water mark update. --------------------------------
        if self.p.trailing_mode == "intraday":
            # Unrealized peaks count: trail follows equity continuously.
            self.peak = max(self.peak, equity)
        else:  # "eod": only ratchet on the end-of-day closing balance.
            if self._in_flat_window(t) and not self._eod_locked_today:
                # At/after the 5pm close we are (or must be) flat, so equity is
                # the closing balance. Lock it into the trail once per session.
                self.peak = max(self.peak, equity)
                self._eod_locked_today = True

        floor = self._floor()
        total_pnl = equity - self.p.starting_balance
        daily_pnl = equity - self.day_start_equity

        # --- terminal / soft breach checks (evaluated in priority order). ----
        if not self.failed and equity <= floor:
            self.failed = True
        if not self.target_reached and total_pnl >= self.p.profit_target:
            self.target_reached = True
        if not self.day_locked and daily_pnl <= -self.p.daily_loss_limit:
            self.day_locked = True

        in_flat_window = self._in_flat_window(t)
        in_entry_window = self._in_entry_window(t)
        halted = self.failed or self.target_reached

        # --- assemble the decision. ------------------------------------------
        must_flatten = False
        can_enter = False
        reason = "ok"

        if self.failed:
            must_flatten, reason = True, "TRAILING DRAWDOWN BREACHED — account failed"
        elif self.target_reached:
            must_flatten = self.p.flatten_on_target
            reason = "PROFIT TARGET reached — challenge passed, stop trading"
        elif self.day_locked:
            must_flatten, reason = True, "DAILY LOSS LIMIT hit — flat and done for the day"
        elif in_flat_window:
            must_flatten, reason = True, "FLAT BY 5PM ET — settlement/maintenance window"
        else:
            # No breach and outside the flat window: may we open a new trade?
            if not in_entry_window:
                reason = "outside entry window (no overnight / after cutoff)"
            elif position != 0:
                can_enter, reason = True, "in position, entries allowed"
            else:
                can_enter, reason = True, "clear to enter"

        return RiskDecision(
            can_enter=can_enter,
            must_flatten=must_flatten,
            halted=halted,
            day_locked=self.day_locked,
            target_reached=self.target_reached,
            reason=reason,
            equity=equity,
            total_pnl=total_pnl,
            daily_pnl=daily_pnl,
            peak=self.peak,
            floor=floor,
            buffer_to_floor=equity - floor,
            buffer_to_daily=self.p.daily_loss_limit + daily_pnl,
        )

    # -- sizing ---------------------------------------------------------------
    def size_for(self, decision: RiskDecision, stop_points: float, symbol: str) -> int:
        """Contracts to trade given a ``stop_points`` stop, sized so one stop-out
        cannot breach the daily loss cap or the trailing floor.

        Risks ``risk_fraction`` of the *smaller* remaining buffer.
        """
        if not decision.can_enter:
            return 0
        headroom = min(decision.buffer_to_daily, decision.buffer_to_floor)
        risk_dollars = headroom * self.p.risk_fraction
        return max_contracts(stop_points, symbol, risk_dollars)
