"""Market-structure trend engine (mirrors the Pine Script logic).

A swing high/low is a pivot: a bar whose high/low is the extreme over
``lookback`` bars on each side. From the last two swing highs and last two
swing lows we classify the trend:

    UPTREND   = Higher High  AND Higher Low
    DOWNTREND = Lower High    AND Lower Low   (both legs must break)

A trend only flips when the *opposite* structure fully confirms, so an uptrend
is not broken until we see both a lower high and a lower low.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Sequence


class Trend(IntEnum):
    DOWN = -1
    NEUTRAL = 0
    UP = 1

    def label(self) -> str:
        return {Trend.UP: "UPTREND", Trend.DOWN: "DOWNTREND", Trend.NEUTRAL: "NEUTRAL"}[self]


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class TrendResult:
    trend: Trend
    flipped: bool          # did the trend flip on the most recent confirmation?
    last_swing_high: float | None
    prev_swing_high: float | None
    last_swing_low: float | None
    prev_swing_low: float | None


def _pivot_highs(candles: Sequence[Candle], lookback: int) -> list[tuple[int, float]]:
    """Return (index, price) of confirmed swing highs."""
    out: list[tuple[int, float]] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        h = candles[i].high
        if all(h > candles[i - k].high for k in range(1, lookback + 1)) and all(
            h >= candles[i + k].high for k in range(1, lookback + 1)
        ):
            out.append((i, h))
    return out


def _pivot_lows(candles: Sequence[Candle], lookback: int) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        low = candles[i].low
        if all(low < candles[i - k].low for k in range(1, lookback + 1)) and all(
            low <= candles[i + k].low for k in range(1, lookback + 1)
        ):
            out.append((i, low))
    return out


def analyze(candles: Sequence[Candle], lookback: int = 5) -> TrendResult:
    """Walk the candle series and return the current confirmed trend state.

    Pivots are merged in chronological order so that the trend flips at the bar
    where the second confirming leg prints, exactly like the Pine version.
    """
    highs = _pivot_highs(candles, lookback)
    lows = _pivot_lows(candles, lookback)

    # Merge into a single time-ordered event stream.
    events: list[tuple[int, str, float]] = [(i, "H", p) for i, p in highs]
    events += [(i, "L", p) for i, p in lows]
    events.sort(key=lambda e: e[0])

    last_ph = prev_ph = None
    last_pl = prev_pl = None
    state = Trend.NEUTRAL
    flipped = False

    for _idx, kind, price in events:
        flipped = False
        if kind == "H":
            prev_ph, last_ph = last_ph, price
        else:
            prev_pl, last_pl = last_pl, price

        higher_high = last_ph is not None and prev_ph is not None and last_ph > prev_ph
        lower_high = last_ph is not None and prev_ph is not None and last_ph < prev_ph
        higher_low = last_pl is not None and prev_pl is not None and last_pl > prev_pl
        lower_low = last_pl is not None and prev_pl is not None and last_pl < prev_pl

        if higher_high and higher_low and state != Trend.UP:
            state, flipped = Trend.UP, True
        elif lower_high and lower_low and state != Trend.DOWN:
            state, flipped = Trend.DOWN, True

    return TrendResult(
        trend=state,
        flipped=flipped,
        last_swing_high=last_ph,
        prev_swing_high=prev_ph,
        last_swing_low=last_pl,
        prev_swing_low=prev_pl,
    )
