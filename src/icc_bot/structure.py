"""Market-structure primitives: swing detection and trend classification.

The SCI course treats swing highs/lows as a *visual* cue. We pin that down to a
concrete, deterministic definition: a **fractal pivot**. A swing high at index i
is a bar whose high is greater than the highs of `lookback` bars on each side
(strictly greater to the left, >= to the right so flat tops still resolve). Swing
lows are the mirror. This is the same idea as TradingView's "Pivot Points High
Low" indicator the trader references in Lesson 10.
"""
from __future__ import annotations

from typing import List, Optional

from .models import Bar, Direction, Swing, SwingType


def find_swings(bars: List[Bar], lookback: int = 2) -> List[Swing]:
    """Return confirmed pivots, oldest first.

    A pivot at index i is only *confirmed* once `lookback` bars exist after it,
    so the most recent `lookback` bars can never be pivots yet (no repainting).
    """
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    swings: List[Swing] = []
    n = len(bars)
    for i in range(lookback, n - lookback):
        b = bars[i]
        left = bars[i - lookback:i]
        right = bars[i + 1:i + 1 + lookback]

        is_high = all(b.high > x.high for x in left) and all(b.high >= x.high for x in right)
        if is_high:
            swings.append(Swing(SwingType.HIGH, i, b.ts, b.high))
            continue

        is_low = all(b.low < x.low for x in left) and all(b.low <= x.low for x in right)
        if is_low:
            swings.append(Swing(SwingType.LOW, i, b.ts, b.low))
    return swings


def last_swing(swings: List[Swing], type_: SwingType) -> Optional[Swing]:
    for s in reversed(swings):
        if s.type == type_:
            return s
    return None


def classify_trend(swings: List[Swing]) -> Optional[Direction]:
    """Classify trend from the last two highs and last two lows.

    Uptrend  = higher high AND higher low (LONG).
    Downtrend = lower high AND lower low (SHORT).
    Anything else (mixed / equal) is undefined -> None (a 'no-trade' condition,
    matching the course's 'no trade zone' idea).
    """
    highs = [s for s in swings if s.type == SwingType.HIGH]
    lows = [s for s in swings if s.type == SwingType.LOW]
    if len(highs) < 2 or len(lows) < 2:
        return None

    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price

    if hh and hl:
        return Direction.LONG
    if lh and ll:
        return Direction.SHORT
    return None
