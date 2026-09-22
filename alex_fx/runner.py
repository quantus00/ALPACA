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
from .backtest import backtest
from .broker import get_broker
from .instrument import lots_for_risk

log = logging.getLogger("alexfx")
STATE_FILE = os.getenv("ALEXFX_STATE_FILE", "alexfx_state.json")


def run_backtest(args, params: Params) -> int:
    candles = (fxdata.synthetic_uptrend_with_pullback(args.pair) if args.demo
               else fxdata.load_csv(args.csv))
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


def trade_tick(broker, args, params: Params) -> None:
    structure = broker.price_history(args.pair, args.structure_tf, 300)
    entry = broker.price_history(args.pair, args.entry_tf, 300)
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
            f = broker.market(args.pair, close, pos["lots"], price)
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
        f = broker.market(args.pair, sig.action, lots, sig.entry)
        log.info("ENTER %s %s %g lots ok=%s %s", sig.action.upper(), args.pair,
                 lots, f.ok, f.error or f.order_id)
        if f.ok:
            _save({"side": sig.action, "lots": lots, "entry": sig.entry,
                   "stop": sig.stop, "tp": sig.take_profit})
    else:
        log.info("flat — %s", sig.reason)


def run_forward(args, params: Params, live: bool) -> int:
    mode = "live" if live else "demo"
    if live and not args.live:
        print("refusing live without --live (master arm). Add --live to trade real money.")
        return 2
    broker = get_broker(mode)
    log.info("%s trading %s (entry=%s structure=%s) every %ss",
             mode.upper(), args.pair, args.entry_tf, args.structure_tf, args.poll)
    while True:
        try:
            trade_tick(broker, args, params)
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
    p.add_argument("command", choices=["backtest", "paper", "live", "selftest"])
    p.add_argument("--pair", default="EUR/USD")
    p.add_argument("--csv", help="historical OHLC CSV for backtest")
    p.add_argument("--demo", action="store_true", help="backtest on synthetic data")
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
    if args.command == "selftest":
        return selftest()
    params = Params(pivot_lookback=args.pivot_lookback, aoi_tol_frac=args.aoi_tol,
                    min_touches=args.min_touches, wick_ratio=args.wick_ratio,
                    rr=args.rr)
    if args.command == "backtest":
        if not args.demo and not args.csv:
            print("backtest needs --csv <file> or --demo")
            return 2
        return run_backtest(args, params)
    return run_forward(args, params, live=(args.command == "live"))


if __name__ == "__main__":
    raise SystemExit(main())
