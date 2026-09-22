"""Tests for the forex bot: pip math, backtester, CSV loader (all offline)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alex_bot.strategy import Params  # noqa: E402
from alex_fx import data as fxdata  # noqa: E402
from alex_fx.backtest import Trade, backtest  # noqa: E402
from alex_fx.instrument import (from_pips, lots_for_risk, pip_size,  # noqa: E402
                                to_pips)


# -- pip math ----------------------------------------------------------------
def test_pip_size():
    assert pip_size("EUR/USD") == 0.0001
    assert pip_size("USD/JPY") == 0.01
    assert round(to_pips("EUR/USD", 0.0025), 1) == 25.0
    assert round(from_pips("EUR/USD", 25), 4) == 0.0025


def test_lots_for_risk_scales_with_stop():
    # $10k, risk 1% ($100). Stop 20 pips on EUR/USD -> pip value ~$10/lot ->
    # 20 pips = $200/lot -> 0.5 lots.
    lots = lots_for_risk("EUR/USD", 10000, 1.0, 1.1000, 1.1000 - 0.0020)
    assert abs(lots - 0.5) < 0.02, lots
    # tighter stop -> bigger size
    tighter = lots_for_risk("EUR/USD", 10000, 1.0, 1.1000, 1.1000 - 0.0010)
    assert tighter > lots


# -- trade R math ------------------------------------------------------------
def test_trade_r_multiple():
    win = Trade("buy", 0, 1.1000, 1.0980, 1.1040, exit=1.1040, reason="tp")
    assert abs(win.r_multiple - 2.0) < 1e-9      # +40 / 20 risk = 2R
    loss = Trade("buy", 0, 1.1000, 1.0980, 1.1040, exit=1.0980, reason="stop")
    assert abs(loss.r_multiple - (-1.0)) < 1e-9
    short = Trade("sell", 0, 1.1000, 1.1020, 1.0960, exit=1.0960, reason="tp")
    assert abs(short.r_multiple - 2.0) < 1e-9


# -- backtester --------------------------------------------------------------
def test_backtest_runs_and_reports():
    candles = fxdata.synthetic_uptrend_with_pullback("EUR/USD")
    res = backtest(candles, "EUR/USD", Params(pivot_lookback=1, aoi_tol_frac=0.03),
                   warmup=6, spread_pips=0.5)
    assert len(res.trades) >= 1
    # summary computes without error and reports the pair
    s = res.summary()
    assert "EUR/USD" in s and "win rate" in s
    # stats are internally consistent
    assert res.wins + res.losses == len(res.closed)


def test_backtest_no_lookahead_flat_series():
    flat = fxdata.synthetic_uptrend_with_pullback("EUR/USD")
    flat = [type(c)(c.time, c.close, c.close, c.close, c.close) for c in flat]
    res = backtest(flat, "EUR/USD", Params(pivot_lookback=1), warmup=6)
    assert res.trades == []                     # dead-flat market -> no trades


# -- csv loader --------------------------------------------------------------
def test_load_csv_roundtrip():
    rows = ["time,open,high,low,close,volume",
            "2026-06-01 00:00:00,1.1000,1.1010,1.0990,1.1005,100",
            "2026-06-01 00:15:00,1.1005,1.1020,1.1000,1.1015,120"]
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "eurusd.csv")
        with open(path, "w") as f:
            f.write("\n".join(rows))
        candles = fxdata.load_csv(path)
        assert len(candles) == 2
        assert candles[0].open == 1.1000 and candles[1].close == 1.1015
        assert candles[0].time < candles[1].time


def test_parse_time_unix_and_iso():
    assert fxdata._parse_time("1780272000") == 1780272000        # unix seconds
    assert fxdata._parse_time("1780272000000") == 1780272000     # unix millis
    assert fxdata._parse_time("2026-06-01T00:00:00") > 0         # iso


def test_parse_yahoo_keyless_payload():
    # Shape of the keyless Yahoo chart endpoint; nulls are skipped.
    payload = {"chart": {"result": [{
        "timestamp": [1000, 2000, 3000],
        "indicators": {"quote": [{
            "open": [1.10, 1.11, None], "high": [1.12, 1.13, 1.14],
            "low": [1.09, 1.10, 1.11], "close": [1.11, 1.12, 1.13]}]},
    }]}}
    candles = fxdata.parse_yahoo(payload)
    assert len(candles) == 2                     # third row has a null -> skipped
    assert candles[0].open == 1.10 and candles[1].close == 1.12
    assert candles[0].time == 1000


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all alex_fx tests passed")
