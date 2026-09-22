"""Offline tests for the forex pair scanner (alex_fx/scanner.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alex_bot.strategy import Candle, Params, Signal, Trend, Zone  # noqa: E402
from alex_fx import scanner as fxscan  # noqa: E402


def _dummy_candles(n=40):
    return [Candle(i, 1.1, 1.1 + 0.001, 1.1 - 0.001, 1.1) for i in range(n)]


# Canned per-pair signals so the scanner logic is tested without network/engine.
_SIGNALS = {
    "EUR/USD": Signal("buy", Trend.UP, 1.10, "setup", zone=Zone(1.09, 1.10, 3, "support"),
                      entry=1.10, stop=1.095, take_profit=1.11, pattern="rejection"),
    "GBP/USD": Signal("none", Trend.UP, 1.25, "at support zone, waiting",
                      zone=Zone(1.24, 1.25, 3, "support")),
    "USD/JPY": Signal("none", Trend.DOWN, 150.0, "no valid area of interest"),
    "AUD/USD": Signal("none", Trend.NEUTRAL, 0.65, "no confirmed trend"),
}


def test_scan_categorizes_and_ranks():
    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF"]
    orig = fxscan.evaluate

    def fake_eval(structure, entry, params):
        return fake_eval.current

    def fetch(pair, tf):
        if pair == "USD/CHF":
            raise RuntimeError("boom")
        fake_eval.current = _SIGNALS[pair]
        return _dummy_candles()

    fxscan.evaluate = fake_eval
    try:
        rows = fxscan.scan(fetch, Params(), pairs=pairs, workers=1)
    finally:
        fxscan.evaluate = orig

    by = {r.pair: r for r in rows}
    assert by["EUR/USD"].status == "SETUP" and by["EUR/USD"].action == "buy"
    assert by["EUR/USD"].rr and abs(by["EUR/USD"].rr - 2.0) < 1e-9
    assert by["GBP/USD"].status == "WATCH"
    assert by["USD/JPY"].status == "TREND"
    assert by["AUD/USD"].status == "-"
    assert by["USD/CHF"].status == "ERR"
    # SETUP must sort to the very top
    assert rows[0].status == "SETUP"


def test_format_table_filters():
    rows = [
        fxscan.ScanRow("EUR/USD", "SETUP", "UPTREND", "buy", "rejection",
                       1.10, 1.10, 1.095, 1.11, 2.0),
        fxscan.ScanRow("GBP/USD", "WATCH", "UPTREND"),
        fxscan.ScanRow("USD/JPY", "TREND", "DOWNTREND"),
    ]
    setups = fxscan.format_table(rows, show="setups")
    assert "EUR/USD" in setups and "GBP/USD" not in setups
    actionable = fxscan.format_table(rows, show="actionable")
    assert "EUR/USD" in actionable and "GBP/USD" in actionable and "USD/JPY" not in actionable
    assert "1 live setup" in setups


def test_universe_nonempty():
    assert "EUR/USD" in fxscan.DEFAULT_UNIVERSE
    assert len(fxscan.DEFAULT_UNIVERSE) >= 15


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all scanner tests passed")
