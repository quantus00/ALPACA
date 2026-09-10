"""Opening Range Breakout (ORB) engine.

The ORB is the most rigorously documented intraday edge for index products
(Zarattini, Barbon & Aziz, *A Profitable Day Trading Strategy for the U.S.
Equity Market*, Swiss Finance Institute, 2024; and earlier work on index
futures). It fits a trailing-drawdown challenge unusually well:

* it trades the **RTH open**, where volume and clean directional moves live;
* risk is **hard-defined** — the stop sits at the opposite edge of the opening
  range, so max loss per trade is known *before* entry (the guard can size to it);
* it takes **one (or few) trades a day** — the discipline these accounts reward;
* it is **flat by the session close**, dovetailing with the flat-by-5pm rule.

Rules implemented (all parameters in :class:`ORBParams`):

1. **Opening range** = the high/low of the first ``or_minutes`` of the RTH
   session (default 15m from 09:30 ET).
2. **Entry** = a breakout of the range by ``entry_buffer_ticks``: long above the
   OR high, short below the OR low, in whichever direction breaks first.
3. **Stop** = the opposite edge of the range (``stop="range"``) or a fixed
   ``stop_points`` distance.
4. **Target** = ``target_r`` × risk (0 disables it → hold to the EOD exit).
5. **Exits** = stop, target, or the ``session_close`` end-of-day flatten.
6. **Filters** = skip the day if the range is too tight (noise, ``min_or_points``)
   or too wide (risk too large, ``max_or_points``); ``max_trades_per_day`` and a
   ``no_entry_after`` cutoff cap activity.

The engine is pure and deterministic: feed it closed bars via :meth:`ORBEngine.on_bar`
(one ET-stamped OHLC per call) and it returns an :class:`ORBSignal`. Position
sizing and the account-level rules live in :mod:`bot.risk`; this module only
decides *direction and levels*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from .risk import point_value


@dataclass
class ORBParams:
    session_open: time = time(9, 30)
    or_minutes: int = 15
    session_close: time = time(15, 55)   # end-of-day flatten (before the 5pm rule)
    no_entry_after: time = time(15, 0)   # no fresh breakouts late in the day

    tick_size: float = 0.25
    entry_buffer_ticks: float = 1.0      # breakout must clear the edge by this much

    stop: str = "range"                  # "range" = opposite OR edge, or "fixed"
    stop_points: float = 0.0             # used when stop == "fixed"
    target_r: float = 2.0                # take profit at R multiple; 0 = EOD only

    max_trades_per_day: int = 1
    allow_long: bool = True
    allow_short: bool = True

    min_or_points: float = 0.0           # skip if the range is tighter than this
    max_or_points: float = 1e12          # skip if the range is wider than this

    def __post_init__(self) -> None:
        if self.or_minutes <= 0:
            raise ValueError("or_minutes must be > 0")
        if self.stop not in ("range", "fixed"):
            raise ValueError("stop must be 'range' or 'fixed'")
        if self.stop == "fixed" and self.stop_points <= 0:
            raise ValueError("stop_points must be > 0 when stop == 'fixed'")
        if self.target_r < 0:
            raise ValueError("target_r must be >= 0")


@dataclass
class ORBSignal:
    action: str            # "none" | "enter_long" | "enter_short" | "exit"
    price: float = 0.0     # fill price for the action
    stop: float = 0.0      # protective stop (on entry)
    target: float = 0.0    # profit target (on entry; 0 if none)
    risk_points: float = 0.0   # entry-to-stop distance (on entry)
    reason: str = ""
    realized_points: float = 0.0   # signed points captured (on exit)


@dataclass
class Trade:
    day: object
    side: str              # "long" | "short"
    entry: float
    exit: float
    stop: float
    target: float
    points: float          # signed points
    outcome: str           # "target" | "stop" | "eod"


def _mins(t: time) -> int:
    return t.hour * 60 + t.minute


class ORBEngine:
    """Stateful, bar-by-bar Opening Range Breakout. One instance per symbol."""

    def __init__(self, params: ORBParams | None = None):
        self.p = params or ORBParams()
        self._reset_day(None)

    # -- per-day state --------------------------------------------------------
    def _reset_day(self, day) -> None:
        self._day = day
        self._or_high: float | None = None
        self._or_low: float | None = None
        self._or_locked = False
        self._day_skipped = False
        self._trades_today = 0
        self._pos = 0                 # -1 short, 0 flat, +1 long
        self._entry = 0.0
        self._stop = 0.0
        self._target = 0.0

    # -- helpers --------------------------------------------------------------
    def _buffer(self) -> float:
        return self.p.entry_buffer_ticks * self.p.tick_size

    def _lock_range(self) -> None:
        self._or_locked = True
        if self._or_high is None or self._or_low is None:
            self._day_skipped = True
            return
        width = self._or_high - self._or_low
        if width < self.p.min_or_points or width > self.p.max_or_points:
            self._day_skipped = True

    def _open_position(self, side: int, entry: float) -> ORBSignal:
        # ``entry`` already includes the breakout buffer; the range stop is the
        # opposite OR edge.
        if side > 0:
            stop = (self._or_low if self.p.stop == "range" else entry - self.p.stop_points)
            risk = entry - stop
            target = entry + self.p.target_r * risk if self.p.target_r > 0 else 0.0
        else:
            stop = (self._or_high if self.p.stop == "range" else entry + self.p.stop_points)
            risk = stop - entry
            target = entry - self.p.target_r * risk if self.p.target_r > 0 else 0.0
        self._pos = side
        self._entry, self._stop, self._target = entry, stop, target
        self._trades_today += 1
        return ORBSignal(
            action="enter_long" if side > 0 else "enter_short",
            price=entry, stop=stop, target=target, risk_points=risk,
            reason="opening-range breakout",
        )

    def _close_position(self, price: float, outcome: str) -> ORBSignal:
        pts = (price - self._entry) if self._pos > 0 else (self._entry - price)
        self._pos = 0
        return ORBSignal(action="exit", price=price, realized_points=pts, reason=outcome)

    # -- main entry point -----------------------------------------------------
    def on_bar(self, ts: datetime, open_: float, high: float, low: float,
               close: float) -> ORBSignal:
        """Process one closed bar (``ts`` its ET open time). Returns the action
        to take at/for this bar."""
        day = ts.date()
        if day != self._day:
            self._reset_day(day)

        m = _mins(ts.time())
        or_start = _mins(self.p.session_open)
        or_end = or_start + self.p.or_minutes

        # 1) Accumulate the opening range.
        if or_start <= m < or_end:
            self._or_high = high if self._or_high is None else max(self._or_high, high)
            self._or_low = low if self._or_low is None else min(self._or_low, low)
            return ORBSignal(action="none", reason="building opening range")

        # Before the session even opens: nothing to do.
        if m < or_start:
            return ORBSignal(action="none", reason="pre-session")

        # 2) Lock the range once the window has passed.
        if not self._or_locked:
            self._lock_range()

        # 3) Manage an open position first (exits take priority).
        if self._pos != 0:
            if m >= _mins(self.p.session_close):
                return self._close_position(close, "eod")
            if self._pos > 0:
                if low <= self._stop:
                    return self._close_position(self._stop, "stop")
                if self._target and high >= self._target:
                    return self._close_position(self._target, "target")
            else:
                if high >= self._stop:
                    return self._close_position(self._stop, "stop")
                if self._target and low <= self._target:
                    return self._close_position(self._target, "target")
            return ORBSignal(action="none", reason="holding")

        # 4) Flat: look for a breakout entry.
        if self._day_skipped:
            return ORBSignal(action="none", reason="day skipped (OR filter)")
        if self._trades_today >= self.p.max_trades_per_day:
            return ORBSignal(action="none", reason="max trades for the day")
        if m >= _mins(self.p.no_entry_after) or m >= _mins(self.p.session_close):
            return ORBSignal(action="none", reason="past entry cutoff")
        if self._or_high is None or self._or_low is None:
            return ORBSignal(action="none", reason="no opening range")

        buf = self._buffer()
        long_level = self._or_high + buf
        short_level = self._or_low - buf
        # First breakout wins; if a bar spans both, use the candle's direction.
        long_break = self.p.allow_long and high >= long_level
        short_break = self.p.allow_short and low <= short_level
        if long_break and short_break:
            if close >= open_:
                short_break = False
            else:
                long_break = False
        if long_break:
            return self._open_position(+1, long_level)
        if short_break:
            return self._open_position(-1, short_level)
        return ORBSignal(action="none", reason="no breakout yet")


def backtest(candles, symbol: str, params: ORBParams | None = None,
             contracts: int = 1) -> dict:
    """Run the engine over a series of ``(ts, o, h, l, c)`` bars and summarize.

    ``candles`` items may be tuples/lists ``(ts, o, h, l, c)`` or objects with
    ``time/open/high/low/close`` (``time`` an ET :class:`datetime`). Returns a
    dict with the trade list and net-dollar / win-rate stats for ``contracts``.
    """
    eng = ORBEngine(params)
    pv = point_value(symbol)
    trades: list[Trade] = []
    open_side = None
    entry = stop = target = 0.0

    for c in candles:
        if isinstance(c, (tuple, list)):
            ts, o, h, l, cl = c
        else:
            ts, o, h, l, cl = c.time, c.open, c.high, c.low, c.close
        sig = eng.on_bar(ts, o, h, l, cl)
        if sig.action in ("enter_long", "enter_short"):
            open_side = "long" if sig.action == "enter_long" else "short"
            entry, stop, target = sig.price, sig.stop, sig.target
        elif sig.action == "exit" and open_side is not None:
            outcome = sig.reason
            trades.append(Trade(day=ts.date(), side=open_side, entry=entry,
                                exit=sig.price, stop=stop, target=target,
                                points=sig.realized_points, outcome=outcome))
            open_side = None

    wins = [t for t in trades if t.points > 0]
    net_points = sum(t.points for t in trades)
    return {
        "trades": trades,
        "num_trades": len(trades),
        "wins": len(wins),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "net_points": net_points,
        "net_dollars": net_points * pv * contracts,
        "point_value": pv,
        "contracts": contracts,
    }
