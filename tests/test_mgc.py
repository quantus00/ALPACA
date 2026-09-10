"""MGC (Micro Gold) instrument wiring: point value, tick size, ORB dollars."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import Config, Instrument  # noqa: E402
from bot.orb import ORBParams, backtest  # noqa: E402
from bot.risk import pnl_dollars, point_value  # noqa: E402


def test_mgc_point_values():
    assert point_value("MGC") == 10.0     # micro gold: $10 per 1.0 point
    assert point_value("GC") == 100.0     # full-size gold
    # Long +10 points, 1 MGC = 10 * $10 = $100.
    assert pnl_dollars(2400.0, 2410.0, "buy", 1, "MGC") == 100.0


def test_mgc_config_tick_and_symbol():
    cfg = Config()
    cfg.instrument = Instrument.MGC_FUTURES
    assert cfg.symbol() == "MGC"
    # Gold must tick at 0.10, not inherit the 0.25 index default.
    assert cfg.build_orb_params().tick_size == 0.10


def _bar(h, m, o, hi, lo, c):
    return (datetime(2026, 9, 10, h, m), o, hi, lo, c)


def test_mgc_orb_backtest_dollars():
    # Opening range 2400-2405 (width 5), 0.10 tick, 1-tick buffer -> entry 2405.10.
    bars = [
        _bar(9, 30, 2402, 2403, 2400, 2402),
        _bar(9, 35, 2402, 2405, 2401, 2404),
        _bar(9, 40, 2404, 2405, 2402, 2404),
        _bar(9, 45, 2404, 2406, 2404, 2405.5),   # breakout long @ 2405.10
        _bar(9, 50, 2406, 2416, 2406, 2415),     # +2R target @ 2415.30
    ]
    params = ORBParams(tick_size=0.10, target_r=2, stop="range")
    res = backtest(bars, "MGC", params, contracts=1)
    assert res["num_trades"] == 1
    assert res["wins"] == 1
    assert abs(res["net_points"] - 10.2) < 1e-9      # 2 * 5.10 risk
    assert abs(res["net_dollars"] - 102.0) < 1e-9    # 10.2 pts * $10 * 1


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nall {len(_TESTS)} MGC tests passed")
