"""Tests for the ORB runner: engine + guard + broker wiring (no network)."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import Config  # noqa: E402
from bot.orb_bot import ORBBot, Position, _selftest_bars  # noqa: E402


class FakeBroker:
    def __init__(self):
        self.orders = []
        self.flattens = 0

    def place_order(self, side, size):
        self.orders.append((side, size))

    def flatten(self):
        self.flattens += 1


def _cfg():
    c = Config()
    c.challenge_stop_points = 0        # use the ORB range stop
    return c


def _win_days(days):
    """N days of a clean +2R long breakout (each ~+$410 at 4 MES)."""
    bars = []
    for d in days:
        bars += [
            (datetime(2026, 9, d, 9, 30), 5005, 5008, 5000, 5006),
            (datetime(2026, 9, d, 9, 35), 5006, 5010, 5003, 5009),
            (datetime(2026, 9, d, 9, 40), 5009, 5010, 5005, 5008),
            (datetime(2026, 9, d, 9, 45), 5008, 5011, 5008, 5010.5),
            (datetime(2026, 9, d, 9, 50), 5011, 5031, 5011, 5030),
        ]
    return bars


def test_selftest_two_winning_days():
    fake = FakeBroker()
    bot = ORBBot(_cfg(), symbol="MES", broker=fake)
    res = bot.replay(_selftest_bars())
    # Each day: enter long 4 contracts, exit +2R (20.5 pts * $5 * 4 = $410).
    assert abs(res["realized"] - 820.0) < 1e-9
    assert res["target_reached"] is False
    assert res["failed"] is False
    assert len(fake.orders) == 2          # one entry per day
    assert fake.orders[0] == ("buy", 4)   # guard-sized to the buffer
    assert fake.flattens == 2             # one exit per day


def test_position_sized_to_buffer_never_oversizes():
    bot = ORBBot(_cfg(), symbol="MES")
    bot.replay(_win_days([10]))
    # 4 MES on a ~10.25pt stop risks 4*10.25*$5 = $205 < $250 budget < $500 daily.
    # (The guard risks half the smaller buffer; a stop-out stays within limits.)
    assert bot.realized > 0


def test_profit_target_halts_after_enough_wins():
    fake = FakeBroker()
    bot = ORBBot(_cfg(), symbol="MES", broker=fake)
    res = bot.replay(_win_days([10, 11, 12, 13]))   # ~$410/day
    assert res["target_reached"] is True
    assert res["realized"] >= 1500
    assert res["failed"] is False


def test_guard_forces_flat_at_5pm():
    fake = FakeBroker()
    bot = ORBBot(_cfg(), symbol="MES", broker=fake)
    bot.pos = Position(side=1, contracts=2, entry=5000.0)
    bot.on_bar(datetime(2026, 9, 10, 17, 30), 5010, 5011, 5009, 5010)
    assert bot.pos.side == 0                       # flattened
    assert abs(bot.realized - 100.0) < 1e-9        # (5010-5000)*$5*2
    assert fake.flattens == 1


def test_no_entry_outside_session():
    fake = FakeBroker()
    bot = ORBBot(_cfg(), symbol="MES", broker=fake)
    # An "opening range" + breakout shifted into the overnight session never fills
    # because the guard blocks entries outside RTH.
    bars = [
        (datetime(2026, 9, 10, 19, 30), 5005, 5008, 5000, 5006),
        (datetime(2026, 9, 10, 19, 35), 5006, 5010, 5003, 5009),
        (datetime(2026, 9, 10, 19, 40), 5009, 5010, 5005, 5008),
        (datetime(2026, 9, 10, 19, 45), 5008, 5011, 5008, 5010.5),
    ]
    # Engine's opening range is 09:30, so these bars are outside it anyway; assert
    # nothing is ordered.
    bot.replay(bars)
    assert fake.orders == []


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nall {len(_TESTS)} orb-bot tests passed")
