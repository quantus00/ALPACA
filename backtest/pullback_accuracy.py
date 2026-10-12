"""STEP 1 — reversal detection accuracy for the 1h-break -> 15m-pullback entry.

Grades every entry (no trade management, no targets) the same way as
backtest/reversal_accuracy.py, in dollars, for tops (short entries) and bottoms
(long entries), and compares the 15m pullback entry with entering right at the
1h signal:

  entries      how many entries fired
  valid        share where price then moved the entry's way by at least
               max(how late the entry was, confirm_atr x 1h ATR) before closing
               back beyond the swing top/bottom (or the next opposite entry)
  late         $ between the real swing top/bottom and the entry price
  move         $ price went the entry's way afterwards
  big caught   big real turns (>= big_turn_pct, 1h bars, hindsight) with an
               entry before the next big turn; "late on big" = $ from that turn

STEP 2 (win rate with 2:1 / 3:1 targets) is backtest/pullback_backtest.py.

    python3 backtest/pullback_accuracy.py --symbol BTC
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from pullback_backtest import PREFIX, dollars, load, money  # noqa: E402
from reversal.accuracy import grade_turns  # noqa: E402
from reversal.detector import LAYERS, invert, load_config, prepare, run  # noqa: E402
from reversal.ltf_entry import simulate  # noqa: E402

HERE = Path(__file__).parent


def grade(entries: list[dict], frame: pd.DataFrame, atr1h: np.ndarray, opp_idx: np.ndarray,
          k_atr: float, upp: float, side: str, sign: float) -> list[dict]:
    lo, cl = frame["low"].to_numpy(float), frame["close"].to_numpy(float)
    n = len(cl)
    rows = []
    for e in entries:
        i, S, E = e["entry_i"], e["entry_raw"], e["extreme_raw"]
        nxt = opp_idx[opp_idx > i]
        stop_at = int(nxt[0]) if len(nxt) else n
        beyond = np.nonzero(cl[i + 1:stop_at] > E)[0]
        end = i + 1 + int(beyond[0]) if len(beyond) else stop_at
        move = S - lo[i + 1:end].min() if end > i + 1 else 0.0
        late = E - S
        rows.append({"side": side, "signal_time": e["entry_time"], "signal_price": sign * S,
                     "extreme_price": sign * E, "late_usd": late * upp, "move_usd": move * upp,
                     "valid": bool(move > 0 and move >= max(late, k_atr * atr1h[e["signal_i"]]))})
    return rows


def stats(g: pd.DataFrame, turns: pd.DataFrame, side: str, upp: float) -> str:
    s = g[g.side == side]
    t = turns[turns.turn == side]
    c = t[t.caught]
    big = f"{len(c)}/{len(t)} | {money(c.late_usd.mean() * upp) if len(c) else '–'}"
    if s.empty:
        return f"0 | – | – | – | {big}"
    return f"{len(s)} | {s.valid.mean():.0%} | {money(s.late_usd.mean())} | {money(s.move_usd.mean())} | {big}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC", choices=["BTC", "GOLD", "MES"])
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--data-dir", default=str(HERE / "data" / "reversal"))
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = load_config(a.config)
    sym, prefix, data_dir = a.symbol, a.prefix or PREFIX[a.symbol], Path(a.data_dir)
    upp, _ = dollars(cfg, sym)
    k_atr = cfg["accuracy"]["confirm_atr"]

    d1h, d1d, d15 = load(data_dir, prefix, "1h"), load(data_dir, prefix, "1d"), load(data_dir, prefix, "15m")
    p15, p15i = prepare(d15, "15m", None, cfg), prepare(invert(d15), "15m", None, cfg)
    end = d15.index[-1]
    windows = {"full": d1h.index[0] + pd.Timedelta(days=60),
               "3y": end - pd.DateOffset(years=3), "12m": end - pd.DateOffset(years=1)}
    unit = "per 1 BTC" if sym == "BTC" else f"for {cfg['instruments'][sym]['contracts']} contract(s)"

    lines = [f"# STEP 1 — {sym} reversal detection accuracy: 1h break vs 15m pullback entry ($ {unit})", "",
             f"Valid = price then moved the entry's way by at least max(how late it was, {k_atr:g} x 1h ATR) "
             f"before closing back beyond the swing top/bottom. Big turns = swings reversing >= "
             f"{cfg['accuracy']['big_turn_pct']:.0%} on 1h bars (hindsight).", "",
             "Columns per side: entries | valid | avg $ late vs real top/bottom | avg $ move after | "
             "big turns caught | avg $ late on big turns", ""]
    for vname, lay in (("structure only", {k: False for k in LAYERS}),
                       ("structure + daily filter", {**{k: False for k in LAYERS}, "htf": True})):
        h1 = prepare(d1h, "1h", d1d, cfg)
        h1i = prepare(invert(d1h), "1h", invert(d1d), cfg)
        atr1h = h1["atr"].to_numpy(float)
        tops = run(h1, cfg, lay, signals_only=True)
        bots = run(h1i, cfg, lay, signals_only=True)
        rows_by_w = {w: [] for w in windows}
        for mode, label in (("immediate", "at 1h signal"), ("pullback", "15m pullback")):
            sh = simulate(tops, p15, cfg, 2.0, mode, 1.0, "short", one_at_a_time=False)
            lg = simulate(bots, p15i, cfg, 2.0, mode, -1.0, "long", one_at_a_time=False)
            si = np.array([e["entry_i"] for e in sh], int)
            li = np.array([e["entry_i"] for e in lg], int)
            g = pd.DataFrame(grade(sh, p15, atr1h, li, k_atr, upp, "top", 1.0) +
                             grade(lg, p15i, atr1h, si, k_atr, upp, "bottom", -1.0))
            g = g.sort_values("signal_time").reset_index(drop=True)
            turns = grade_turns(d1h, g, cfg)
            tag = "daily_filter" if "daily" in vname else "structure"
            g.to_csv(HERE / "pullback_logs" / f"{sym}_{tag}_{mode}_entries_graded.csv", index=False)
            for w, s in windows.items():
                gw, tw = g[g.signal_time >= s], turns[turns.turn_time >= s]
                rows_by_w[w].append(f"| {label} | {stats(gw, tw, 'top', upp)} | {stats(gw, tw, 'bottom', upp)} |")
        for w, s in windows.items():
            lines += [f"## 1h setup: {vname} — {w} (from {s:%b %Y})", "",
                      "| entry | TOPS: entries | valid | late | move | big caught | late on big "
                      "| BOTTOMS: entries | valid | late | move | big caught | late on big |",
                      "|---|" + "---|" * 12] + rows_by_w[w] + [""]
        print(vname, "done")
    out = HERE / f"pullback_accuracy_{sym}.md"
    out.write_text("\n".join(lines))
    print("wrote", out)


if __name__ == "__main__":
    main()
