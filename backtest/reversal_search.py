"""Search filter combinations for the highest reversal rate — tuned on TRAIN data,
reported on unseen TEST data.

Stage A (1h signals): every combination of
  swing size (1h pivot lookback), trend filter (none / daily EMA50 / 4h EMA50),
  key level (off / top is the highest high of 7 or 30 days), divergence, volume, MACD.
Stage B (15m pullback entry) on the best Stage-A setups: pullback depth 0.382 / 0.5 / 0.618.

"Reversal rate" = share of signals/entries graded VALID (same rule as
backtest/reversal_accuracy.py), reported separately for tops and bottoms.
The split date separates train from test; nothing is chosen using test data.

    python3 backtest/reversal_search.py [--split 2024-01-01] [--target 0.70]
Writes backtest/reversal_search.md and backtest/reversal_search_*.csv
"""
from __future__ import annotations

import argparse
import copy
import itertools
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from reversal.accuracy import grade_signals, grade_turns  # noqa: E402
from reversal.detector import LAYERS, attach_funding, invert, load_config, prepare, run  # noqa: E402

HERE = Path(__file__).parent
DATA = HERE / "data" / "reversal"
HTF = {"none": None, "1d_ema50": ("1d", 50), "4h_ema50": ("4h", 50)}
_G: dict = {}


def _load(tf):
    return pd.read_csv(DATA / f"BTCUSD_{tf}.csv", index_col="time", parse_dates=["time"]).sort_index()


def _init(cfg_path):
    _G["cfg"] = load_config(cfg_path)
    _G["bars"] = {tf: _load(tf) for tf in ("15m", "1h", "4h", "1d")}
    fpath = DATA / "BTC_funding.csv"
    if fpath.exists():                         # optional: perp funding history (fetch_funding.py)
        f = pd.read_csv(fpath, index_col="time", parse_dates=["time"])
        _G["bars"]["1h"] = attach_funding(_G["bars"]["1h"], f, "1h")
    _G["frames"] = {}


def _frames(htf: str, kl_days: int):
    key = (htf, kl_days)
    if key not in _G["frames"]:
        cfg = copy.deepcopy(_G["cfg"])
        cfg["key_level"]["lookback_days"] = max(kl_days, 1)
        b = _G["bars"]
        hdf = None
        if HTF[htf]:
            tf, ema = HTF[htf]
            cfg["htf"]["map"]["1h"], cfg["htf"]["ema_length"] = tf, ema
            hdf = b[tf]
        d = prepare(b["1h"], "1h", hdf, cfg)
        di = prepare(invert(b["1h"]), "1h", invert(hdf) if hdf is not None else None, cfg)
        _G["frames"][key] = (d, di)
    return _G["frames"][key]


def _summ(g: pd.DataFrame, turns: pd.DataFrame, split, prefix: str) -> dict:
    out = {}
    for part, sel_g, sel_t in (("train", g.signal_time < split, turns.turn_time < split),
                               ("test", g.signal_time >= split, turns.turn_time >= split)):
        for side in ("top", "bottom"):
            s = g[sel_g & (g.side == side)]
            t = turns[sel_t & (turns.turn == side)]
            out[f"{prefix}{part}_{side}_n"] = len(s)
            out[f"{prefix}{part}_{side}_valid"] = s.valid.mean() if len(s) else np.nan
            out[f"{prefix}{part}_{side}_late"] = s.late_usd.mean() if len(s) else np.nan
            out[f"{prefix}{part}_{side}_big"] = int(t.caught.sum())
            out[f"{prefix}{part}_{side}_bigtotal"] = len(t)
    return out


def stage_a(params):
    L, htf, kl, div, vol, macd, fund = params
    cfg = copy.deepcopy(_G["cfg"])
    cfg["structure"]["pivot_lookback"] = L
    d, di = _frames(htf, kl)
    lay = {k: False for k in LAYERS}
    lay.update(divergence=bool(div), volume=bool(vol), macd=bool(macd), htf=htf != "none", key_level=kl > 0,
               funding=bool(fund))
    tops = run(d, cfg, lay, signals_only=True)
    bots = run(di, cfg, lay, signals_only=True)
    g = grade_signals(d, di, tops, bots, cfg)
    if g.empty:
        return None
    turns = grade_turns(_G["bars"]["1h"], g, cfg)
    return dict(pivot=L, trend=htf, key_days=kl, divergence=div, volume=vol, macd=macd, funding=fund,
                **_summ(g, turns, _G["split"], ""))


def stage_b(params):
    from reversal.ltf_entry import simulate
    (L, htf, kl, div, vol, macd, fund), retr = params
    cfg = copy.deepcopy(_G["cfg"])
    cfg["structure"]["pivot_lookback"] = L
    cfg["ltf"]["min_retrace"] = retr
    d, di = _frames(htf, kl)
    lay = {k: False for k in LAYERS}
    lay.update(divergence=bool(div), volume=bool(vol), macd=bool(macd), htf=htf != "none", key_level=kl > 0,
               funding=bool(fund))
    tops = run(d, cfg, lay, signals_only=True)
    bots = run(di, cfg, lay, signals_only=True)
    if "p15" not in _G:
        _G["p15"] = prepare(_G["bars"]["15m"], "15m", None, cfg)
        _G["p15i"] = prepare(invert(_G["bars"]["15m"]), "15m", None, cfg)
    sh = simulate(tops, _G["p15"], cfg, 2.0, "pullback", 1.0, "short", one_at_a_time=False)
    lg = simulate(bots, _G["p15i"], cfg, 2.0, "pullback", -1.0, "long", one_at_a_time=False)
    from pullback_accuracy import grade
    atr1h = d["atr"].to_numpy(float)
    si = np.array([e["entry_i"] for e in sh], int)
    li = np.array([e["entry_i"] for e in lg], int)
    g = pd.DataFrame(grade(sh, _G["p15"], atr1h, li, cfg["accuracy"]["confirm_atr"], 1.0, "top", 1.0) +
                     grade(lg, _G["p15i"], atr1h, si, cfg["accuracy"]["confirm_atr"], 1.0, "bottom", -1.0))
    if g.empty:
        return None
    g = g.sort_values("signal_time").reset_index(drop=True)
    turns = grade_turns(_G["bars"]["1h"], g, cfg)
    return dict(pivot=L, trend=htf, key_days=kl, divergence=div, volume=vol, macd=macd, funding=fund, min_retrace=retr,
                **_summ(g, turns, _G["split"], ""))


def _worker_init(cfg_path, split):
    _init(cfg_path)
    _G["split"] = pd.Timestamp(split, tz="UTC")


def describe(r) -> str:
    parts = [f"swing {int(r.pivot)}", {"none": "no trend filter", "1d_ema50": "daily EMA50",
                                       "4h_ema50": "4h EMA50"}[r.trend]]
    if r.key_days:
        parts.append(f"{int(r.key_days)}-day high/low")
    parts += [n for n, on in (("divergence", r.divergence), ("volume", r.volume), ("MACD", r.macd),
                              ("funding extreme", r.get("funding", 0))) if on]
    if "min_retrace" in r and not pd.isna(r.get("min_retrace", np.nan)):
        parts.append(f"15m pullback {r.min_retrace:g}")
    return ", ".join(parts)


def table(df: pd.DataFrame, title: str) -> list[str]:
    lines = [f"### {title}", "",
             "| setup | TRAIN tops | TRAIN bottoms | TEST tops | TEST bottoms | test big turns caught (tops / bottoms) |",
             "|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        def c(part, side):
            n, v = r[f"{part}_{side}_n"], r[f"{part}_{side}_valid"]
            return f"{v:.0%} of {int(n)}" if n else "–"
        lines.append(f"| {describe(r)} | {c('train','top')} | {c('train','bottom')} | {c('test','top')} | "
                     f"{c('test','bottom')} | {int(r.test_top_big)}/{int(r.test_top_bigtotal)} / "
                     f"{int(r.test_bottom_big)}/{int(r.test_bottom_bigtotal)} |")
    return lines + [""]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2024-01-01")
    ap.add_argument("--target", type=float, default=0.70)
    ap.add_argument("--min-train", type=int, default=25, help="min train signals per side to count")
    ap.add_argument("--config", default=None)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()

    has_funding = (DATA / "BTC_funding.csv").exists()
    if has_funding:
        print("funding data found: adding the funding filter to the search")
    grid = list(itertools.product((3, 5, 8, 12, 20), HTF, (0, 7, 30), (0, 1), (0, 1), (0, 1),
                                  (0, 1) if has_funding else (0,)))
    with ProcessPoolExecutor(a.workers, initializer=_worker_init, initargs=(a.config, a.split)) as ex:
        res = [r for r in ex.map(stage_a, grid, chunksize=4) if r]
    A = pd.DataFrame(res)
    A["train_min"] = A[["train_top_valid", "train_bottom_valid"]].min(axis=1)
    A.to_csv(HERE / "reversal_search_stage_a.csv", index=False)
    ok = A[(A.train_top_n >= a.min_train) & (A.train_bottom_n >= a.min_train)]
    bestA = ok.sort_values("train_min", ascending=False).head(12)
    print("stage A done:", len(A), "configs")

    # Stage B: 15m pullback on the 12 best stage-A setups (chosen on TRAIN only)
    combos = [((int(r["pivot"]), r.trend, int(r.key_days), int(r.divergence), int(r.volume), int(r.macd),
                int(r.funding)), retr)
              for _, r in bestA.iterrows() for retr in (0.382, 0.5, 0.618)]
    with ProcessPoolExecutor(a.workers, initializer=_worker_init, initargs=(a.config, a.split)) as ex:
        resb = [r for r in ex.map(stage_b, combos) if r]
    B = pd.DataFrame(resb)
    B["train_min"] = B[["train_top_valid", "train_bottom_valid"]].min(axis=1)
    B.to_csv(HERE / "reversal_search_stage_b.csv", index=False)
    okB = B[(B.train_top_n >= 15) & (B.train_bottom_n >= 15)]
    print("stage B done:", len(B), "configs")

    base = A[(A["pivot"] == 5) & (A.trend == "none") & (A.key_days == 0) & (A.divergence == 0) & (A.volume == 0) & (A.macd == 0) & (A.funding == 0)]
    lines = [f"# Reversal-rate search (BTC) — target {a.target:.0%} on tops AND bottoms", "",
             f"Tuned on TRAIN = 2017 to {a.split}; TEST = {a.split} to Oct 2026 was never used to choose. "
             "Rate = share of signals graded VALID (moved the signal's way by at least max(how late it was, "
             "2 x 1h ATR) before closing back beyond the swing top/bottom). Cells: rate of N signals.", "",
             f"Stage A tested {len(A)} combinations at the 1h signal; stage B tested {len(B)} 15m-pullback "
             "versions of the 12 best.", ""]
    lines += table(base, "Starting point (current detector, 1h signal)")
    lines += table(bestA, f"Stage A — best 12 by the WORSE of train tops/bottoms (min {a.min_train} train signals each)")
    lines += table(okB.sort_values("train_min", ascending=False).head(12),
                   "Stage B — best 15m pullback versions (min 15 train entries each side)")
    hit = pd.concat([ok, okB])
    hit = hit[(hit.train_min >= a.target)]
    lines.append(f"Setups reaching {a.target:.0%} on BOTH sides in TRAIN: {len(hit)}.")
    if len(hit):
        hit = hit.assign(test_min=hit[["test_top_valid", "test_bottom_valid"]].min(axis=1))
        lines += [""] + table(hit.sort_values("test_min", ascending=False).head(10),
                              "Those setups on unseen TEST data")
    (HERE / "reversal_search.md").write_text("\n".join(lines))
    print("wrote backtest/reversal_search.md")


if __name__ == "__main__":
    main()
