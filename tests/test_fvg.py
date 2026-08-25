"""Deterministic tests for the FVG strategy engine."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.fvg import FVGStrategy  # noqa: E402
from bot.trend import Candle  # noqa: E402


def _c(t, o, h, l, cl):
    return Candle(time=t * 60, open=o, high=h, low=l, close=cl, volume=1.0)


def test_bullish_fvg_enter_and_target_exit():
    s = FVGStrategy(rr=1.0, buffer=0.0)
    seq = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 102, 99, 101),
        _c(2, 106, 109, 105, 108),   # low 105 > high[0]=101 -> bullish FVG
        _c(3, 108, 116, 107, 115),   # hits target 115
    ]
    sigs = [s.update(c) for c in seq]
    assert sigs[2].action == "enter" and sigs[2].side == "long"
    assert abs(sigs[2].stop - 101) < 1e-9
    assert abs(sigs[2].target - 115) < 1e-9   # entry 108 + 1*(108-101)
    assert sigs[3].action == "exit" and sigs[3].reason == "target"
    print("bullish enter/target OK")


def test_bullish_fvg_stop_exit():
    s = FVGStrategy(rr=2.0, buffer=0.0)
    seq = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 102, 99, 101),
        _c(2, 106, 109, 105, 108),   # enter long @108 stop=101
        _c(3, 105, 106, 100, 102),   # low 100 <= stop 101 -> stop out
    ]
    sigs = [s.update(c) for c in seq]
    assert sigs[2].action == "enter"
    assert sigs[3].action == "exit" and sigs[3].reason == "stop"
    print("bullish stop OK")


def test_bearish_fvg_short_when_allowed():
    s = FVGStrategy(rr=1.0, allow_short=True)
    seq = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 101, 98, 99),
        _c(2, 96, 97, 94, 95),       # high 97 < low[0]=99 -> bearish FVG, short @95 stop=99
        _c(3, 95, 96, 90, 91),       # low 90 <= target 91 -> target
    ]
    sigs = [s.update(c) for c in seq]
    assert sigs[2].action == "enter" and sigs[2].side == "short"
    assert abs(sigs[2].stop - 99) < 1e-9
    assert abs(sigs[2].target - 91) < 1e-9    # 95 - 1*(99-95)
    assert sigs[3].action == "exit" and sigs[3].reason == "target"
    print("bearish short OK")


def test_bearish_fvg_skipped_on_spot():
    s = FVGStrategy(allow_short=False)
    seq = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 101, 98, 99),
        _c(2, 96, 97, 94, 95),       # bearish FVG but shorting disabled
    ]
    sigs = [s.update(c) for c in seq]
    assert sigs[2].action == "none"
    assert s.position is None
    print("bearish skip on spot OK")


def test_max_hold_time_stop():
    s = FVGStrategy(rr=100.0, buffer=0.0, max_hold=2)  # target unreachable -> time stop
    seq = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 102, 99, 101),
        _c(2, 106, 109, 105, 108),   # enter long
        _c(3, 108, 109, 107, 108),   # hold 1
        _c(4, 108, 109, 107, 108),   # hold 2 -> max_hold exit
    ]
    sigs = [s.update(c) for c in seq]
    assert sigs[2].action == "enter"
    assert sigs[4].action == "exit" and sigs[4].reason == "max_hold"
    print("max_hold OK")


def test_no_gap_no_signal():
    s = FVGStrategy()
    seq = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 101, 99, 100),
        _c(2, 100, 101, 99, 100),    # no gap
    ]
    sigs = [s.update(c) for c in seq]
    assert all(sig.action == "none" for sig in sigs)
    print("no-gap OK")


def test_min_gap_frac_filters_tiny_gaps():
    # tiny gap (0.5 over a ~100 price = 0.5%); require 1% -> filtered out
    s = FVGStrategy(min_gap_frac=0.01)
    seq = [
        _c(0, 100, 101, 99, 100),
        _c(1, 100, 102, 99, 101),
        _c(2, 102, 103, 101.5, 102),  # low 101.5 > high[0]=101 but gap 0.5 < 1% of 102
    ]
    sigs = [s.update(c) for c in seq]
    assert sigs[2].action == "none"
    print("min_gap_frac OK")


if __name__ == "__main__":
    test_bullish_fvg_enter_and_target_exit()
    test_bullish_fvg_stop_exit()
    test_bearish_fvg_short_when_allowed()
    test_bearish_fvg_skipped_on_spot()
    test_max_hold_time_stop()
    test_no_gap_no_signal()
    test_min_gap_frac_filters_tiny_gaps()
    print("all FVG tests passed")
