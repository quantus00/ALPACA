"""Unit tests for the dual-broker P/L engine (bot/portfolio.py)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.portfolio import (ExitRules, Leg, clear_state, combined_pnl_pct,  # noqa: E402
                           flatten_plan, load_state, save_state, should_flatten)


def _btc(entry=100.0, size=1.0, side="buy"):
    return Leg(broker="coinbase", instrument="btc_usd_spot", symbol="BTC-USD",
               side=side, size=size, entry_price=entry, multiplier=1.0)


def _opt(entry=2.0, size=1.0, side="buy"):
    return Leg(broker="webull", instrument="spy_options", symbol="SPY",
               side=side, size=size, entry_price=entry, multiplier=100.0,
               meta={"option_id": "123"})


def test_single_leg_long_pnl():
    leg = _btc(entry=100.0, size=2.0)
    assert leg.pnl_dollars(110.0) == 20.0          # (110-100)*2
    assert leg.pnl_pct(110.0) == 10.0              # 20 / 200 cost
    assert leg.pnl_pct(90.0) == -10.0


def test_single_leg_short_pnl():
    leg = _btc(entry=100.0, size=1.0, side="sell")
    assert leg.pnl_pct(90.0) == 10.0               # short gains when price drops
    assert leg.pnl_pct(110.0) == -10.0


def test_option_multiplier_in_cost_basis():
    leg = _opt(entry=2.0, size=1.0)                # cost = 2 * 1 * 100 = 200
    assert leg.cost_basis() == 200.0
    assert leg.pnl_dollars(3.0) == 100.0           # (3-2)*1*100
    assert leg.pnl_pct(3.0) == 50.0


def test_combined_weights_by_notional():
    # BTC: cost 100, +10 dollars (+10%). Option: cost 200, +40 dollars (+20%).
    btc = _btc(entry=100.0, size=1.0)              # mark 110 -> +10
    opt = _opt(entry=2.0, size=1.0)                # mark 2.4 -> +0.4*100 = +40
    combined = combined_pnl_pct([btc, opt], [110.0, 2.4])
    assert round(combined, 4) == round((10 + 40) / (100 + 200) * 100, 4)  # +16.67%


def test_combined_skips_unpriced_leg():
    btc = _btc(entry=100.0, size=1.0)
    opt = _opt(entry=2.0, size=1.0)
    # option mark unknown -> combined uses BTC only.
    assert combined_pnl_pct([btc, opt], [110.0, None]) == 10.0
    # entry unknown -> that leg is skipped too.
    opt.entry_price = None
    assert combined_pnl_pct([btc, opt], [110.0, 3.0]) == 10.0
    # nothing priceable -> None.
    assert combined_pnl_pct([opt], [3.0]) is None


def test_combined_take_profit_trips():
    btc = _btc(entry=100.0, size=1.0)
    opt = _opt(entry=2.0, size=1.0)
    rules = ExitRules(tp_pct=15.0)
    flat, reason = should_flatten([btc, opt], [110.0, 2.4], rules)   # +16.67%
    assert flat and "take-profit" in reason


def test_combined_stop_loss_trips():
    btc = _btc(entry=100.0, size=1.0)
    rules = ExitRules(sl_pct=5.0)
    flat, reason = should_flatten([btc], [94.0], rules)             # -6%
    assert flat and "stop-loss" in reason


def test_per_leg_rule_trips_even_when_combined_is_calm():
    # Combined ~0 but one leg is deep in profit -> per-leg TP should trip.
    win = _btc(entry=100.0, size=1.0)              # mark 130 -> +30%
    lose = _btc(entry=100.0, size=1.0, side="buy")
    lose.symbol = "BTC-USD"
    rules = ExitRules(tp_pct=50.0, leg_tp_pct=25.0)
    flat, reason = should_flatten([win, lose], [130.0, 70.0], rules)
    assert flat and "leg take-profit" in reason


def test_no_rule_no_flatten():
    btc = _btc(entry=100.0, size=1.0)
    flat, reason = should_flatten([btc], [110.0], ExitRules())
    assert not flat and reason == ""


def test_single_mode_flattens_only_the_tripped_leg():
    # Leg 0 hits its +25% take-profit; leg 1 is flat. Combined TP is far off.
    win = _btc(entry=100.0, size=1.0)              # mark 130 -> +30%
    calm = _btc(entry=100.0, size=1.0)             # mark 100 -> 0%
    rules = ExitRules(leg_tp_pct=25.0)
    plan = flatten_plan([win, calm], [130.0, 100.0], rules, mode="single")
    assert [i for i, _ in plan] == [0]             # only leg 0
    assert "leg take-profit" in plan[0][1]


def test_combined_mode_flattens_all_on_a_per_leg_hit():
    win = _btc(entry=100.0, size=1.0)              # +30%
    calm = _btc(entry=100.0, size=1.0)             # 0%
    rules = ExitRules(leg_tp_pct=25.0)
    plan = flatten_plan([win, calm], [130.0, 100.0], rules, mode="combined")
    assert sorted(i for i, _ in plan) == [0, 1]    # every leg
    assert "flatten all" in plan[0][1]


def test_combined_basket_rule_flattens_all_in_single_mode_too():
    # A combined tp/sl always closes the whole basket, regardless of mode.
    a = _btc(entry=100.0, size=1.0)
    b = _btc(entry=100.0, size=1.0)
    rules = ExitRules(tp_pct=5.0)
    plan = flatten_plan([a, b], [110.0, 110.0], rules, mode="single")  # +10% combined
    assert sorted(i for i, _ in plan) == [0, 1]
    assert "take-profit" in plan[0][1]


def test_state_roundtrip():
    legs = [_btc(), _opt()]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "state.json")
        save_state(path, legs)
        loaded = load_state(path)
        assert len(loaded) == 2
        assert loaded[0].symbol == "BTC-USD"
        assert loaded[1].meta["option_id"] == "123"
        assert loaded[1].multiplier == 100.0
        clear_state(path)
        assert load_state(path) == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all portfolio tests passed")
