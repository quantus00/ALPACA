"""Tests for the CSV backtest + report harness (bot/orb_backtest.py)."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.orb_backtest import load_csv, report, run  # noqa: E402
from bot.risk import ChallengeParams  # noqa: E402


def _mgc_win_days(days):
    bars = []
    for d in days:
        bars += [
            (datetime(2026, 9, d, 9, 30), 2402, 2403, 2400, 2402),
            (datetime(2026, 9, d, 9, 35), 2402, 2405, 2401, 2404),
            (datetime(2026, 9, d, 9, 40), 2404, 2405, 2402, 2404),
            (datetime(2026, 9, d, 9, 45), 2404, 2406, 2404, 2405.5),   # breakout
            (datetime(2026, 9, d, 9, 50), 2406, 2416, 2406, 2415),     # +2R target
        ]
    return bars


def test_report_fields_on_winning_days():
    bars = _mgc_win_days([10, 11, 12])
    cp = ChallengeParams()
    bot = run(bars, "MGC", tick=0.10, or_minutes=15, target_r=2, cparams=cp)
    r = report(bot, cp)
    assert r["trades"] == 3
    assert r["wins"] == 3
    assert r["win_rate"] == 1.0
    assert r["net_pnl"] > 0
    assert r["failed"] is False
    assert r["exits"].get("target") == 3
    assert r["avg_win"] > 0


def test_csv_roundtrip(tmp_path=None):
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "mgc_test_bars.csv")
    with open(path, "w") as fh:
        fh.write("time,open,high,low,close\n")
        for b in _mgc_win_days([10]):
            ts, o, h, l, c = b
            fh.write(f"{ts.isoformat()},{o},{h},{l},{c}\n")
    bars = load_csv(path)
    assert len(bars) == 5
    assert bars[0][0] == datetime(2026, 9, 10, 9, 30)
    assert bars[0][3] == 2400  # low
    os.remove(path)


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nall {len(_TESTS)} backtest-harness tests passed")
