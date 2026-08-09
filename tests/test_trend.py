"""Unit tests for the market-structure trend engine."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.trend import Candle, Trend, analyze  # noqa: E402


def _mk(prices):
    """Build candles where high/low straddle the close so pivots are clean."""
    out = []
    for i, p in enumerate(prices):
        out.append(Candle(time=i, open=p, high=p + 1, low=p - 1, close=p))
    return out


def test_uptrend_hh_hl():
    # Rising zig-zag: each swing high and swing low is higher than the last.
    prices = [10, 12, 8, 14, 9, 18, 13, 22, 16, 26, 20, 30]
    res = analyze(_mk(prices), lookback=1)
    assert res.trend == Trend.UP
    assert res.trend.label() == "UPTREND"


def test_downtrend_lh_ll():
    prices = [30, 20, 26, 16, 22, 13, 18, 9, 14, 8, 12, 6]
    res = analyze(_mk(prices), lookback=1)
    assert res.trend == Trend.DOWN
    assert res.trend.label() == "DOWNTREND"


def test_trend_flips_only_when_both_legs_break():
    # Up first, then a lower high AND lower low should flip it to DOWN.
    up = [10, 12, 8, 14, 9, 18, 13, 22, 16, 26, 20, 30]
    down = [24, 15, 20, 11, 16, 7]   # lower highs + lower lows
    res = analyze(_mk(up + down), lookback=1)
    assert res.trend == Trend.DOWN


def test_neutral_when_insufficient_structure():
    res = analyze(_mk([10, 11, 12]), lookback=2)
    assert res.trend == Trend.NEUTRAL


if __name__ == "__main__":
    test_uptrend_hh_hl()
    test_downtrend_lh_ll()
    test_trend_flips_only_when_both_legs_break()
    test_neutral_when_insufficient_structure()
    print("all trend tests passed")
