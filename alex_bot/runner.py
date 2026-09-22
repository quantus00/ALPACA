"""Alex bot runner: fetch candles, evaluate the strategy, size and route trades.

Defaults to MES futures on Webull (live-capable). Trades any asset the cockpit's
Webull/Coinbase connections support. Live orders route through the cockpit's
plumbing and only transmit when the master switch is armed (--live); otherwise
everything runs paper/dry-run.

  python -m alex_bot.runner analyze                       # default MES/1h/15m
  python -m alex_bot.runner analyze --symbol BTC-USD --broker coinbase --instrument crypto
  python -m alex_bot.runner trade  --symbol MES --broker webull --instrument futures        # paper
  python -m alex_bot.runner trade  --symbol MES --broker webull --instrument futures --live # LIVE
  python -m alex_bot.runner selftest
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time

from .config import BotConfig
from .execution import get_executor
from .strategy import Params, Signal, evaluate, find_zones, trend_of

log = logging.getLogger("alex")
STATE_FILE = os.getenv("ALEX_STATE_FILE", "alex_state.json")


def position_size(equity: float, risk_pct: float, entry: float, stop: float,
                  fallback: float) -> float:
    per_unit = abs(entry - stop)
    if per_unit <= 0 or equity <= 0:
        return fallback
    return (equity * (risk_pct / 100.0)) / per_unit


def _candles(cfg: BotConfig, tf: str, limit: int = 300):
    from .data import get_candles
    return get_candles(cfg.symbol, tf, limit)


def evaluate_cfg(cfg: BotConfig, params: Params) -> Signal:
    structure = _candles(cfg, cfg.structure_tf)
    entry = _candles(cfg, cfg.entry_tf)
    return evaluate(structure, entry, params)


def analyze(cfg: BotConfig, params: Params, sig: Signal | None = None) -> Signal:
    sig = sig or evaluate_cfg(cfg, params)
    print(f"=== {cfg.describe()} ===")
    print(f"Trend:  {sig.trend.label()}")
    print(f"Signal: {sig.action.upper()}  — {sig.reason}")
    if sig.action in ("buy", "sell"):
        qty = position_size(cfg.equity, cfg.risk_pct, sig.entry, sig.stop,
                            cfg.contract_size)
        rr = abs(sig.take_profit - sig.entry) / max(abs(sig.entry - sig.stop), 1e-9)
        print(f"  pattern     : {sig.pattern}")
        print(f"  entry {sig.entry:g}  stop {sig.stop:g}  tp {sig.take_profit:g}  (R:R {rr:.1f})")
        print(f"  size        : {qty:.6g}  (risk {cfg.risk_pct}% of {cfg.equity:g})")
    return sig


# -- position state ----------------------------------------------------------
def _load():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _save(pos):
    with open(STATE_FILE, "w") as f:
        json.dump(pos, f, indent=2)


def _clear():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def trade_tick(cfg: BotConfig, params: Params) -> None:
    """One decision cycle: manage an open position, else look for an entry."""
    sig = evaluate_cfg(cfg, params)
    executor = get_executor(cfg)
    pos = _load()

    if pos:  # manage exits (stop / take-profit / trend flip against us)
        price = sig.price
        side = pos["side"]
        hit_stop = price <= pos["stop"] if side == "buy" else price >= pos["stop"]
        hit_tp = price >= pos["tp"] if side == "buy" else price <= pos["tp"]
        flipped = (side == "buy" and sig.trend.name == "DOWN") or \
                  (side == "sell" and sig.trend.name == "UP")
        if hit_stop or hit_tp or flipped:
            reason = "stop" if hit_stop else "take-profit" if hit_tp else "trend flip"
            f = executor.flatten(cfg, side, pos["qty"], price)
            log.info("EXIT (%s) %s -> ok=%s %s", reason, cfg.symbol, f.ok,
                     f.error or f.order_id)
            if f.ok:
                _clear()
        else:
            log.info("holding %s %s: price %g stop %g tp %g", side, cfg.symbol,
                     price, pos["stop"], pos["tp"])
        return

    if sig.action in ("buy", "sell"):
        qty = position_size(cfg.equity, cfg.risk_pct, sig.entry, sig.stop,
                            cfg.contract_size)
        f = executor.market(cfg, sig.action, qty, sig.entry)
        log.info("ENTER %s %s %g -> ok=%s %s", sig.action.upper(), cfg.symbol,
                 qty, f.ok, f.error or f.order_id)
        if f.ok:
            _save({"side": sig.action, "qty": qty, "entry": sig.entry,
                   "stop": sig.stop, "tp": sig.take_profit, "symbol": cfg.symbol})
    else:
        log.info("flat — %s", sig.reason)


def run_loop(cfg: BotConfig, params: Params, mode: str) -> None:
    armed = "LIVE-ARMED" if not cfg.dry_run() else "paper"
    log.info("%s loop: %s every %ss [%s]", mode, cfg.describe(),
             cfg.poll_seconds, armed)
    while True:
        try:
            if mode == "poll":
                analyze(cfg, params)
            else:
                trade_tick(cfg, params)
        except Exception:  # noqa: BLE001
            log.exception("tick failed")
        time.sleep(cfg.poll_seconds)


def selftest() -> int:
    from .strategy import Candle
    seq = [90, 95, 88, 100, 92, 112]
    for _ in range(3):
        seq += [115, 108, 100, 107, 114]
    seq += [116, 118]
    structure = [Candle(i, p, p + 1, p - 1, p) for i, p in enumerate(seq)]
    entry = [Candle(0, 104, 105, 100.5, 101), Candle(1, 101, 101.5, 98.8, 101.2)]
    sig = evaluate(structure, entry, Params(pivot_lookback=1, aoi_tol_frac=0.03))
    print("selftest:", sig.action, "-", sig.reason)
    ok = sig.action == "buy"
    print("selftest:", "OK" if ok else "FAILED")
    return 0 if ok else 1


def _cfg_from_args(args) -> BotConfig:
    cfg = BotConfig()
    if args.broker is not None:
        cfg.broker = args.broker
    if args.instrument is not None:
        cfg.instrument = args.instrument
    if args.symbol is not None:
        cfg.symbol = args.symbol
    if args.account_id is not None:
        cfg.account_id = args.account_id
    if args.entry_tf is not None:
        cfg.entry_tf = args.entry_tf
    if args.structure_tf is not None:
        cfg.structure_tf = args.structure_tf
    if args.route_mode is not None:
        cfg.mode = args.route_mode
    if args.equity is not None:
        cfg.equity = args.equity
    if args.risk is not None:
        cfg.risk_pct = args.risk
    if args.size is not None:
        cfg.contract_size = args.size
    if args.poll is not None:
        cfg.poll_seconds = args.poll
    if args.live:
        cfg.live_armed = True
    return cfg


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Alex market-structure bot")
    p.add_argument("command", choices=["analyze", "poll", "trade", "selftest"])
    p.add_argument("--broker", choices=["webull", "coinbase"])
    p.add_argument("--instrument", choices=["futures", "crypto", "stock", "option"])
    p.add_argument("--symbol")
    p.add_argument("--account-id", dest="account_id")
    p.add_argument("--size", type=float)
    p.add_argument("--entry-tf", dest="entry_tf")
    p.add_argument("--structure-tf", dest="structure_tf")
    p.add_argument("--route", dest="route_mode", choices=["paper", "live"],
                   help="cockpit account to route to (default live)")
    p.add_argument("--live", action="store_true",
                   help="ARM live: actually transmit real orders (master switch)")
    p.add_argument("--equity", type=float)
    p.add_argument("--risk", type=float)
    p.add_argument("--poll", type=int)
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
    cfg = _cfg_from_args(args)

    if args.command == "analyze":
        analyze(cfg, params)
        return 0
    run_loop(cfg, params, args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
