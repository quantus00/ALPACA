"""Forex Alex bot — backtest, paper (FOREX.com demo), and live (FOREX.com).

  # Backtest on a CSV of historical bars:
  python -m alex_fx.runner backtest --csv eurusd_15m.csv --pair EUR/USD --spread 1.0

  # Backtest on the built-in synthetic series (no data needed):
  python -m alex_fx.runner backtest --demo --pair EUR/USD

  # Paper trade on your FOREX.com DEMO account (forward test):
  FOREXCOM_USERNAME=.. FOREXCOM_PASSWORD=.. FOREXCOM_APPKEY=.. \
    python -m alex_fx.runner paper --pair EUR/USD --entry-tf 15m --structure-tf 1h

  # Live (real money) — must pass --live to arm:
  python -m alex_fx.runner live --pair EUR/USD --live

  python -m alex_fx.runner selftest
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time

from alex_bot.strategy import Params, Trend, evaluate
from . import data as fxdata
from . import scanner as fxscan
from .backtest import backtest
from .broker import get_broker
from .instrument import lots_for_risk

log = logging.getLogger("alexfx")
STATE_FILE = os.getenv("ALEXFX_STATE_FILE", "alexfx_state.json")


def _fetch_candles(args, tf: str):
    """Keyless candle source for backtest/paper: demo | csv | yahoo | stooq."""
    if args.source == "demo":
        return fxdata.synthetic_uptrend_with_pullback(args.pair)
    if args.source == "csv":
        return fxdata.load_csv(args.csv)
    return fxdata.get_candles(args.pair, tf, args.source)   # yahoo | stooq (keyless)


def run_scan(args, params: Params) -> int:
    """Scan every pair for a setup — the on-open screener. Keyless."""
    pairs = ([s.strip() for s in args.pairs.split(",")] if args.pairs
             else fxscan.DEFAULT_UNIVERSE)
    fetch = lambda pair, tf: fxdata.get_candles(pair, tf, args.source)  # noqa: E731
    log.info("Scanning %d pairs (entry=%s structure=%s, data=%s)...",
             len(pairs), args.entry_tf, args.structure_tf, args.source)
    rows = fxscan.scan(fetch, params, args.entry_tf, args.structure_tf, pairs,
                       workers=args.workers)
    print(fxscan.format_table(rows, show=args.show))
    return 0


def run_backtest(args, params: Params) -> int:
    candles = _fetch_candles(args, args.entry_tf)
    if len(candles) < args.warmup + 5:
        print(f"not enough candles ({len(candles)}) — need > {args.warmup + 5}")
        return 1
    res = backtest(candles, args.pair, params, warmup=args.warmup,
                   spread_pips=args.spread, exit_on_flip=not args.no_flip_exit)
    print(res.summary())
    if args.trades:
        for t in res.closed:
            print(f"  {t.side:<4} in {t.entry:.5f} out {t.exit:.5f} "
                  f"[{t.reason}] {t.r_multiple:+.2f}R")
    return 0


# -- forward trading (FOREX.com demo=paper / live) ---------------------------
def _load():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _save(p):
    with open(STATE_FILE, "w") as f:
        json.dump(p, f, indent=2)


def _clear():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def trade_tick(fetch, exec_broker, args, params: Params) -> None:
    structure = fetch(args.structure_tf)
    entry = fetch(args.entry_tf)
    sig = evaluate(structure, entry, params)
    pos = _load()

    if pos:
        price = sig.price
        side = pos["side"]
        hit_stop = price <= pos["stop"] if side == "buy" else price >= pos["stop"]
        hit_tp = price >= pos["tp"] if side == "buy" else price <= pos["tp"]
        flipped = (side == "buy" and sig.trend == Trend.DOWN) or \
                  (side == "sell" and sig.trend == Trend.UP)
        if hit_stop or hit_tp or flipped:
            reason = "stop" if hit_stop else "tp" if hit_tp else "flip"
            close = "sell" if side == "buy" else "buy"
            f = exec_broker.market(args.pair, close, pos["lots"], price)
            log.info("EXIT(%s) %s ok=%s %s", reason, args.pair, f.ok,
                     f.error or f.order_id)
            if f.ok:
                _clear()
        else:
            log.info("holding %s %s price %g stop %g tp %g", side, args.pair,
                     price, pos["stop"], pos["tp"])
        return

    if sig.action in ("buy", "sell"):
        lots = lots_for_risk(args.pair, args.equity, args.risk, sig.entry, sig.stop)
        lots = max(round(lots, 2), 0.01)
        f = exec_broker.market(args.pair, sig.action, lots, sig.entry)
        log.info("ENTER %s %s %g lots ok=%s %s", sig.action.upper(), args.pair,
                 lots, f.ok, f.error or f.order_id)
        if f.ok:
            _save({"side": sig.action, "lots": lots, "entry": sig.entry,
                   "stop": sig.stop, "tp": sig.take_profit})
    else:
        log.info("flat — %s", sig.reason)


def run_forward(args, params: Params, live: bool) -> int:
    # KEYLESS by default: local simulator with free (Yahoo/Stooq) data.
    if args.broker == "sim":
        exec_broker = get_broker("sim")           # SimBroker, no keys, no orders sent
        fetch = lambda tf: fxdata.get_candles(args.pair, tf, args.source)  # noqa: E731
        label = "PAPER-SIM (keyless)"
    else:  # forexcom demo/live (needs FOREX.com creds)
        mode = "live" if live else "demo"
        if live and not args.live:
            print("refusing live without --live (master arm). Add --live to trade real money.")
            return 2
        exec_broker = get_broker(mode)
        fetch = lambda tf: exec_broker.price_history(args.pair, tf, 300)   # noqa: E731
        label = f"FOREX.com {mode.upper()}"

    if live and args.broker == "sim":
        print("note: 'live' with --broker sim just simulates locally (no real orders).")

    # Opening scan: screen the whole universe once so you see the board on start.
    if not args.no_scan and args.broker == "sim":
        try:
            fetch = lambda pair, tf: fxdata.get_candles(pair, tf, args.source)  # noqa: E731
            rows = fxscan.scan(fetch, params, args.entry_tf, args.structure_tf,
                               workers=args.workers)
            print("=== opening scan ===")
            print(fxscan.format_table(rows, show="actionable"))
        except Exception:  # noqa: BLE001
            log.warning("opening scan skipped (data unavailable)")

    log.info("%s trading %s (entry=%s structure=%s, data=%s) every %ss",
             label, args.pair, args.entry_tf, args.structure_tf, args.source, args.poll)
    while True:
        try:
            trade_tick(fetch, exec_broker, args, params)
        except Exception:  # noqa: BLE001
            log.exception("tick failed")
        time.sleep(args.poll)


def selftest() -> int:
    candles = fxdata.synthetic_uptrend_with_pullback("EUR/USD")
    res = backtest(candles, "EUR/USD", Params(pivot_lookback=1, aoi_tol_frac=0.03),
                   warmup=6, spread_pips=0.5)
    print(res.summary())
    ok = len(res.trades) >= 1
    print("selftest:", "OK" if ok else "FAILED (no trades)")
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Alex FOREX bot (FOREX.com)")
    p.add_argument("command", choices=["scan", "backtest", "paper", "live", "selftest"])
    p.add_argument("--pair", default="EUR/USD")
    p.add_argument("--pairs", help="scan: comma-separated pairs (default: full universe)")
    p.add_argument("--show", choices=["setups", "actionable", "all"],
                   default="actionable", help="scan: which rows to print")
    p.add_argument("--workers", type=int, default=8, help="scan concurrency")
    p.add_argument("--no-scan", action="store_true", help="skip the opening scan")
    p.add_argument("--source", choices=["yahoo", "stooq", "csv", "demo"],
                   default="yahoo",
                   help="KEYLESS data source (yahoo intraday / stooq daily), "
                        "or csv / demo. Default yahoo — no API key.")
    p.add_argument("--csv", help="historical OHLC CSV (implies --source csv)")
    p.add_argument("--demo", action="store_true", help="synthetic data (implies --source demo)")
    p.add_argument("--broker", choices=["sim", "forexcom"], default="sim",
                   help="paper/live execution: sim = keyless local simulator "
                        "(default), forexcom = FOREX.com demo/live (needs creds)")
    p.add_argument("--trades", action="store_true", help="print each backtest trade")
    p.add_argument("--spread", type=float, default=1.0, help="modeled spread (pips)")
    p.add_argument("--warmup", type=int, default=60)
    p.add_argument("--no-flip-exit", dest="no_flip_exit", action="store_true")
    p.add_argument("--entry-tf", dest="entry_tf", default="15m")
    p.add_argument("--structure-tf", dest="structure_tf", default="1h")
    p.add_argument("--equity", type=float, default=10000.0)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--poll", type=int, default=300)
    p.add_argument("--live", action="store_true", help="ARM real-money live orders")
    # strategy knobs
    p.add_argument("--pivot-lookback", dest="pivot_lookback", type=int, default=3)
    p.add_argument("--aoi-tol", dest="aoi_tol", type=float, default=0.0015)
    p.add_argument("--min-touches", dest="min_touches", type=int, default=3)
    p.add_argument("--wick-ratio", dest="wick_ratio", type=float, default=1.5)
    p.add_argument("--rr", type=float, default=2.0)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Convenience flags override --source.
    if args.csv:
        args.source = "csv"
    elif args.demo:
        args.source = "demo"
    if args.command == "selftest":
        return selftest()
    params = Params(pivot_lookback=args.pivot_lookback, aoi_tol_frac=args.aoi_tol,
                    min_touches=args.min_touches, wick_ratio=args.wick_ratio,
                    rr=args.rr)
    if args.command == "scan":
        return run_scan(args, params)
    if args.command == "backtest":
        if args.source == "csv" and not args.csv:
            print("--source csv needs --csv <file>")
            return 2
        return run_backtest(args, params)
    return run_forward(args, params, live=(args.command == "live"))


if __name__ == "__main__":
    raise SystemExit(main())
