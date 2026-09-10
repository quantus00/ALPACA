"""Deterministic tests for the Opening Range Breakout engine (bot/orb.py)."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.orb import ORBEngine, ORBParams, backtest  # noqa: E402


def _bar(h, m, o, hi, lo, c, day=10):
    return (datetime(2026, 9, day, h, m), o, hi, lo, c)


# Opening range (09:30-09:45, three 5-min bars): high 5010, low 5000, width 10.
def _or_bars(day=10):
    return [
        _bar(9, 30, 5005, 5008, 5000, 5006, day),
        _bar(9, 35, 5006, 5010, 5003, 5009, day),
        _bar(9, 40, 5009, 5010, 5005, 5008, day),
    ]


def _run(engine, bars):
    return [engine.on_bar(*b) for b in bars]


# ---------------------------------------------------------------------------
# Range construction.
# ---------------------------------------------------------------------------
def test_builds_range_then_no_entry_without_breakout():
    eng = ORBEngine()
    sigs = _run(eng, _or_bars())
    assert all(s.action == "none" for s in sigs)
    # A post-OR bar that stays inside the range -> no entry.
    s = eng.on_bar(*_bar(9, 45, 5005, 5008, 5002, 5006))
    assert s.action == "none"
    assert "no breakout" in s.reason


# ---------------------------------------------------------------------------
# Long breakout -> target and -> stop.
# ---------------------------------------------------------------------------
def test_long_breakout_hits_target():
    eng = ORBEngine()  # buffer 0.25, target_r 2, range stop
    _run(eng, _or_bars())
    s_in = eng.on_bar(*_bar(9, 45, 5008, 5011, 5008, 5010.5))
    assert s_in.action == "enter_long"
    assert s_in.price == 5010.25          # 5010 + 1 tick
    assert s_in.stop == 5000              # opposite OR edge
    assert s_in.risk_points == 10.25
    assert s_in.target == 5030.75         # entry + 2R
    s_out = eng.on_bar(*_bar(9, 50, 5011, 5031, 5011, 5030))
    assert s_out.action == "exit"
    assert s_out.reason == "target"
    assert abs(s_out.realized_points - 20.5) < 1e-9


def test_long_breakout_hits_stop():
    eng = ORBEngine()
    _run(eng, _or_bars())
    eng.on_bar(*_bar(9, 45, 5008, 5011, 5008, 5010.5))
    s_out = eng.on_bar(*_bar(9, 50, 5010, 5012, 4999, 5001))
    assert s_out.action == "exit"
    assert s_out.reason == "stop"
    assert abs(s_out.realized_points - (-10.25)) < 1e-9


# ---------------------------------------------------------------------------
# Short breakout.
# ---------------------------------------------------------------------------
def test_short_breakout_hits_target():
    eng = ORBEngine()
    _run(eng, _or_bars())
    s_in = eng.on_bar(*_bar(9, 45, 5000, 5001, 4999, 4999.5))
    assert s_in.action == "enter_short"
    assert s_in.price == 4999.75          # 5000 - 1 tick
    assert s_in.stop == 5010              # opposite OR edge
    assert s_in.target == 4979.25         # entry - 2R
    s_out = eng.on_bar(*_bar(9, 50, 4999, 4999, 4979, 4980))
    assert s_out.action == "exit"
    assert s_out.reason == "target"
    assert abs(s_out.realized_points - 20.5) < 1e-9


# ---------------------------------------------------------------------------
# End-of-day flatten when no target is set.
# ---------------------------------------------------------------------------
def test_holds_to_eod_when_no_target():
    eng = ORBEngine(ORBParams(target_r=0))
    _run(eng, _or_bars())
    s_in = eng.on_bar(*_bar(9, 45, 5008, 5011, 5006, 5010))
    assert s_in.action == "enter_long"
    assert s_in.target == 0.0
    assert eng.on_bar(*_bar(12, 0, 5012, 5016, 5011, 5015)).action == "none"
    s_out = eng.on_bar(*_bar(15, 55, 5015, 5016, 5014, 5015))
    assert s_out.action == "exit"
    assert s_out.reason == "eod"
    assert abs(s_out.realized_points - 4.75) < 1e-9


# ---------------------------------------------------------------------------
# Fixed-stop mode.
# ---------------------------------------------------------------------------
def test_fixed_stop_levels():
    eng = ORBEngine(ORBParams(stop="fixed", stop_points=5, target_r=2))
    _run(eng, _or_bars())
    s = eng.on_bar(*_bar(9, 45, 5008, 5011, 5008, 5010.5))
    assert s.action == "enter_long"
    assert s.price == 5010.25
    assert s.stop == 5005.25              # entry - 5
    assert s.risk_points == 5
    assert s.target == 5020.25            # entry + 2*5


# ---------------------------------------------------------------------------
# Filters and limits.
# ---------------------------------------------------------------------------
def test_min_or_filter_skips_day():
    eng = ORBEngine(ORBParams(min_or_points=20))   # range is 10 < 20
    _run(eng, _or_bars())
    s = eng.on_bar(*_bar(9, 45, 5008, 5011, 5008, 5010.5))
    assert s.action == "none"
    assert "day skipped" in s.reason


def test_max_or_filter_skips_day():
    eng = ORBEngine(ORBParams(max_or_points=5))     # range is 10 > 5
    _run(eng, _or_bars())
    s = eng.on_bar(*_bar(9, 45, 5008, 5011, 5008, 5010.5))
    assert s.action == "none"


def test_no_entry_after_cutoff():
    eng = ORBEngine(ORBParams(no_entry_after=datetime(2026, 9, 10, 9, 40).time()))
    _run(eng, _or_bars())
    s = eng.on_bar(*_bar(9, 45, 5008, 5011, 5008, 5010.5))
    assert s.action == "none"
    assert "cutoff" in s.reason


def test_one_trade_per_day():
    eng = ORBEngine()   # max_trades_per_day = 1
    _run(eng, _or_bars())
    eng.on_bar(*_bar(9, 45, 5008, 5011, 5008, 5010.5))    # enter
    eng.on_bar(*_bar(9, 50, 5010, 5012, 4999, 5001))      # stop out
    s = eng.on_bar(*_bar(9, 55, 5008, 5011, 5008, 5010.5))  # would re-break
    assert s.action == "none"
    assert "max trades" in s.reason


def test_new_day_resets():
    eng = ORBEngine()
    _run(eng, _or_bars(day=10))
    eng.on_bar(*_bar(9, 45, 5008, 5011, 5008, 5010.5, day=10))
    eng.on_bar(*_bar(9, 50, 5010, 5012, 4999, 5001, day=10))   # done for day 10
    _run(eng, _or_bars(day=11))
    s = eng.on_bar(*_bar(9, 45, 5008, 5011, 5008, 5010.5, day=11))
    assert s.action == "enter_long"


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------
def test_params_validation():
    for bad in (
        dict(or_minutes=0),
        dict(stop="market"),
        dict(stop="fixed", stop_points=0),
        dict(target_r=-1),
    ):
        try:
            ORBParams(**bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")


# ---------------------------------------------------------------------------
# Backtest summary.
# ---------------------------------------------------------------------------
def test_backtest_summary_dollars():
    bars = []
    for day in (10, 11):
        bars += _or_bars(day)
        bars.append(_bar(9, 45, 5008, 5011, 5008, 5010.5, day))   # enter long
        bars.append(_bar(9, 50, 5011, 5031, 5011, 5030, day))     # +2R target
    res = backtest(bars, "MES", contracts=2)
    assert res["num_trades"] == 2
    assert res["wins"] == 2
    assert res["win_rate"] == 1.0
    assert abs(res["net_points"] - 41.0) < 1e-9        # 2 * 20.5
    # MES point value $5, 2 contracts -> 41 * 5 * 2 = $410.
    assert abs(res["net_dollars"] - 410.0) < 1e-9


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nall {len(_TESTS)} ORB tests passed")
