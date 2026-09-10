"""The ChallengeGuard actually gates the StrategyRunner's signals."""
import os
import sys
from datetime import datetime, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import Config  # noqa: E402
from bot.risk import ChallengeGuard, ChallengeParams  # noqa: E402
from bot.strategy import StrategyRunner  # noqa: E402


def _runner(now, equity, position=0.0):
    cfg = Config()
    guard = ChallengeGuard(ChallengeParams())
    return StrategyRunner(
        cfg,
        guard=guard,
        equity_fn=lambda: equity,
        position_fn=lambda: position,
        now_fn=lambda: now,
    )


def test_entry_allowed_during_rth():
    r = _runner(datetime(2026, 9, 10, 10, 0), equity=50_000, position=0)
    action, decision = r._apply_guard("buy")
    assert action == "buy"
    assert decision.can_enter is True


def test_entry_blocked_overnight():
    r = _runner(datetime(2026, 9, 10, 19, 0), equity=50_000, position=0)
    action, decision = r._apply_guard("buy")
    assert action == "none"
    assert decision.can_enter is False


def test_forced_flatten_at_5pm_when_in_position():
    r = _runner(datetime(2026, 9, 10, 17, 30), equity=50_000, position=1)
    action, decision = r._apply_guard("none")
    assert action == "flatten"
    assert decision.must_flatten is True


def test_no_flatten_when_already_flat():
    r = _runner(datetime(2026, 9, 10, 17, 30), equity=50_000, position=0)
    action, _ = r._apply_guard("none")
    assert action == "none"


def test_target_reached_blocks_entry():
    r = _runner(datetime(2026, 9, 10, 10, 0), equity=51_500, position=0)
    action, decision = r._apply_guard("buy")
    assert action == "none"
    assert decision.target_reached is True


def test_no_guard_passes_signal_through():
    cfg = Config()  # challenge disabled by default
    r = StrategyRunner(cfg)
    action, decision = r._apply_guard("buy")
    assert action == "buy"
    assert decision is None


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nall {len(_TESTS)} strategy-guard tests passed")
