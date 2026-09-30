"""
Point-movement direction engine (alternative to structure trend).
==================================================================
Instead of HH/HL swing structure, decide direction from how far higher-timeframe
price MOVED. Supports every option the user asked to "try all":

  direction modes:
    momentum : long if HTF close > close N bars ago (down if below)
    threshold: only flip once the HTF move over N bars is >= a threshold
    candle   : sign of the last completed HTF candle body (close-open)

  threshold units (for 'threshold' mode):
    atr   : threshold = k * ATR(HTF)      (volatility-normalized)
    pct   : threshold = k% of price
    points: raw points (set per asset)

The "scale number" is the HTF size (minutes) and/or N bars back — larger = fewer,
slower, cleaner signals. This module reuses multi_asset.sim_hybrid for the actual
trade simulation, only swapping how direction is decided.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mes_backtest import resample  # type: ignore
from multi_asset import ASSETS, Trend, atr, sim_hybrid, stats, load_bars  # type: ignore

DD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Raw-point thresholds per asset (~1 HTF ATR-ish), used only for units='points'.
POINT_THR = {"BTC(D)": 1500.0, "SPY(D)": 8.0, "GOLD(60m)": 20.0, "MES(60m)": 20.0}


class PMove:
    def __init__(self, bars, minutes, mode="momentum", N=1, k=1.0, units="atr",
                 point_thr=0.0):
        h = resample(bars, minutes)
        n = len(h)
        hatr = atr(h)
        state = [0] * n
        cur = 0
        for i in range(n):
            if mode == "candle":
                d = 1 if h[i].c > h[i].o else (-1 if h[i].c < h[i].o else cur)
            else:
                j = i - N
                if j < 0:
                    d = cur
                else:
                    move = h[i].c - h[j].c
                    if mode == "momentum":
                        d = 1 if move > 0 else (-1 if move < 0 else cur)
                    else:  # threshold
                        if units == "atr":
                            T = k * hatr[i]
                        elif units == "pct":
                            T = k / 100.0 * h[i].c
                        else:
                            T = point_thr
                        d = 1 if move >= T else (-1 if move <= -T else cur)
            cur = d
            state[i] = d
        self.ct = [int(x.start.timestamp()) + minutes * 60 for x in h]
        self.state = state

    def at(self, t):
        ts = int(t.timestamp()); s = 0
        for i, c in enumerate(self.ct):
            if c <= ts: s = self.state[i]
            else: break
        return s


def run():
    hdr = f"{'asset':<10}{'direction':<22}{'trd':>5}{'win%':>7}{'netR':>7}{'PF':>6}{'maxDD$':>10}"
    print(hdr); print("-" * len(hdr))
    lines = [hdr, "-" * len(hdr)]
    for A in ASSETS:
        bars = load_bars(os.path.join(DD, A.file))
        tm = A.trend_min
        pt = POINT_THR.get(A.name, 0.0)
        variants = [
            ("structure (baseline)", Trend(bars, tm)),
            ("momentum N1", PMove(bars, tm, "momentum", N=1)),
            ("momentum N3", PMove(bars, tm, "momentum", N=3)),
            ("candle", PMove(bars, tm, "candle")),
            ("thr atr k1 N3", PMove(bars, tm, "threshold", N=3, k=1.0, units="atr")),
            ("thr pct 1% N3", PMove(bars, tm, "threshold", N=3, k=1.0, units="pct")),
            ("thr points N3", PMove(bars, tm, "threshold", N=3, units="points", point_thr=pt)),
            ("momentum N1 @2x TF", PMove(bars, tm * 2, "momentum", N=1)),
        ]
        for label, direction in variants:
            s = stats(sim_hybrid(bars, direction, A, core_tp=3.0, core_sl=1.5, scale=False))
            if not s:
                continue
            pf = "inf" if s["pf"] == 9.99 else f"{s['pf']:.2f}"
            row = (f"{A.name:<10}{label:<22}{s['n']:>5}{s['win']:>6.1f}%"
                   f"{s['netR']:>7.1f}{pf:>6}{s['dd']:>10.0f}")
            print(row); lines.append(row)
        print(); lines.append("")
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "point_move_results.txt"), "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    run()
