"""Unit tests for Alex's market-structure strategy engine (alex_bot/strategy.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alex_bot.strategy import (  # noqa: E402
    Candle, Params, Trend, Zone, active_zone, entry_pattern, evaluate,
    find_zones, is_bearish_engulfing, is_bearish_rejection,
    is_bullish_engulfing, is_bullish_rejection, trend_of)


def c(o, h, l, cl, t=0):
    return Candle(time=t, open=o, high=h, low=l, close=cl)


def _line(prices):
    """Candles whose high/low straddle the close by 1 so pivots are clean."""
    return [Candle(time=i, open=p, high=p + 1, low=p - 1, close=p)
            for i, p in enumerate(prices)]


# -- trend -------------------------------------------------------------------
def test_trend_up_and_down():
    up = [10, 12, 8, 14, 9, 18, 13, 22, 16, 26, 20, 30]
    down = [30, 20, 26, 16, 22, 13, 18, 9, 14, 8, 12, 6]
    assert trend_of(_line(up), lookback=1) == Trend.UP
    assert trend_of(_line(down), lookback=1) == Trend.DOWN


# -- candle patterns ---------------------------------------------------------
def test_bullish_rejection():
    z = Zone(low=99.0, high=101.0, touches=3, kind="support")
    # long lower wick into the zone, closes back above it
    cd = c(o=101.2, h=101.5, l=98.5, cl=101.3)
    assert is_bullish_rejection(cd, z, Params())
    # no wick -> not a rejection
    assert not is_bullish_rejection(c(101, 102, 100.9, 101.8), z, Params())


def test_bearish_rejection():
    z = Zone(low=99.0, high=101.0, touches=3, kind="resistance")
    cd = c(o=98.8, h=101.5, l=98.6, cl=98.7)
    assert is_bearish_rejection(cd, z, Params())


def test_engulfing():
    prev = c(100, 100.5, 99, 99.2)      # bearish
    cur = c(99.1, 101.5, 99.0, 101.2)   # bullish, engulfs prev body
    assert is_bullish_engulfing(prev, cur)
    assert not is_bearish_engulfing(prev, cur)

    prevb = c(99, 101, 98.8, 100.8)     # bullish
    curb = c(100.9, 101.1, 98.5, 98.7)  # bearish engulf
    assert is_bearish_engulfing(prevb, curb)


# -- area of interest --------------------------------------------------------
def _zone_dataset():
    """Rise, then three clean pullbacks to ~100 support, between higher highs."""
    seq = []
    # establish a higher low then higher high (sets uptrend)
    seq += [90, 95, 88, 100, 92, 112]
    # three touches of ~100 support separated by pushes up to ~115
    for _ in range(3):
        seq += [115, 108, 100, 107, 114]
    seq += [116, 118]  # drift up, current price above the 100 zone
    return _line(seq)


def test_find_zones_detects_support():
    zones = find_zones(_zone_dataset(), Params(pivot_lookback=1, aoi_tol_frac=0.03))
    supports = [z for z in zones if z.kind == "support"]
    assert any(z.touches >= 3 and abs(z.mid - 100) < 3 for z in supports), \
        [(z.kind, round(z.mid, 1), z.touches) for z in zones]


def test_active_zone_requires_candle_tagging_zone():
    data = _zone_dataset()
    params = Params(pivot_lookback=1, aoi_tol_frac=0.03)
    # a candle up at 118 that never wicks down to the ~99 support -> not active
    assert active_zone(data, c(118, 119, 117.5, 118), side="long",
                       params=params) is None
    # a candle whose low tags the ~99 support -> active
    z = active_zone(data, c(101, 101.5, 98.8, 101), side="long", params=params)
    assert z is not None and z.kind == "support"


# -- full evaluate -----------------------------------------------------------
def test_evaluate_long_signal():
    structure = _zone_dataset()
    params = Params(pivot_lookback=1, aoi_tol_frac=0.03)
    # entry candles: prior bar, then a bullish rejection tagging the 100 zone
    entry = [c(104, 105, 100.5, 101), c(101, 101.5, 98.8, 101.2)]
    sig = evaluate(structure, entry, params)
    assert sig.action == "buy", sig.reason
    assert sig.pattern in ("rejection", "engulfing")
    assert sig.entry and sig.stop and sig.take_profit
    assert sig.stop < sig.entry < sig.take_profit          # long geometry
    # risk:reward respected (2:1 default)
    risk = sig.entry - sig.stop
    reward = sig.take_profit - sig.entry
    assert abs(reward - params.rr * risk) < 1e-6


def test_evaluate_waits_without_pattern():
    structure = _zone_dataset()
    params = Params(pivot_lookback=1, aoi_tol_frac=0.03)
    # candle low tags the ~99 zone but it's a dull candle (no rejection/engulf)
    entry = [c(99.0, 99.4, 98.98, 99.35), c(99.1, 99.3, 99.05, 99.12)]
    sig = evaluate(structure, entry, params)
    assert sig.action == "none" and "waiting" in sig.reason, sig.reason


def test_evaluate_neutral_no_trade():
    flat = _line([100, 100, 100, 100, 100])
    sig = evaluate(flat, flat, Params(pivot_lookback=1))
    assert sig.action == "none"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all alex strategy tests passed")
