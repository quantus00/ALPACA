"""
Single-session ('today') intraday backtest, entries from 09:30 ET.
Runs the tuned hybrid_core (SL 1.5 / TP 3.0 ATR) on 5-minute bars, taking entries
only on the target date at/after 09:30 ET (prior days are context only).
Runs both direction methods: structure trend and point-movement momentum(N3).
"""
import os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mes_backtest import load_bars, resample, trend_states  # type: ignore
from multi_asset import atr, _pullback_signals, Trend  # type: ignore
from point_move import PMove  # type: ignore

DD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ET = ZoneInfo("America/New_York")
DIR_MIN = 60          # direction timeframe (60m)
CORE_TP, CORE_SL = 3.0, 1.5

ASSETS = [
    ("MES",  "today_MESZ6_M5.json", 5.0),
    ("SPY",  "today_SPY_M5.json",   1.0),
    ("GOLD", "today_GCZ6_M5.json", 10.0),
    ("BTC",  "today_BTCUSD_M5.json", 1.0),
]


def rth_open(dt_et):
    m = dt_et.hour * 60 + dt_et.minute
    return 9 * 60 + 30 <= m < 16 * 60


def run(bars, direction, pv, target_date):
    at = atr(bars); sig = _pullback_signals(bars, direction)
    T = []; pos = None
    for i, b in enumerate(bars):
        st = direction.at(b.start); et = b.et
        if pos is not None:
            d = pos['dir']
            hs = (b.l <= pos['sl']) if d > 0 else (b.h >= pos['sl'])
            ht = (b.h >= pos['tp']) if d > 0 else (b.l <= pos['tp'])
            if hs:
                T.append((pos, pos['sl'], et, 'stop')); pos = None
            elif ht:
                T.append((pos, pos['tp'], et, 'target')); pos = None
        # entries only on the target date, from 09:30 ET
        if pos is None and sig[i] != 0 and at[i] > 0 and et.date().isoformat() == target_date and rth_open(et):
            d = sig[i]; ref = b.c; a = at[i]
            pos = dict(dir=d, ref=ref, atr=a, t=et,
                       tp=ref + CORE_TP * a if d > 0 else ref - CORE_TP * a,
                       sl=ref - CORE_SL * a if d > 0 else ref + CORE_SL * a,
                       risk=CORE_SL * a * pv)
    if pos is not None:
        T.append((pos, bars[-1].c, bars[-1].et, 'eod'))
    out = []
    for p, px, xt, r in T:
        pnl = (px - p['ref']) * p['dir'] * pv
        out.append(dict(side='L' if p['dir'] > 0 else 'S', t=p['t'], xt=xt, entry=p['ref'],
                        exit=px, pnl=pnl, R=pnl / p['risk'] if p['risk'] else 0, r=r))
    return out


def main():
    # target date = latest date present across the MES file
    b0 = load_bars(os.path.join(DD, ASSETS[0][1]))
    target = max(b.et.date() for b in b0).isoformat()
    print(f"=== Single-session backtest — {target} (entries 09:30 ET+) — hybrid_core SL1.5/TP3 ATR ===\n")
    for name, fn, pv in ASSETS:
        bars = load_bars(os.path.join(DD, fn))
        methods = {"structure": Trend(bars, DIR_MIN),
                   "momentumN3": PMove(bars, DIR_MIN, "momentum", N=3)}
        for mname, direction in methods.items():
            trades = run(bars, direction, pv, target)
            net = sum(t['pnl'] for t in trades); R = sum(t['R'] for t in trades)
            w = sum(1 for t in trades if t['pnl'] > 0)
            print(f"{name:<5} {mname:<11} trades={len(trades)} win={w}  net=${net:,.0f}  R={R:+.2f}")
            for t in trades:
                print(f"        {t['side']} {t['t']:%H:%M}->{t['xt']:%H:%M} {t['r']:<6} "
                      f"entry={t['entry']:.2f} exit={t['exit']:.2f} pnl=${t['pnl']:,.0f} ({t['R']:+.2f}R)")
        print()


if __name__ == "__main__":
    main()
