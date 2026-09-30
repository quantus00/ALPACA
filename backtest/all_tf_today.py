"""All-timeframe test on today's session (entries 09:30 ET+), per instrument-bot."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mes_backtest import load_bars, resample  # type: ignore
from multi_asset import Trend  # type: ignore
from point_move import PMove  # type: ignore
from today_backtest import run  # type: ignore

DD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TFS = [1, 5, 15, 30, 60]
ASSETS = [("MES", "m1_MESZ6.json", 5.0), ("SPY", "m1_SPY.json", 1.0),
          ("GOLD", "m1_GCZ6.json", 10.0), ("BTC", "m1_BTCUSD.json", 1.0)]


def main():
    m1_mes = load_bars(os.path.join(DD, ASSETS[0][1]))
    target = max(b.et.date() for b in m1_mes).isoformat()
    print(f"# All-timeframe test — session {target}, entries 09:30 ET+ (net R / #trades)\n")
    hdr = f"{'asset':<6}{'method':<10}" + "".join(f"{('M'+str(t)):>12}" for t in TFS)
    print(hdr); print("-" * len(hdr))
    results = {}
    for name, fn, pv in ASSETS:
        m1 = load_bars(os.path.join(DD, fn))
        for method in ("structure", "momentum"):
            cells = []
            for tf in TFS:
                bars = resample(m1, tf)
                dmin = tf * 3
                direction = Trend(m1, dmin) if method == "structure" else PMove(m1, dmin, "momentum", N=3)
                trades = run(bars, direction, pv, target)
                R = sum(t["R"] for t in trades)
                results[(name, method, tf)] = (R, len(trades))
                cells.append(f"{R:+.1f}/{len(trades)}")
            print(f"{name:<6}{method:<10}" + "".join(f"{c:>12}" for c in cells))
        print()
    import json
    json.dump({f"{k[0]}|{k[1]}|{k[2]}": v for k, v in results.items()},
              open(os.path.join(DD, "..", "alltf_today.json"), "w"))


if __name__ == "__main__":
    main()
