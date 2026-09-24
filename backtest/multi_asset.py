"""
Cross-asset backtest of the two chosen strategies, volatility-normalized.
======================================================================
Runs the SAME two strategies on very different assets (BTC, SPY, gold, MES) by
sizing every stop/target in ATR units, so a "stop" means the same *risk* on each
instrument. Entries are pullback-in-trend only (no asset-specific 09:30 open),
so the logic is identical everywhere.

Strategies
  TREND-FOLLOWER (option 2): enter pullback with the higher-TF trend; disaster
    stop at sl_mult*ATR; exit (take profit) when the trend flips against you.
  HYBRID (option 1): 1 unit, core bracket tp/sl in ATR; optional SCALE toggle —
    add on strength (+add_mult*ATR) then a wider ATR bracket + trend-flip exit.

Results are reported in $ (per 1 unit) and in R-multiples (P&L / initial risk),
so assets are comparable. See CROSS_ASSET.md for the write-up.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from mes_backtest import load_bars, resample, trend_states  # type: ignore

HERE = os.path.dirname(os.path.abspath(__file__))
COMMISSION_FRAC = 0.0  # per-asset commissions vary; report gross + a flat bps below
SLIP_BPS = 1.0         # 1 basis point per side as a light cost proxy


@dataclass
class Asset:
    name: str
    file: str
    entry_min: int         # bar size of the file, in minutes
    trend_min: int         # higher timeframe for the structure trend
    point_value: float     # $ per 1.0 price move, per unit


ASSETS = [
    Asset("BTC(D)", "BTCUSD_D.json", 1440, 1440 * 7, 1.0),
    Asset("SPY(D)", "SPY_D.json", 1440, 1440 * 7, 1.0),
    Asset("GOLD(60m)", "GCZ6_M60.json", 60, 240, 10.0),
    Asset("MES(60m)", "MEScont_M60.json", 60, 240, 5.0),
]


class Trend:
    def __init__(self, bars, minutes):
        b = resample(bars, minutes)
        self.ct = [int(x.start.timestamp()) + minutes * 60 for x in b]
        self.state = trend_states(b)

    def at(self, t):
        ts = int(t.timestamp()); s = 0
        for i, c in enumerate(self.ct):
            if c <= ts: s = self.state[i]
            else: break
        return s


def atr(bars, n=14):
    out = [0.0] * len(bars)
    trs = []
    for i, b in enumerate(bars):
        tr = b.h - b.l if i == 0 else max(b.h - b.l, abs(b.h - bars[i - 1].c), abs(b.l - bars[i - 1].c))
        trs.append(tr)
        out[i] = sum(trs[-n:]) / min(len(trs), n)
    return out


def _pullback_signals(bars, trend):
    """Yield (index, dir) for pullback-in-trend entries. Fires on EVERY pullback
    while the trend holds (dip against the trend, then a close that resumes it),
    not just the first one after a flip."""
    sig = [0] * len(bars)
    pulled = False
    for i in range(1, len(bars)):
        b = bars[i]; st = trend.at(b.start)
        if st == 1:
            if b.l < bars[i - 1].l:
                pulled = True
            if pulled and b.c > bars[i - 1].h:
                sig[i] = 1; pulled = False
        elif st == -1:
            if b.h > bars[i - 1].h:
                pulled = True
            if pulled and b.c < bars[i - 1].l:
                sig[i] = -1; pulled = False
        else:
            pulled = False
    return sig


def sim_trend_follower(bars, trend, A, sl_mult=2.0):
    at = atr(bars); sig = _pullback_signals(bars, trend); pv = A.point_value
    T = []; pos = None
    for i, b in enumerate(bars):
        st = trend.at(b.start)
        if pos is not None:
            d = pos['dir']
            hit = (b.l <= pos['sl']) if d > 0 else (b.h >= pos['sl'])
            if hit:
                px = pos['sl']; pnl = (px - pos['ref']) * d * pv - pos['cost']
                T.append((pnl, pos['risk'])); pos = None
            elif st == -d:  # trend flip -> exit (take profit if ahead)
                px = b.c; pnl = (px - pos['ref']) * d * pv - pos['cost']
                T.append((pnl, pos['risk'])); pos = None
        if pos is None and sig[i] != 0 and at[i] > 0:
            d = sig[i]; ref = b.c; risk = sl_mult * at[i] * pv
            pos = dict(dir=d, ref=ref, sl=ref - sl_mult * at[i] if d > 0 else ref + sl_mult * at[i],
                       risk=risk, cost=abs(ref) * SLIP_BPS / 1e4 * pv * 2)
    if pos is not None:
        d = pos['dir']; T.append(((bars[-1].c - pos['ref']) * d * pv - pos['cost'], pos['risk']))
    return T


def sim_hybrid(bars, trend, A, core_tp=3.0, core_sl=1.0, scale=False,
               add_mult=1.5, add_qty=7, s_tp=6.0, s_sl=2.0, flip=True):
    at = atr(bars); sig = _pullback_signals(bars, trend); pv = A.point_value
    T = []; pos = None
    for i, b in enumerate(bars):
        st = trend.at(b.start)
        if pos is not None:
            d = pos['dir']
            if pos['stage'] == 'pre_scale':
                # waiting to add on strength; stop at core_sl
                hit = (b.l <= pos['sl']) if d > 0 else (b.h >= pos['sl'])
                fav = (b.h >= pos['add']) if d > 0 else (b.l <= pos['add'])
                if hit:
                    pnl = (pos['sl'] - pos['ref']) * d * pos['qty'] * pv - pos['cost']
                    T.append((pnl, pos['risk'])); pos = None
                elif fav:
                    ap = pos['add']; nq = pos['qty'] + add_qty
                    pos['ref'] = (pos['ref'] * pos['qty'] + ap * add_qty) / nq; pos['qty'] = nq
                    pos['stage'] = 'post'
                    pos['tp'] = pos['ref'] + s_tp * pos['atr'] if d > 0 else pos['ref'] - s_tp * pos['atr']
                    pos['sl'] = pos['ref'] - s_sl * pos['atr'] if d > 0 else pos['ref'] + s_sl * pos['atr']
            else:
                hs = (b.l <= pos['sl']) if d > 0 else (b.h >= pos['sl'])
                ht = (b.h >= pos['tp']) if d > 0 else (b.l <= pos['tp'])
                if hs:
                    pnl = (pos['sl'] - pos['ref']) * d * pos['qty'] * pv - pos['cost']
                    T.append((pnl, pos['risk'])); pos = None
                elif ht:
                    pnl = (pos['tp'] - pos['ref']) * d * pos['qty'] * pv - pos['cost']
                    T.append((pnl, pos['risk'])); pos = None
                elif scale and flip and st == -d:
                    pnl = (b.c - pos['ref']) * d * pos['qty'] * pv - pos['cost']
                    T.append((pnl, pos['risk'])); pos = None
        if pos is None and sig[i] != 0 and at[i] > 0:
            d = sig[i]; ref = b.c; a = at[i]; risk = core_sl * a * pv
            cost = abs(ref) * SLIP_BPS / 1e4 * pv * 2
            if scale:
                pos = dict(dir=d, ref=ref, qty=1, stage='pre_scale', atr=a, risk=risk, cost=cost,
                           sl=ref - core_sl * a if d > 0 else ref + core_sl * a,
                           add=ref + add_mult * a if d > 0 else ref - add_mult * a)
            else:
                pos = dict(dir=d, ref=ref, qty=1, stage='single', atr=a, risk=risk, cost=cost,
                           tp=ref + core_tp * a if d > 0 else ref - core_tp * a,
                           sl=ref - core_sl * a if d > 0 else ref + core_sl * a)
    if pos is not None:
        d = pos['dir']; T.append(((bars[-1].c - pos['ref']) * d * pos['qty'] * pv - pos['cost'], pos['risk']))
    return T


def stats(T):
    n = len(T)
    if n == 0: return None
    net = sum(p for p, _ in T); wins = [p for p, _ in T if p > 0]
    gl = -sum(p for p, _ in T if p <= 0); gw = sum(wins)
    Rs = [p / r for p, r in T if r > 0]
    eq = peak = dd = 0.0
    for p, _ in T: eq += p; peak = max(peak, eq); dd = min(dd, eq - peak)
    return dict(n=n, net=net, win=100 * len(wins) / n, pf=(gw / gl if gl > 0 else 9.99),
                dd=dd, expR=sum(Rs) / len(Rs) if Rs else 0, netR=sum(Rs) if Rs else 0)


def main():
    hdr = f"{'asset':<10}{'strategy':<16}{'span':<22}{'trd':>5}{'win%':>7}{'net$':>11}{'expR':>7}{'netR':>7}{'PF':>6}{'maxDD$':>11}"
    lines = [hdr, "-" * len(hdr)]
    print(hdr); print("-" * len(hdr))
    for A in ASSETS:
        bars = load_bars(os.path.join(HERE, "data", A.file))
        span = f"{bars[0].et:%Y-%m-%d}->{bars[-1].et:%Y-%m-%d}"
        trend = Trend(bars, A.trend_min)
        runs = {
            "trend_follow": sim_trend_follower(bars, trend, A, sl_mult=2.0),
            "hybrid_core": sim_hybrid(bars, trend, A, scale=False),
            "hybrid_scale": sim_hybrid(bars, trend, A, scale=True),
        }
        for name, T in runs.items():
            s = stats(T)
            if not s: continue
            pf = "inf" if s['pf'] == 9.99 else f"{s['pf']:.2f}"
            row = (f"{A.name:<10}{name:<16}{span:<22}{s['n']:>5}{s['win']:>6.1f}%"
                   f"{s['net']:>11.0f}{s['expR']:>7.2f}{s['netR']:>7.1f}{pf:>6}{s['dd']:>11.0f}")
            print(row); lines.append(row)
        print(); lines.append("")
    open(os.path.join(HERE, "cross_asset_results.txt"), "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
