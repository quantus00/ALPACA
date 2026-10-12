"""How accurately does the detector call reversals — tops AND bottoms — in dollars?

For each entry timeframe x layer variant x window it reports, separately for
top (up->down) and bottom (down->up) signals:
  signals         how many reversal signals fired
  valid           share that followed through (see reversal/accuracy.py)
  avg/median late $ between the real top/bottom and the signal price
  avg move $      how far price went the signal's way afterwards
  big turns       big real turns (>= big_turn_pct, hindsight) caught / total
  late on big     average $ from those real turns to the first signal

    python3 backtest/reversal_accuracy.py [--tf 1h,4h,1d]

Writes backtest/reversal_accuracy.md and per-signal / per-turn CSVs in
backtest/reversal_accuracy/. All dollars are per 1 BTC.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reversal.accuracy import grade_signals, grade_turns, signals_both  # noqa: E402
from reversal.detector import LAYERS, TF_HOURS, load_config  # noqa: E402

HERE = Path(__file__).parent


def variants(extra: list[str]):
    off = {k: False for k in LAYERS}
    yield "base", off
    for k in LAYERS:
        yield f"+{k}", {**off, k: True}
    yield "all", {k: True for k in LAYERS}
    for c in extra:
        yield c, {k: k in c.split("+") for k in LAYERS}


def load(data_dir: Path, tf: str) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"BTCUSD_{tf}.csv", index_col="time", parse_dates=["time"]).sort_index()


def money(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def stats(sig: pd.DataFrame, turns: pd.DataFrame, side: str) -> dict:
    s = sig[sig.side == side] if len(sig) else sig
    t = turns[turns.turn == side]
    caught = t[t.caught]
    return dict(n=len(s), valid=s.valid.mean() if len(s) else np.nan,
                late_avg=s.late_usd.mean() if len(s) else np.nan,
                late_med=s.late_usd.median() if len(s) else np.nan,
                move_avg=s.move_usd.mean() if len(s) else np.nan,
                big=len(t), big_caught=len(caught),
                big_late=caught.late_usd.mean() if len(caught) else np.nan)


def cell(st: dict) -> str:
    if st["n"] == 0:
        return f"0 | – | – | – | 0/{st['big']} | –"
    return (f"{st['n']} | {st['valid']:.0%} | {money(st['late_avg'])} | {money(st['move_avg'])} | "
            f"{st['big_caught']}/{st['big']} | {money(st['big_late'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="1h,4h,1d")
    ap.add_argument("--data-dir", default=str(HERE / "data" / "reversal"))
    ap.add_argument("--config", default=None)
    ap.add_argument("--extra", default="htf+divergence,htf+volume")
    ap.add_argument("--csv", default="base,+htf,htf+divergence", help="variants to write per-signal CSVs for")
    ap.add_argument("--out", default=str(HERE / "reversal_accuracy.md"))
    a = ap.parse_args()
    cfg = load_config(a.config)
    data_dir = Path(a.data_dir)
    extra = [c for c in a.extra.split(",") if c]
    csv_for = set(a.csv.split(","))
    out_dir = HERE / "reversal_accuracy"
    out_dir.mkdir(exist_ok=True)
    acc = cfg["accuracy"]

    lines = ["# Reversal accuracy — tops AND bottoms, BTC/USD, dollars per 1 BTC", "",
             f"Valid = price then moved the signal's way by at least max(how late the signal was, "
             f"{acc['confirm_atr']:g} x ATR) before closing back beyond the top/bottom. "
             f"Big turns = real swings that reversed >= {acc['big_turn_pct']:.0%} (found with hindsight).", "",
             "Columns per side: signals | valid | avg $ late vs the real top/bottom | avg $ move after the signal | "
             "big turns caught | avg $ late on caught big turns", ""]
    rows = []
    for tf in a.tf.split(","):
        htf = cfg["htf"]["map"][tf]
        df, hdf = load(data_dir, tf), load(data_dir, htf)
        ref = load(data_dir, "1h")          # big turns always measured on 1h bars
        end = df.index[-1] + pd.Timedelta(hours=TF_HOURS[tf])
        windows = {"full": df.index[0] + pd.Timedelta(days=60),
                   "3y": end - pd.DateOffset(years=3), "12m": end - pd.DateOffset(years=1)}
        for w, start in windows.items():
            lines += [f"## {tf} chart — {w} (from {start:%b %Y})", "",
                      "| variant | TOPS: signals | valid | late | move | big caught | late on big "
                      "| BOTTOMS: signals | valid | late | move | big caught | late on big |",
                      "|---|" + "---|" * 12]
            lines.append("{rows_" + f"{tf}_{w}" + "}")
            lines.append("")
        table: dict[str, list[str]] = {w: [] for w in windows}
        for name, lay in variants(extra):
            d, di, tops, bottoms = signals_both(df, hdf, tf, cfg, lay)
            sig = grade_signals(d, di, tops, bottoms, cfg)
            turns = grade_turns(ref, sig, cfg)
            if name in csv_for:
                tag = name.replace("+", "plus_")
                sig.to_csv(out_dir / f"{tf}_{tag}_signals.csv", index=False)
                turns.to_csv(out_dir / f"{tf}_{tag}_big_turns.csv", index=False)
            for w, start in windows.items():
                sw = sig[sig.signal_time >= start] if len(sig) else sig
                tw = turns[turns.turn_time >= start]
                st_t, st_b = stats(sw, tw, "top"), stats(sw, tw, "bottom")
                table[w].append(f"| {name} | {cell(st_t)} | {cell(st_b)} |")
                rows += [dict(tf=tf, variant=name, window=w, side="top", **st_t),
                         dict(tf=tf, variant=name, window=w, side="bottom", **st_b)]
        text = "\n".join(lines)
        for w in windows:
            text = text.replace("{rows_" + f"{tf}_{w}" + "}", "\n".join(table[w]))
        lines = text.split("\n")
        print(f"{tf} done")

    pd.DataFrame(rows).to_csv(HERE / "reversal_accuracy_summary.csv", index=False)
    Path(a.out).write_text("\n".join(lines) + f"\n\nData through {end}.\n")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
