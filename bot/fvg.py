"""Fair Value Gap (FVG) strategy engine — pure, deterministic, testable.

A 3-bar Fair Value Gap (a.k.a. imbalance) on the current timeframe:

    Bullish FVG at bar i:  low[i]  > high[i-2]   (a gap the market skipped up through)
    Bearish FVG at bar i:  high[i] < low[i-2]    (a gap it skipped down through)

Strategy (momentum in the direction of the gap):

    * A bullish FVG opens a LONG at the close of bar i.
    * A bearish FVG opens a SHORT at the close of bar i (skipped when shorting
      is disabled, e.g. spot-only accounts).
    * Stop = the far edge of the gap (bullish: high[i-2]; bearish: low[i-2]),
      widened by ``buffer`` price units.
    * Target = entry +/- rr * risk, where risk = |entry - stop|.
    * Exit on stop, target, or an optional ``max_hold`` bar count.

The engine is fed ONE CLOSED candle at a time via :meth:`update` so live and
backtest behaviour are identical: the same sequence of candles yields the same
signals regardless of how they are delivered.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .trend import Candle


@dataclass
class FVGSignal:
    action: str                       # "enter" | "exit" | "none"
    side: Optional[str] = None        # "long" | "short"
    price: float = 0.0                # entry/exit reference price (candle close or level)
    stop: Optional[float] = None
    target: Optional[float] = None
    reason: str = ""                  # bullish_fvg | bearish_fvg | stop | target | max_hold


@dataclass
class _Position:
    side: str          # "long" | "short"
    entry: float
    stop: float
    target: float
    bars_held: int = 0


class FVGStrategy:
    """Stateful FVG state machine. Feed closed candles in order via ``update``."""

    def __init__(
        self,
        rr: float = 2.0,
        buffer: float = 0.0,
        max_hold: int = 0,
        allow_short: bool = True,
        min_gap_frac: float = 0.0,
    ) -> None:
        self.rr = rr
        self.buffer = buffer
        self.max_hold = max_hold           # 0 = no time stop
        self.allow_short = allow_short
        self.min_gap_frac = min_gap_frac   # ignore gaps smaller than this fraction of price
        self._c1: Optional[Candle] = None  # bar i-2
        self._c2: Optional[Candle] = None  # bar i-1
        self.position: Optional[_Position] = None

    # ------------------------------------------------------------------ helpers
    def _detect(self, c0: Candle) -> Optional[tuple[str, float]]:
        """Return (side, stop_level) if a fresh FVG forms on ``c0`` (bar i)."""
        c_2, c_1 = self._c1, self._c2
        if c_2 is None or c_1 is None:
            return None
        # Bullish: current low gaps above the high two bars back.
        if c0.low > c_2.high:
            gap = c0.low - c_2.high
            if gap >= self.min_gap_frac * c0.close:
                return "long", c_2.high
        # Bearish: current high gaps below the low two bars back.
        if c0.high < c_2.low:
            gap = c_2.low - c0.high
            if gap >= self.min_gap_frac * c0.close:
                return "short", c_2.low
        return None

    def _open(self, side: str, entry: float, stop_level: float) -> FVGSignal:
        if side == "long":
            stop = stop_level - self.buffer
            risk = entry - stop
            target = entry + self.rr * risk
        else:
            stop = stop_level + self.buffer
            risk = stop - entry
            target = entry - self.rr * risk
        self.position = _Position(side=side, entry=entry, stop=stop, target=target)
        return FVGSignal(action="enter", side=side, price=entry, stop=stop,
                         target=target, reason=f"{'bullish' if side == 'long' else 'bearish'}_fvg")

    def _close(self, price: float, reason: str) -> FVGSignal:
        side = self.position.side if self.position else None
        self.position = None
        return FVGSignal(action="exit", side=side, price=price, reason=reason)

    # -------------------------------------------------------------------- update
    def update(self, c0: Candle) -> FVGSignal:
        """Process the latest CLOSED candle and return the resulting signal.

        Exit checks run before entry checks, so a bar that closes a position does
        not also open a new one on the same bar. Stop is checked before target
        (the conservative assumption when a single bar spans both)."""
        sig = FVGSignal(action="none")

        if self.position is not None:
            pos = self.position
            pos.bars_held += 1
            if pos.side == "long":
                if c0.low <= pos.stop:
                    sig = self._close(pos.stop, "stop")
                elif c0.high >= pos.target:
                    sig = self._close(pos.target, "target")
            else:  # short
                if c0.high >= pos.stop:
                    sig = self._close(pos.stop, "stop")
                elif c0.low <= pos.target:
                    sig = self._close(pos.target, "target")
            if sig.action == "none" and self.max_hold and pos.bars_held >= self.max_hold:
                sig = self._close(c0.close, "max_hold")
        else:
            hit = self._detect(c0)
            if hit is not None:
                side, stop_level = hit
                if side == "short" and not self.allow_short:
                    sig = FVGSignal(action="none", reason="short_skipped_spot")
                else:
                    sig = self._open(side, c0.close, stop_level)

        # slide the 3-bar window forward
        self._c1, self._c2 = self._c2, c0
        return sig
