"""Dry-run tests for the dual-broker orchestrator (bot/dual.py).

No network or credentials: Coinbase's public mark lookup is stubbed and orders
run in dry-run, so this exercises the open -> state -> flatten plumbing only.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import dual as dual_mod  # noqa: E402
from bot.brokers.coinbase import CoinbaseBroker  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.portfolio import load_state  # noqa: E402


def _base_cfg(state_path):
    cfg = Config()
    cfg.dry_run = True
    cfg.state_file = state_path
    return cfg


def test_parse_legs_variants():
    specs = dual_mod.parse_legs("coinbase:btc_usd_spot:0.01,webull:spy_options:1")
    assert len(specs) == 2
    assert specs[0].broker.value == "coinbase"
    assert specs[0].size == 0.01
    assert specs[1].instrument.value == "spy_options"

    listed = dual_mod.parse_legs(["coinbase:btc_usd_spot:0.02"])
    assert listed[0].size == 0.02


def test_bad_leg_spec_raises():
    try:
        dual_mod.parse_legs("coinbase:btc_usd_spot")   # missing size
    except ValueError:
        return
    raise AssertionError("expected ValueError for malformed leg spec")


def test_open_saves_state_and_flatten_clears(monkeypatch=None):
    # Stub Coinbase's public mark so no network call happens.
    orig = CoinbaseBroker.mark_price
    CoinbaseBroker.mark_price = lambda self, symbol=None, meta=None: 50000.0
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            cfg = _base_cfg(path)
            specs = dual_mod.parse_legs(
                "coinbase:btc_usd_spot:0.01,webull:spy_options:1")
            legs = dual_mod.open_all(cfg, specs, "buy")

            assert len(legs) == 2
            brokers = {leg.broker for leg in legs}
            assert brokers == {"coinbase", "webull"}
            cb = next(leg for leg in legs if leg.broker == "coinbase")
            assert cb.entry_price == 50000.0        # from stubbed mark
            assert cb.multiplier == 1.0
            wb = next(leg for leg in legs if leg.broker == "webull")
            assert wb.multiplier == 100.0

            # State persisted for a later flatten/status.
            assert len(load_state(path)) == 2

            dual_mod.flatten_all(cfg, legs)
            assert load_state(path) == []
    finally:
        CoinbaseBroker.mark_price = orig


def test_rules_from_config():
    cfg = Config()
    cfg.tp_pct, cfg.sl_pct, cfg.leg_tp_pct, cfg.leg_sl_pct = 2.0, 1.0, 3.0, 1.5
    rules = dual_mod.rules_from(cfg)
    assert rules.tp_pct == 2.0 and rules.sl_pct == 1.0
    assert rules.leg_tp_pct == 3.0 and rules.leg_sl_pct == 1.5
    assert rules.active()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all dual tests passed")
