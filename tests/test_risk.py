"""Unit tests for the prop-firm challenge guard (bot/risk.py).

Covers every rule of the trailing-drawdown evaluation: profit-target pass,
daily-loss lockout and its next-session reset, both trailing-drawdown modes
(intraday high-water vs end-of-day close), the floor freezing at breakeven,
flat-by-5pm / maintenance / overnight session gating, position sizing, and the
dollar/point helpers.
"""
import os
import sys
from datetime import datetime, time, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.risk import (  # noqa: E402
    ChallengeGuard,
    ChallengeParams,
    max_contracts,
    pnl_dollars,
    point_value,
)


def _dt(month, day, hour, minute=0):
    """An ET-naive datetime (the guard treats naive input as already ET)."""
    return datetime(2026, month, day, hour, minute)


# ---------------------------------------------------------------------------
# Dollar / point helpers.
# ---------------------------------------------------------------------------
def test_point_values_and_pnl():
    assert point_value("MES") == 5.0
    assert point_value("mes") == 5.0        # case-insensitive
    assert point_value("ES") == 50.0
    # Long +10 points, 2 MES = 10 * $5 * 2 = $100.
    assert pnl_dollars(5000, 5010, "buy", 2, "MES") == 100.0
    # Short from 5010 down to 5000 = +10 points profit, 1 MES = $50.
    assert pnl_dollars(5010, 5000, "sell", 1, "MES") == 50.0
    # Long that goes against you.
    assert pnl_dollars(5000, 4990, "buy", 1, "MES") == -50.0


def test_point_value_unknown_symbol():
    try:
        point_value("AAPL")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for unknown symbol")


def test_max_contracts():
    # $250 risk, 5-point stop, MES ($5/pt) -> $25/contract -> 10 contracts.
    assert max_contracts(5, "MES", 250) == 10
    # 10-point stop halves it.
    assert max_contracts(10, "MES", 250) == 5
    # Rounds DOWN so worst case stays within budget.
    assert max_contracts(7, "MES", 250) == 7   # floor(250/35)=7
    assert max_contracts(5, "MES", 0) == 0
    try:
        max_contracts(0, "MES", 250)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for non-positive stop")


# ---------------------------------------------------------------------------
# Parameter validation.
# ---------------------------------------------------------------------------
def test_params_defaults_and_validation():
    p = ChallengeParams()
    assert p.profit_target == 1500
    assert p.daily_loss_limit == 500
    assert p.trailing_drawdown == 1000
    # Floor freeze defaults to the starting balance.
    assert p.lock_floor_at == p.starting_balance

    try:
        ChallengeParams(trailing_mode="weekly")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for bad trailing_mode")

    try:
        ChallengeParams(entry_windows=((time(16, 0), time(9, 0)),))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unordered entry window")


# ---------------------------------------------------------------------------
# Baseline "clear to trade" during RTH.
# ---------------------------------------------------------------------------
def test_clear_to_enter_during_rth():
    g = ChallengeGuard()
    d = g.update(_dt(9, 10, 10, 0), equity=50_000, position=0)
    assert d.can_enter is True
    assert d.must_flatten is False
    assert d.halted is False
    assert d.floor == 49_000
    assert d.buffer_to_floor == 1_000
    assert d.buffer_to_daily == 500
    assert d.total_pnl == 0


# ---------------------------------------------------------------------------
# Profit target = pass condition.
# ---------------------------------------------------------------------------
def test_profit_target_halts_and_flattens():
    g = ChallengeGuard()
    d = g.update(_dt(9, 10, 10, 0), equity=51_500, position=1)
    assert d.target_reached is True
    assert d.halted is True
    assert d.must_flatten is True          # flatten_on_target default
    assert d.can_enter is False
    assert "PROFIT TARGET" in d.reason
    # Terminal: stays halted even if equity later pulls back.
    d2 = g.update(_dt(9, 10, 10, 5), equity=51_200, position=0)
    assert d2.halted is True
    assert d2.can_enter is False


# ---------------------------------------------------------------------------
# Daily loss limit: lockout then next-session reset.
# ---------------------------------------------------------------------------
def test_daily_loss_lockout_and_reset():
    g = ChallengeGuard()
    g.update(_dt(9, 10, 10, 0), equity=50_000, position=0)
    d = g.update(_dt(9, 10, 11, 0), equity=49_500, position=1)   # -$500 on the day
    assert d.day_locked is True
    assert d.must_flatten is True
    assert d.can_enter is False
    assert d.halted is False                # daily loss is NOT terminal
    assert d.buffer_to_daily == 0
    assert "DAILY LOSS" in d.reason

    # Evening session (>= 18:00) rolls the trading day: lock clears...
    d_eve = g.update(_dt(9, 10, 18, 30), equity=49_500, position=0)
    assert d_eve.day_locked is False
    # ...but overnight is not a trading window, so still no entries.
    assert d_eve.can_enter is False

    # Next RTH morning: free to trade again, fresh daily anchor.
    d_next = g.update(_dt(9, 11, 10, 0), equity=49_500, position=0)
    assert d_next.day_locked is False
    assert d_next.can_enter is True
    assert d_next.daily_pnl == 0


# ---------------------------------------------------------------------------
# Trailing drawdown — intraday high-water mark.
# ---------------------------------------------------------------------------
def test_intraday_trailing_ratchets_and_breaches():
    g = ChallengeGuard()  # intraday mode
    g.update(_dt(9, 10, 10, 0), equity=50_000, position=0)
    # Rally: the floor trails up with the intraday peak.
    d = g.update(_dt(9, 10, 11, 0), equity=50_800, position=1)
    assert d.peak == 50_800
    assert d.floor == 49_800            # 50_800 - 1_000
    assert d.buffer_to_floor == 1_000
    # Give back exactly the trailing amount from the peak -> account failed.
    d2 = g.update(_dt(9, 10, 12, 0), equity=49_800, position=1)
    assert d2.halted is True
    assert d2.must_flatten is True
    assert "TRAILING DRAWDOWN" in d2.reason


def test_trailing_floor_freezes_at_breakeven():
    g = ChallengeGuard()
    g.update(_dt(9, 10, 10, 0), equity=50_000, position=0)
    d = g.update(_dt(9, 10, 11, 0), equity=51_200, position=1)
    # Floor would be 50_200 but is capped at the starting balance (50_000).
    assert d.peak == 51_200
    assert d.floor == 50_000
    assert d.buffer_to_floor == 1_200


# ---------------------------------------------------------------------------
# Trailing drawdown — end-of-day (closing balance) mode.
# ---------------------------------------------------------------------------
def test_eod_trailing_only_ratchets_on_close():
    p = ChallengeParams(trailing_mode="eod")
    g = ChallengeGuard(p)
    g.update(_dt(9, 10, 10, 0), equity=50_000, position=0)
    # Intraday spike does NOT move the floor in EOD mode.
    d = g.update(_dt(9, 10, 11, 0), equity=50_800, position=1)
    assert d.peak == 50_000
    assert d.floor == 49_000
    assert d.buffer_to_floor == 1_800
    # Pull back intraday - still safe because the floor never rose.
    d2 = g.update(_dt(9, 10, 13, 0), equity=50_100, position=1)
    assert d2.halted is False
    # Flatten into the 5pm close green: the closing balance locks into the trail.
    d3 = g.update(_dt(9, 10, 17, 30), equity=50_600, position=0)
    assert d3.peak == 50_600
    assert d3.floor == 49_600
    assert d3.must_flatten is True          # inside the flat window
    # Next session keeps the locked-in floor.
    d4 = g.update(_dt(9, 11, 10, 0), equity=50_100, position=0)
    assert d4.floor == 49_600


# ---------------------------------------------------------------------------
# Session clock: RTH cutoff, forced flat, maintenance, overnight.
# ---------------------------------------------------------------------------
def test_session_windows():
    g = ChallengeGuard()
    # Inside RTH -> can enter.
    assert g.update(_dt(9, 10, 16, 30), equity=50_000).can_enter is True
    # After the 16:55 cutoff but before 5pm: no new entries, not yet forced flat.
    d_cut = g.update(_dt(9, 10, 16, 58), equity=50_000, position=1)
    assert d_cut.can_enter is False
    assert d_cut.must_flatten is False
    # 5:00-6:00pm ET: hard flat.
    d_flat = g.update(_dt(9, 10, 17, 30), equity=50_000, position=1)
    assert d_flat.must_flatten is True
    assert d_flat.can_enter is False
    assert "FLAT BY 5PM" in d_flat.reason
    # Overnight after reopen: thin session left alone (no entries, no force-flat).
    d_night = g.update(_dt(9, 10, 19, 0), equity=50_000, position=0)
    assert d_night.can_enter is False
    assert d_night.must_flatten is False


def test_custom_overnight_window_allows_entries():
    p = ChallengeParams(entry_windows=((time(18, 0), time(23, 0)),))
    g = ChallengeGuard(p)
    assert g.update(_dt(9, 10, 19, 0), equity=50_000).can_enter is True
    assert g.update(_dt(9, 10, 10, 0), equity=50_000).can_enter is False


# ---------------------------------------------------------------------------
# Anti-oversizing.
# ---------------------------------------------------------------------------
def test_size_for_respects_smaller_buffer():
    g = ChallengeGuard()
    d = g.update(_dt(9, 10, 10, 0), equity=50_000, position=0)
    # headroom = min(daily 500, floor 1000) = 500; risk 50% = $250; 5pt stop,
    # MES $25/contract -> 10 contracts.
    assert g.size_for(d, stop_points=5, symbol="MES") == 10
    assert g.size_for(d, stop_points=10, symbol="MES") == 5
    # No entry allowed -> zero size.
    d_flat = g.update(_dt(9, 10, 17, 30), equity=50_000)
    assert g.size_for(d_flat, stop_points=5, symbol="MES") == 0


# ---------------------------------------------------------------------------
# Timezone-aware input is converted to ET.
# ---------------------------------------------------------------------------
def test_utc_input_converted_to_et():
    g = ChallengeGuard()
    if g._tz is None:
        return  # zoneinfo unavailable; skip
    # 14:00 UTC in September = 10:00 EDT -> inside RTH.
    d = g.update(datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc), equity=50_000)
    assert d.can_enter is True
    # 22:00 UTC = 18:00 EDT -> overnight, no entries.
    g2 = ChallengeGuard()
    d2 = g2.update(datetime(2026, 9, 10, 22, 0, tzinfo=timezone.utc), equity=50_000)
    assert d2.can_enter is False


_TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nall {len(_TESTS)} risk tests passed")
