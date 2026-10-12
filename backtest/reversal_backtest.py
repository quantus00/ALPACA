"""Backtest the uptrend -> downtrend reversal detector (BTC futures short), in DOLLARS.

Every trade shorts `sizing.btc_per_trade` BTC (default 1 BTC) at the signal and
covers at the exit, after the dollar fees/slippage/funding in reversal/config.toml.

Runs each entry timeframe x layer variant over the full history once, then
scores the trades that ENTER inside each window (full / last 3y / last 12m).

Variants
  base            structure break only, entry on the break bar
  +<layer>        base plus one confluence layer
  all             every layer on (the strict set)
  all-<layer>     every layer except one (shows what each layer is worth)
  extra combos    passed with --combos "htf+divergence,htf+volume"

Reversal log (per trade, for the variants in --log):
  signal price vs the actual top it came from (how late the signal was),
  the actual bottom of that drop vs the exit price (how much of the drop was caught).

    python3 backtest/fetch_btc_history.py            # once, builds the bars
    python3 backtest/reversal_backtest.py [--tf 1h,4h,1d]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reversal.detector import LAYERS, TF_HOURS, load_config, position_size, prepare, run  # noqa: E402

HERE = Path(__file__).parent


def variants(combos: list[str]):
    off = {k: False for k in LAYERS}
    yield "base", off
    for k in LAYERS:
        yield f"+{k}", {**off, k: True}
    on = {k: True for k in LAYERS}
    yield "all", on
    for k in LAYERS:
        yield f"all-{k}", {**on, k: False}
    for c in combos:
        parts = c.split("+")
        yield c, {k: k in parts for k in LAYERS}


def load(data_dir: Path, tf: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"BTCUSD_{tf}.csv", index_col="time", parse_dates=["time"]).sort_index()


def reversal_log(d: pd.DataFrame, trades, qty: float) -> pd.DataFrame:
    """Signal vs actual top, and actual bottom vs exit, in dollars."""
    lo, hi, cl = d["low"].to_numpy(), d["high"].to_numpy(), d["close"].to_numpy()
    rows = []
    for n, t in enumerate(trades):
        nxt = trades[n + 1].entry_i if n + 1 < len(trades) else len(cl)
        # The drop "ends" when price closes back above the top, or at the next signal.
        above = np.nonzero(cl[t.entry_i:nxt] > t.top)[0]
        end = t.entry_i + (above[0] if len(above) else nxt - t.entry_i)
        seg = lo[t.entry_i:max(end, t.entry_i + 1)]
        b = t.entry_i + int(seg.argmin())
        rows.append({
            "signal_time": t.entry_time, "signal_price": round(t.entry_price, 2),
            "actual_top_time": t.top_time, "actual_top_price": round(t.top, 2),
            "late_by_usd": round((t.top - t.entry_price) * qty, 2),
            "actual_bottom_time": d.index[b], "actual_bottom_price": round(lo[b], 2),
            "possible_usd": round((t.entry_price - lo[b]) * qty, 2),
            "exit_time": t.exit_time, "exit_price": round(t.exit_price, 2), "exit_reason": t.exit_reason,
            "pnl_usd": round(t.pnl_usd, 2), "hours_held": t.bars_held,
        })
    return pd.DataFrame(rows)


def score(trades, start, end, tf, qty):
    ts = [t for t in trades if start <= t.entry_time < end]
    if not ts:
        return dict(n=0, win=np.nan, net=0.0, avg=np.nan, pf=np.nan, mdd=0.0, hold=np.nan, late=np.nan, best=np.nan, worst=np.nan)
    pnl = np.array([t.pnl_usd for t in ts])
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t in ts:
        for p in t.path[:-1]:                         # mark-to-market inside the trade
            v = eq + p * qty * t.entry_price
            mdd = max(mdd, peak - v)
        eq += t.pnl_usd
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return dict(n=len(ts), win=(pnl > 0).mean(), net=pnl.sum(), avg=pnl.mean(),
                pf=gains / losses if losses > 0 else np.inf, mdd=mdd,
                hold=np.mean([t.bars_held for t in ts]) * TF_HOURS[tf],
                late=np.mean([(t.top - t.entry_price) * qty for t in ts]),
                best=pnl.max(), worst=pnl.min())


def money(x):
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def fmt(s):
    if s["n"] == 0:
        return "0 | – | – | –"
    return f"{s['n']} | {s['win']:.0%} | {money(s['net'])} | {money(s['mdd'])}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h,4h,1d")
    ap.add_argument("--data-dir", default=str(HERE / "data" / "reversal"))
    ap.add_argument("--config", default=None)
    ap.add_argument("--combos", default="htf+divergence,htf+volume,htf+divergence+adx,htf+volume+macd")
    ap.add_argument("--log", default="all,+htf,htf+divergence", help="variants that get a reversal log CSV")
    ap.add_argument("--out", default=str(HERE / "reversal_results.md"))
    a = ap.parse_args()
    cfg = load_config(a.config)
    qty, fee_btc, _ = position_size(cfg)
    prod = cfg["sizing"]["product"]
    data_dir = Path(a.data_dir)
    combos = [c for c in a.combos.split(",") if c]
    log_names = set(a.log.split(","))
    out_dir = HERE / "reversal_logs"
    out_dir.mkdir(exist_ok=True)

    c = cfg["costs"]
    lines = ["# Reversal detector backtest — BTC/USD, futures short, in dollars", "",
             f"Each signal shorts **{cfg['sizing']['contracts']} x {prod}** = {qty:g} BTC of futures exposure. "
             f"Costs per BTC per side: fee ${fee_btc:,.2f} + slippage ${c['slippage_usd_per_btc_side']:g}; "
             f"funding {c['funding_per_8h']:g} per 8h (perp only). Prices are BTC spot used as a proxy for the futures price. "
             "Columns per window: trades | win rate | net P&L | max drawdown (mark-to-market, $).", ""]
    rows = []
    for tf in a.tf.split(","):
        htf = cfg["htf"]["map"][tf]
        d = prepare(load(data_dir, tf), tf, load(data_dir, htf), cfg)
        end = d.index[-1] + pd.Timedelta(hours=TF_HOURS[tf])
        windows = {"full": d.index[0] + pd.Timedelta(days=60),      # skip indicator warm-up
                   "3y": end - pd.DateOffset(years=3), "12m": end - pd.DateOffset(years=1)}
        bh = {w: (d["close"].iloc[-1] - d.loc[d.index >= s, "close"].iloc[0]) * qty for w, s in windows.items()}
        lines += [f"## Entry timeframe {tf} (higher-timeframe filter: {htf})", "",
                  f"Holding {qty:g} BTC over each window: " + ", ".join(f"{w} {money(v)}" for w, v in bh.items()), "",
                  "| variant | " + " | ".join(f"{w}: trades | win | net | maxDD" for w in windows) + " |",
                  "|---|" + "---|" * (4 * len(windows))]
        for name, lay in variants(combos):
            trades = run(d, cfg, lay)
            cells = []
            for w, s in windows.items():
                sc = score(trades, s, end, tf, qty)
                cells.append(fmt(sc))
                rows.append(dict(tf=tf, variant=name, window=w, **sc))
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
            if name in log_names:
                reversal_log(d, trades, qty).to_csv(out_dir / f"{tf}_{name.replace('+', 'plus_')}.csv", index=False)
        lines.append("")
        print(f"{tf} done")

    pd.DataFrame(rows).to_csv(HERE / "reversal_summary.csv", index=False)
    lines.append(f"Data through {end}. Per-trade reversal logs (signal vs actual top/bottom) in backtest/reversal_logs/.")
    Path(a.out).write_text("\n".join(lines))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
