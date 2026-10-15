"""The ICC strategy, pinned to explicit rules.

Pipeline (per the SCI course, made mechanical):

  1. STRUCTURE   Higher timeframe (e.g. 1H) trend from swings: HH+HL=LONG, LH+LL=SHORT.
                 Anything else = no-trade ("no-trade zone").
  2. INDICATION  Trend implies a fresh break of the last opposing swing (a new
                 high for LONG, new low for SHORT). The "previous push" origin
                 level is the last swing low (LONG) / high (SHORT).
  3. CORRECTION  Price pulls back but stays on the trend side of the origin level.
  4. CONTINUATION (entry)  On the lower timeframe (e.g. 15m) price flips back in
                 the trend direction (breaks the last micro swing) while still on
                 the trend side of the origin level.

Stop = the micro swing that would invalidate the flip (below the higher-low for
longs, above the lower-high for shorts). Target = the opposing HTF level or a
projected R multiple, whichever is further, guaranteeing the configured R:R.

This is a deterministic *approximation* of a discretionary method. It will not
reproduce the trader's judgment. Validate on paper before risking money.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .models import Bar, Direction, Signal, SwingType
from .structure import classify_trend, find_swings, last_swing


@dataclass
class ICCParams:
    htf_lookback: int = 2       # pivot lookback on the structure timeframe
    ltf_lookback: int = 2       # pivot lookback on the entry timeframe
    target_rr: float = 3.0      # minimum/target reward-to-risk (course uses 1:3–1:4)
    stop_buffer: float = 0.0    # optional absolute buffer added beyond the stop swing
    min_stop_distance: float = 0.0  # reject setups with a stop tighter than this


def evaluate(
    symbol: str,
    htf_bars: List[Bar],
    ltf_bars: List[Bar],
    params: Optional[ICCParams] = None,
) -> Optional[Signal]:
    """Return a Signal if a valid ICC continuation entry exists right now, else None.

    Stateless: derives everything from the supplied bars. The caller is
    responsible for not acting on a new signal while a position is already open.
    """
    p = params or ICCParams()
    if len(htf_bars) < p.htf_lookback * 2 + 3 or len(ltf_bars) < p.ltf_lookback * 2 + 3:
        return None

    htf_swings = find_swings(htf_bars, p.htf_lookback)
    trend = classify_trend(htf_swings)
    if trend is None:
        return None

    ltf_swings = find_swings(ltf_bars, p.ltf_lookback)
    close = ltf_bars[-1].close
    ts = ltf_bars[-1].ts

    if trend is Direction.LONG:
        origin = last_swing(htf_swings, SwingType.LOW)      # the "previous push"
        indication_high = last_swing(htf_swings, SwingType.HIGH)
        micro_high = last_swing(ltf_swings, SwingType.HIGH)
        micro_low = last_swing(ltf_swings, SwingType.LOW)
        if not (origin and indication_high and micro_high and micro_low):
            return None
        # Regime: still above the origin, and pulled back below the new high.
        if not (close > origin.price and close < indication_high.price):
            return None
        # Continuation trigger: break back above the last micro swing high.
        if not close > micro_high.price:
            return None
        stop = micro_low.price - p.stop_buffer
        if stop >= close:
            return None
        risk = close - stop
        if risk < p.min_stop_distance or risk <= 0:
            return None
        projected = close + p.target_rr * risk
        target = max(indication_high.price, projected)
        return Signal(Direction.LONG, symbol, close, stop, target,
                      reason="ICC long continuation", ts=ts)

    else:  # SHORT
        origin = last_swing(htf_swings, SwingType.HIGH)
        indication_low = last_swing(htf_swings, SwingType.LOW)
        micro_high = last_swing(ltf_swings, SwingType.HIGH)
        micro_low = last_swing(ltf_swings, SwingType.LOW)
        if not (origin and indication_low and micro_high and micro_low):
            return None
        if not (close < origin.price and close > indication_low.price):
            return None
        if not close < micro_low.price:
            return None
        stop = micro_high.price + p.stop_buffer
        if stop <= close:
            return None
        risk = stop - close
        if risk < p.min_stop_distance or risk <= 0:
            return None
        projected = close - p.target_rr * risk
        target = min(indication_low.price, projected)
        return Signal(Direction.SHORT, symbol, close, stop, target,
                      reason="ICC short continuation", ts=ts)
