"""Win rate at fixed R:R for the best reversal setup from backtest/reversal_search.py.

Setup (chosen on 2017-2023 only): 1h swing size 3, 4h EMA50 trend filter,
RSI divergence, volume spike, MACD. Shorts after tops, longs after bottoms.
Entry at the 1h signal close (and, for comparison, on a 15m pullback).
Stop beyond the 1h lower high / higher low; target = R x risk.
Reports TRAIN (tuning years) and TEST (unseen) separately, per 1 contract.

    python3 backtest/best_setup_winrate.py [--split 2024-01-01]
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from pullback_backtest import dollars, load, money  # noqa: E402
from reversal.detector import LAYERS, invert, load_config, prepare, run  # noqa: E402
from reversal.ltf_entry import simulate  # noqa: E402

HERE = Path(__file__).parent
ET = "America/New_York"
BEST = dict(pivot=3, htf_tf="4h", ema=50, layers=dict(divergence=True, volume=True, macd=True, htf=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2024-01-01")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    split = pd.Timestamp(a.split, tz="UTC")
    cfg = copy.deepcopy(load_config(a.config))
    cfg["structure"]["pivot_lookback"] = BEST["pivot"]
    cfg["htf"]["map"]["1h"], cfg["htf"]["ema_length"] = BEST["htf_tf"], BEST["ema"]
    lay = {k: False for k in LAYERS}
    lay.update(BEST["layers"])
    upp, cost = dollars(cfg, "BTC")
    data = HERE / "data" / "reversal"
    d1h, d4h, d15 = load(data, "BTCUSD", "1h"), load(data, "BTCUSD", "4h"), load(data, "BTCUSD", "15m")
    h1, h1i = prepare(d1h, "1h", d4h, cfg), prepare(invert(d1h), "1h", invert(d4h), cfg)
    p15, p15i = prepare(d15, "15m", None, cfg), prepare(invert(d15), "15m", None, cfg)
    tops, bots = run(h1, cfg, lay, signals_only=True), run(h1i, cfg, lay, signals_only=True)

    lines = ["# Win rate — best reversal setup (swing 3, 4h EMA50, divergence, volume, MACD)", "",
             f"Per 1 nano contract (0.01 BTC): ${upp:.2f} per $1 BTC move, ${cost:.2f} fees+slippage per round trip. "
             f"TRAIN = 2017 to {a.split} (used to choose the setup); TEST = {a.split} to Oct 2026 (unseen). "
             f"{len(tops)} top signals, {len(bots)} bottom signals in total.", "",
             "Break-even win rate: 33% at 2:1, 25% at 3:1 (before costs).", "",
             "| entry | R:R | side | TRAIN trades | win | net | TEST trades | win | net | test maxDD |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    logs = []
    for mode, label in (("immediate", "at 1h signal"), ("pullback", "15m pullback")):
        for rr in cfg["ltf"]["rr"]:
            tr = pd.DataFrame(simulate(tops, p15, cfg, rr, mode, 1.0, "short") +
                              simulate(bots, p15i, cfg, rr, mode, -1.0, "long"))
            if tr.empty:
                continue
            tr["pnl_usd"] = tr.points * upp - cost
            tr = tr.sort_values("entry_time").reset_index(drop=True)
            for side in ("short", "long", "both"):
                s = tr if side == "both" else tr[tr.side == side]
                cells = []
                for part in (s[s.entry_time < split], s[s.entry_time >= split]):
                    cells.append(f"{len(part)} | {(part.pnl_usd > 0).mean():.0%} | {money(part.pnl_usd.sum())}"
                                 if len(part) else "0 | – | –")
                te = s[s.entry_time >= split].pnl_usd.cumsum()
                mdd = float((te.cummax().clip(lower=0) - te).max()) if len(te) else 0.0
                lines.append(f"| {label} | {rr:g}:1 | {side} | {cells[0]} | {cells[1]} | {money(mdd)} |")
            out = tr.assign(entry_mode=label, rr=f"{rr:g}:1")
            logs.append(out)
    lg = pd.concat(logs)
    for c in ("signal_time", "entry_time", "exit_time"):
        lg[c] = lg[c].dt.tz_convert(ET).dt.strftime("%Y-%m-%d %I:%M %p")
    keep = ["entry_mode", "rr", "side", "signal_time", "entry_time", "entry", "stop", "target",
            "exit_time", "exit", "reason", "hours", "pnl_usd"]
    lg[keep].round(2).to_csv(HERE / "best_setup_trades.csv", index=False)
    lines += ["", "Trade log (New York time): backtest/best_setup_trades.csv. "
              "Same-bar stop and target touches count as a stop."]
    (HERE / "best_setup_winrate.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
