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
