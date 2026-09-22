"""Alex bot runner: fetch candles, evaluate the strategy, size the trade.

Standalone and read-only for now — it reports what the strategy sees and would
do. LIVE order routing is deliberately NOT wired here yet: it will call the
cockpit's existing Webull/Coinbase connections once we lock the strategy. Paper
mode simulates fills so you can watch it end-to-end with zero risk.

Usage:
  python -m alex_bot.runner analyze --symbol BTC-USD --entry-tf 15m --structure-tf 1h
  python -m alex_bot.runner poll    --symbol BTC-USD --poll 300          # paper
  python -m alex_bot.runner selftest
"""
from __future__ import annotations

import argparse
import logging
import time

from .strategy import Params, Signal, evaluate, find_zones, trend_of

log = logging.getLogger("alex")


def position_size(equity: float, risk_pct: float, entry: float,
                  stop: float) -> float:
    """Units to trade so that (entry-stop) distance risks risk_pct of equity."""
    per_unit = abs(entry - stop)
    if per_unit <= 0 or equity <= 0:
        return 0.0
    dollar_risk = equity * (risk_pct / 100.0)
    return dollar_risk / per_unit


def analyze_symbol(symbol: str, entry_tf: str, structure_tf: str,
                   params: Params, equity: float, risk_pct: float) -> Signal:
    from .data import get_candles
    structure = get_candles(symbol, structure_tf, limit=300)
    entry = get_candles(symbol, entry_tf, limit=300)
    sig = evaluate(structure, entry, params)

    print(f"=== {symbol}  structure={structure_tf}  entry={entry_tf} ===")
    print(f"Trend:  {sig.trend.label()}")
    zones = find_zones(structure, params)
    if zones:
        print("Areas of interest (>=%d touches):" % params.min_touches)
        for z in sorted(zones, key=lambda z: z.mid):
            print(f"  {z.kind:<10} {z.low:g}-{z.high:g}  ({z.touches} touches)")
    else:
        print("Areas of interest: none yet")
    print(f"Signal: {sig.action.upper()}  — {sig.reason}")
    if sig.action in ("buy", "sell"):
        qty = position_size(equity, risk_pct, sig.entry, sig.stop)
        rr = abs(sig.take_profit - sig.entry) / max(abs(sig.entry - sig.stop), 1e-9)
        print(f"  pattern     : {sig.pattern}")
        print(f"  entry       : {sig.entry:g}")
        print(f"  stop        : {sig.stop:g}")
        print(f"  take-profit : {sig.take_profit:g}   (R:R {rr:.1f})")
        print(f"  size        : {qty:.6g} units  (risk {risk_pct}% of {equity:g})")
    return sig


def _build_params(args) -> Params:
    return Params(
        pivot_lookback=args.pivot_lookback,
        aoi_lookback=args.aoi_lookback,
        aoi_tol_frac=args.aoi_tol,
        min_touches=args.min_touches,
        wick_ratio=args.wick_ratio,
        rr=args.rr,
    )


def run_poll(args, params: Params) -> None:
    log.info("Paper-watching %s (entry=%s structure=%s) every %ss. No live "
             "orders are sent.", args.symbol, args.entry_tf, args.structure_tf,
             args.poll)
    last = None
    while True:
        try:
            sig = analyze_symbol(args.symbol, args.entry_tf, args.structure_tf,
                                 params, args.equity, args.risk)
            if sig.action in ("buy", "sell") and sig.action != last:
                log.info("[PAPER] would %s %s @ %g  stop %g  tp %g",
                         sig.action.upper(), args.symbol, sig.entry, sig.stop,
                         sig.take_profit)
                last = sig.action
            elif sig.action == "none":
                last = None
        except Exception:  # noqa: BLE001
            log.exception("poll tick failed")
        time.sleep(args.poll)


def selftest() -> int:
    from .strategy import Candle
    seq = [90, 95, 88, 100, 92, 112]
    for _ in range(3):
        seq += [115, 108, 100, 107, 114]
    seq += [116, 118]
    structure = [Candle(i, p, p + 1, p - 1, p) for i, p in enumerate(seq)]
    entry = [Candle(0, 104, 105, 100.5, 101), Candle(1, 101, 101.5, 98.8, 101.2)]
    p = Params(pivot_lookback=1, aoi_tol_frac=0.03)
    sig = evaluate(structure, entry, p)
    print("selftest trend:", trend_of(structure, 1).label())
    print("selftest signal:", sig.action, "-", sig.reason)
    ok = sig.action == "buy"
    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Alex market-structure bot")
    p.add_argument("mode", choices=["analyze", "poll", "selftest"])
    p.add_argument("--symbol", default="BTC-USD")
    p.add_argument("--entry-tf", dest="entry_tf", default="15m")
    p.add_argument("--structure-tf", dest="structure_tf", default="1h")
    p.add_argument("--equity", type=float, default=10000.0)
    p.add_argument("--risk", type=float, default=1.0, help="%% of equity per trade")
    p.add_argument("--poll", type=int, default=300)
    # strategy knobs
    p.add_argument("--pivot-lookback", dest="pivot_lookback", type=int, default=3)
    p.add_argument("--aoi-lookback", dest="aoi_lookback", type=int, default=150)
    p.add_argument("--aoi-tol", dest="aoi_tol", type=float, default=0.0015)
    p.add_argument("--min-touches", dest="min_touches", type=int, default=3)
    p.add_argument("--wick-ratio", dest="wick_ratio", type=float, default=1.5)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.mode == "selftest":
        return selftest()
    params = _build_params(args)
    if args.mode == "analyze":
        analyze_symbol(args.symbol, args.entry_tf, args.structure_tf, params,
                       args.equity, args.risk)
        return 0
    run_poll(args, params)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
