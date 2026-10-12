"""Reversal detector: no lookahead, and it catches a textbook top."""
import numpy as np
import pandas as pd

from reversal.detector import LAYERS, load_config, prepare, run


def _random_walk(n=3000, seed=7):
    rng = np.random.default_rng(seed)
    close = 30000 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.003, n)) * close
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread, "close": close,
                         "volume": rng.uniform(10, 100, n)}, index=idx)


def _daily(df):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample("1D").agg(agg).dropna()


def _sig(trades):
    return [(t.entry_time, round(t.entry_price, 6), t.exit_time, t.exit_reason) for t in trades]


def test_no_lookahead():
    """Trades that finished before a cut must be identical with or without the future bars."""
    cfg = load_config()
    full = _random_walk()
    for lay in ({k: False for k in LAYERS}, {**{k: False for k in LAYERS}, "htf": True}, None):
        all_trades = run(prepare(full, "1h", _daily(full), cfg), cfg, lay)
        for cut in (1500, 2200):
            part = full.iloc[:cut]
            got = run(prepare(part, "1h", _daily(part), cfg), cfg, lay)
            end = part.index[-1]
            want = [t for t in all_trades if t.exit_time < end]
            assert _sig([t for t in got if t.exit_reason != "open"]) == _sig(want)


def test_catches_textbook_top():
    """Rally to a top, lower high, then break of the pullback low -> one short, profitable on the drop."""
    legs = [(100, 110, 20), (110, 104, 12), (104, 120, 32), (120, 110, 12), (110, 140, 30), (140, 128, 12), (128, 135, 10),
            (135, 90, 60), (90, 92, 40)]
    px = np.concatenate([np.linspace(a, b, n, endpoint=False) for a, b, n in legs])
    px = px + np.where(np.arange(len(px)) % 2, 0.15, -0.15)
    idx = pd.date_range("2024-01-01", periods=len(px), freq="1h", tz="UTC")
    df = pd.DataFrame({"open": np.r_[px[0], px[:-1]], "high": px + 0.3, "low": px - 0.3,
                       "close": px, "volume": 50.0}, index=idx)
    cfg = load_config()
    cfg["costs"]["slippage_usd_per_btc_side"] = 0.0
    for p in cfg["products"].values():
        p["fee_usd_per_contract_side"] = 0.0
    trades = run(prepare(df, "1h", None, cfg), cfg, {k: False for k in LAYERS})
    assert len(trades) == 1
    t = trades[0]
    assert 120 < t.entry_price < 128 and t.top > 139   # just under the 127.5 neckline
    assert t.pnl_usd > 0


def test_bottom_is_mirror_of_top():
    """The textbook top, flipped upside down, must be found as a bottom at the mirrored price."""
    from reversal.accuracy import grade_signals, signals_both
    legs = [(100, 110, 20), (110, 104, 12), (104, 120, 32), (120, 110, 12), (110, 140, 30), (140, 128, 12),
            (128, 135, 10), (135, 90, 60), (90, 92, 40)]
    px = np.concatenate([np.linspace(a, b, n, endpoint=False) for a, b, n in legs])
    px = px + np.where(np.arange(len(px)) % 2, 0.15, -0.15)
    idx = pd.date_range("2024-01-01", periods=len(px), freq="1h", tz="UTC")
    up = pd.DataFrame({"open": np.r_[px[0], px[:-1]], "high": px + 0.3, "low": px - 0.3,
                       "close": px, "volume": 50.0}, index=idx)
    down = up.copy()            # mirror around 250: tops become bottoms
    down["open"], down["close"] = 250 - up["open"], 250 - up["close"]
    down["high"], down["low"] = 250 - up["low"], 250 - up["high"]
    cfg = load_config()
    off = {k: False for k in LAYERS}
    d, di, tops, bottoms = signals_both(down, None, "1h", cfg, off)
    g = grade_signals(d, di, tops, bottoms, cfg)
    b = g[g.side == "bottom"]
    assert len(b) == 1
    assert abs(b.iloc[0].signal_price - (250 - 127.35)) < 1e-6      # mirror of the top signal
    assert b.iloc[0].extreme_price < 111 and b.iloc[0].valid


def _textbook():
    legs = [(100, 110, 20), (110, 104, 12), (104, 120, 32), (120, 110, 12), (110, 140, 30), (140, 128, 12),
            (128, 135, 10), (135, 90, 60), (90, 92, 40)]
    px = np.concatenate([np.linspace(a, b, n, endpoint=False) for a, b, n in legs])
    px = px + np.where(np.arange(len(px)) % 2, 0.15, -0.15)
    idx = pd.date_range("2024-01-01", periods=len(px), freq="1h", tz="UTC")
    return pd.DataFrame({"open": np.r_[px[0], px[:-1]], "high": px + 0.3, "low": px - 0.3,
                         "close": px, "volume": 50.0}, index=idx)


def test_funding_filter_and_mirror():
    """Top needs funding in its top 20%; the mirrored (bottom) frame flips funding."""
    from reversal.detector import attach_funding, invert
    df = _textbook()
    cfg = load_config()
    cfg["funding"]["rank_days"] = 3
    lay = {**{k: False for k in LAYERS}, "funding": True}
    ft = pd.date_range(df.index[0], df.index[-1], freq="8h")
    rising = pd.DataFrame({"funding": np.linspace(-1e-4, 3e-4, len(ft))}, index=ft)   # high at the top
    falling = pd.DataFrame({"funding": np.linspace(3e-4, -1e-4, len(ft))}, index=ft)  # low at the top
    hi = attach_funding(df, rising, "1h")
    lo = attach_funding(df, falling, "1h")
    assert len(run(prepare(hi, "1h", None, cfg), cfg, lay, signals_only=True)) == 1
    assert len(run(prepare(lo, "1h", None, cfg), cfg, lay, signals_only=True)) == 0
    assert (invert(hi)["funding"] == -hi["funding"]).all()
    # funding at a bar is the last value known at that bar's close
    assert hi["funding"].iloc[8] == rising["funding"].iloc[1]


def test_funding_parsers():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backtest"))
    import fetch_funding as ff
    assert ff.parse_binance([{"symbol": "BTCUSDT", "fundingTime": 1700000000000, "fundingRate": "0.0001"}]) \
        == [(1700000000000, 0.0001)]
    assert ff.parse_bybit({"result": {"list": [{"fundingRate": "-0.00005", "fundingRateTimestamp": "1700028800000"}]}}) \
        == [(1700028800000, -0.00005)]
    assert ff.parse_deribit({"result": [{"timestamp": 1700000000000, "interest_8h": 0.0002, "interest_1h": 2e-5}]}) \
        == [(1700000000000, 0.0002)]
