"""Alex's market-structure strategy — the pure, testable engine.

Encodes the method taught in the ALEX transcript as concrete, deterministic
rules so it can be automated and unit-tested with no network or credentials:

    1. TREND (market structure): bullish = higher highs + higher lows,
       bearish = lower highs + lower lows. Trade only with the trend.
    2. AREA OF INTEREST (AOI): a horizontal zone touched >= 3 times (support
       below price when bullish, resistance above price when bearish). Price
       must have retraced back INTO the zone.
    3. ENTRY SIGNAL at the zone, in the trend direction: a REJECTION candle
       (long wick into the zone, close back out) or an ENGULFING candle.

`evaluate()` ties them together and returns a Signal with entry / stop /
take-profit. Everything here is broker-agnostic and side-effect free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Sequence

EPS = 1e-9


class Trend(IntEnum):
    DOWN = -1
    NEUTRAL = 0
    UP = 1

    def label(self) -> str:
        return {Trend.UP: "UPTREND", Trend.DOWN: "DOWNTREND",
                Trend.NEUTRAL: "NEUTRAL"}[self]


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open


@dataclass
class Zone:
    """A horizontal area of interest built from clustered swing pivots."""
    low: float
    high: float
    touches: int
    kind: str  # "support" | "resistance"

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0

    @property
    def height(self) -> float:
        return self.high - self.low

    def contains(self, price: float, buffer: float = 0.0) -> bool:
        return (self.low - buffer) <= price <= (self.high + buffer)


@dataclass
class Params:
    pivot_lookback: int = 3        # bars each side that define a swing pivot
    aoi_lookback: int = 150        # how far back to gather pivots for zones
    aoi_tol_frac: float = 0.0015   # cluster width: prices within 0.15% merge
    min_touches: int = 3           # Alex: a valid AOI has 3+ touches
    wick_ratio: float = 1.5        # rejection wick must be >= 1.5x the body
    rr: float = 2.0                # take-profit = rr * risk (2:1 default)
    stop_buffer_frac: float = 0.0015  # breathing room beyond the zone


@dataclass
class Signal:
    action: str                    # "buy" | "sell" | "none"
    trend: Trend
    price: float
    reason: str
    zone: Zone | None = None
    entry: float | None = None
    stop: float | None = None
    take_profit: float | None = None
    pattern: str | None = None     # "rejection" | "engulfing"


# ---------------------------------------------------------------------------
# 1. Market structure / trend
# ---------------------------------------------------------------------------

def pivot_highs(candles: Sequence[Candle], lookback: int) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        h = candles[i].high
        if all(h > candles[i - k].high for k in range(1, lookback + 1)) and all(
            h >= candles[i + k].high for k in range(1, lookback + 1)
        ):
            out.append((i, h))
    return out


def pivot_lows(candles: Sequence[Candle], lookback: int) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    n = len(candles)
    for i in range(lookback, n - lookback):
        low = candles[i].low
        if all(low < candles[i - k].low for k in range(1, lookback + 1)) and all(
            low <= candles[i + k].low for k in range(1, lookback + 1)
        ):
            out.append((i, low))
    return out


def trend_of(candles: Sequence[Candle], lookback: int = 3) -> Trend:
    """Confirmed trend from swing structure (both legs must break to flip)."""
    highs = pivot_highs(candles, lookback)
    lows = pivot_lows(candles, lookback)
    events = [(i, "H", p) for i, p in highs] + [(i, "L", p) for i, p in lows]
    events.sort(key=lambda e: e[0])

    last_ph = prev_ph = last_pl = prev_pl = None
    state = Trend.NEUTRAL
    for _idx, kind, price in events:
        if kind == "H":
            prev_ph, last_ph = last_ph, price
        else:
            prev_pl, last_pl = last_pl, price
        hh = last_ph is not None and prev_ph is not None and last_ph > prev_ph
        lh = last_ph is not None and prev_ph is not None and last_ph < prev_ph
        hl = last_pl is not None and prev_pl is not None and last_pl > prev_pl
        ll = last_pl is not None and prev_pl is not None and last_pl < prev_pl
        if hh and hl and state != Trend.UP:
            state = Trend.UP
        elif lh and ll and state != Trend.DOWN:
            state = Trend.DOWN
    return state


# ---------------------------------------------------------------------------
# 2. Area of interest (3+ touch horizontal zone)
# ---------------------------------------------------------------------------

def _cluster(prices: list[float], tol_frac: float) -> list[list[float]]:
    """Greedily group prices that sit within tol_frac of the cluster anchor."""
    clusters: list[list[float]] = []
    for p in sorted(prices):
        if clusters and abs(p - clusters[-1][0]) <= clusters[-1][0] * tol_frac:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return clusters


def find_zones(candles: Sequence[Candle], params: Params) -> list[Zone]:
    """All valid AOIs (clusters of swing pivots with >= min_touches)."""
    window = candles[-params.aoi_lookback:]
    highs = [p for _i, p in pivot_highs(window, params.pivot_lookback)]
    lows = [p for _i, p in pivot_lows(window, params.pivot_lookback)]
    zones: list[Zone] = []
    # Highs cluster into resistance, lows into support; either can be an AOI.
    for prices, kind in ((lows, "support"), (highs, "resistance")):
        for members in _cluster(prices, params.aoi_tol_frac):
            if len(members) >= params.min_touches:
                zones.append(Zone(low=min(members), high=max(members),
                                  touches=len(members), kind=kind))
    return zones


def active_zone(candles: Sequence[Candle], entry_candle: Candle, side: str,
                params: Params) -> Zone | None:
    """The AOI the latest candle is retracing INTO, on the correct side.

    A pullback is the candle's WICK tagging the zone (Alex: "wait for price to
    come back into this area"), not the close sitting inside it.
      side "long"  -> a support zone at/below price whose top the candle's LOW
                      has reached.
      side "short" -> a resistance zone at/above price whose bottom the candle's
                      HIGH has reached.
    Nearest tagged zone wins (tie-break: more touches).
    """
    price = entry_candle.close
    buffer = price * params.stop_buffer_frac
    candidates: list[Zone] = []
    for z in find_zones(candles, params):
        if side == "long":
            tagged = entry_candle.low <= z.high + buffer
            correct_side = z.mid <= price * (1 + params.aoi_tol_frac)
            if tagged and correct_side:
                candidates.append(z)
        else:
            tagged = entry_candle.high >= z.low - buffer
            correct_side = z.mid >= price * (1 - params.aoi_tol_frac)
            if tagged and correct_side:
                candidates.append(z)
    if not candidates:
        return None
    anchor = entry_candle.low if side == "long" else entry_candle.high
    return min(candidates, key=lambda z: (abs(z.mid - anchor), -z.touches))


# ---------------------------------------------------------------------------
# 3. Entry signals (candlestick triggers at the zone)
# ---------------------------------------------------------------------------

def is_bullish_rejection(c: Candle, zone: Zone, params: Params) -> bool:
    """Long wick stabs into/below the support zone but closes back above it."""
    pierced = c.low <= zone.high
    closed_out = c.close > zone.low
    wick_dominant = (c.lower_wick >= params.wick_ratio * max(c.body, EPS)
                     and c.lower_wick >= c.upper_wick)
    return pierced and closed_out and wick_dominant


def is_bearish_rejection(c: Candle, zone: Zone, params: Params) -> bool:
    """Long wick stabs into/above the resistance zone but closes back below it."""
    pierced = c.high >= zone.low
    closed_out = c.close < zone.high
    wick_dominant = (c.upper_wick >= params.wick_ratio * max(c.body, EPS)
                     and c.upper_wick >= c.lower_wick)
    return pierced and closed_out and wick_dominant


def is_bullish_engulfing(prev: Candle, c: Candle) -> bool:
    return (c.bullish and prev.bearish
            and c.close >= prev.open and c.open <= prev.close)


def is_bearish_engulfing(prev: Candle, c: Candle) -> bool:
    return (c.bearish and prev.bullish
            and c.open >= prev.close and c.close <= prev.open)


def entry_pattern(entry_candles: Sequence[Candle], zone: Zone, side: str,
                  params: Params) -> str | None:
    """Return "rejection" | "engulfing" | None for the latest closed candle."""
    if len(entry_candles) < 2:
        return None
    prev, c = entry_candles[-2], entry_candles[-1]
    if side == "long":
        if is_bullish_rejection(c, zone, params):
            return "rejection"
        if is_bullish_engulfing(prev, c) and c.low <= zone.high:
            return "engulfing"
    else:
        if is_bearish_rejection(c, zone, params):
            return "rejection"
        if is_bearish_engulfing(prev, c) and c.high >= zone.low:
            return "engulfing"
    return None


# ---------------------------------------------------------------------------
# Full evaluation: trend -> AOI -> pullback -> entry signal
# ---------------------------------------------------------------------------

def evaluate(structure_candles: Sequence[Candle],
             entry_candles: Sequence[Candle],
             params: Params | None = None) -> Signal:
    params = params or Params()
    trend = trend_of(structure_candles, params.pivot_lookback)
    price = entry_candles[-1].close if entry_candles else (
        structure_candles[-1].close if structure_candles else 0.0)

    if trend == Trend.NEUTRAL:
        return Signal("none", trend, price, "no confirmed trend")

    side = "long" if trend == Trend.UP else "short"
    zone = active_zone(structure_candles, entry_candles[-1], side, params) \
        if entry_candles else None
    if zone is None:
        return Signal("none", trend, price,
                      "no valid area of interest at price (need 3+ touches, "
                      "price retraced into it)")

    pattern = entry_pattern(entry_candles, zone, side, params)
    if pattern is None:
        return Signal("none", trend, price,
                      f"at {zone.kind} zone {zone.low:g}-{zone.high:g}, "
                      "waiting for rejection/engulfing entry", zone=zone)

    buffer = price * params.stop_buffer_frac
    if side == "long":
        entry = price
        stop = zone.low - buffer
        take_profit = entry + params.rr * (entry - stop)
        action = "buy"
    else:
        entry = price
        stop = zone.high + buffer
        take_profit = entry - params.rr * (stop - entry)
        action = "sell"

    return Signal(action, trend, price,
                  f"{trend.label()} + {zone.kind} AOI ({zone.touches} touches) "
                  f"+ {pattern}", zone=zone, entry=entry, stop=stop,
                  take_profit=take_profit, pattern=pattern)
