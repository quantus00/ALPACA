"""CLI entry point.

Modes:
  poll     -- run the Python strategy on a timer; notify UPTREND/DOWNTREND and
              place orders when all timeframes align (self-contained, no TV).
  webhook  -- run the Flask server and let TradingView alerts drive orders.
  once     -- evaluate a single tick and print the trend + any signal.
  dual     -- open the same side on several brokers at once (per-leg
              instrument/size), then watch single + combined P/L and flatten
              them all together when a chosen % trips.
  flatten  -- close every open leg saved from a previous `dual` run.
  status   -- print open legs with their single + combined P/L.
  balances -- print account balances for the given brokers.

Examples:
  python -m bot.main poll --broker coinbase --instrument btc_usd_spot --size 0.01
  python -m bot.main dual --leg coinbase:btc_usd_spot:0.01 \\
                          --leg webull:spy_options:1 --tp 2 --sl 1 --live
  python -m bot.main flatten
  python -m bot.main balances --leg coinbase --leg webull
"""
from __future__ import annotations

import argparse
import logging
import time

from . import dual as dual_mod
from .brokers import get_broker
from .config import Broker, Config, Instrument
from .notifier import Notifier
from .portfolio import load_state
from .strategy import StrategyRunner


def _build_config(args, validate: bool = True) -> Config:
    cfg = Config()
    if args.broker:
        cfg.broker = Broker(args.broker)
    if args.instrument:
        cfg.instrument = Instrument(args.instrument)
    if args.size is not None:
        cfg.contract_size = args.size
    if args.dry_run is not None:
        cfg.dry_run = args.dry_run
    # Dual-broker P/L exit overrides (None => keep env/default).
    for name in ("tp", "sl", "leg_tp", "leg_sl"):
        val = getattr(args, name, None)
        if val is not None:
            setattr(cfg, f"{name}_pct", val)
    if validate:
        cfg.validate()
    return cfg


def _run_poll(cfg: Config) -> None:
    notifier = Notifier()
    runner = StrategyRunner(cfg, notifier)
    broker = get_broker(cfg)
    log = logging.getLogger("poll")
    log.info("Polling every %ss  (%s)", cfg.poll_seconds, cfg.describe())
    while True:
        try:
            sig = runner.evaluate()
            log.info("4H=%s align=%s -> %s", sig.trend.label(), sig.aligned, sig.action)
            if sig.action in ("buy", "sell"):
                res = broker.place_order(sig.action, cfg.contract_size)
                if res.ok:
                    notifier.notify_entry(sig.action, res.symbol, sig.price,
                                          cfg.contract_size)
                else:
                    log.error("Order failed: %s", res.error)
        except Exception:  # noqa: BLE001
            log.exception("poll tick failed")
        time.sleep(cfg.poll_seconds)


def _run_once(cfg: Config) -> None:
    notifier = Notifier()
    runner = StrategyRunner(cfg, notifier)
    sig = runner.evaluate()
    print(f"4H TREND: {sig.trend.label()}")
    for tf, state in sig.aligned.items():
        print(f"  {tf:>4} : {state}")
    print(f"SIGNAL: {sig.action.upper()}")


def _leg_specs(args) -> list:
    if args.leg:
        return dual_mod.parse_legs(args.leg)
    env_legs = Config().legs                # fall back to BOT_LEGS
    if env_legs:
        return dual_mod.parse_legs(env_legs)
    raise SystemExit("no legs given: pass --leg broker:instrument:size "
                     "(repeatable) or set BOT_LEGS")


def _run_dual(cfg: Config, args) -> None:
    log = logging.getLogger("dual")
    specs = _leg_specs(args)
    log.info("Opening %d leg(s) side=%s dry_run=%s", len(specs), args.side, cfg.dry_run)
    legs = dual_mod.open_all(cfg, specs, args.side)
    if not legs:
        log.error("No legs opened; nothing to manage.")
        return
    marks = dual_mod.marks_for(cfg, legs)
    log.info("Opened:\n%s", dual_mod.report(legs, marks))
    dual_mod.monitor(cfg, legs, dual_mod.rules_from(cfg))


def _run_flatten(cfg: Config) -> None:
    log = logging.getLogger("flatten")
    legs = load_state(cfg.state_file)
    if not legs:
        log.info("No open legs recorded in %s.", cfg.state_file)
        return
    dual_mod.flatten_all(cfg, legs)
    log.info("Flattened %d leg(s).", len(legs))


def _run_status(cfg: Config) -> None:
    legs = load_state(cfg.state_file)
    if not legs:
        print("No open legs.")
        return
    marks = dual_mod.marks_for(cfg, legs)
    print("OPEN LEGS:")
    print(dual_mod.report(legs, marks))


def _run_balances(cfg: Config, args) -> None:
    # Accept bare broker names or full leg specs; only the broker matters here.
    tokens = args.leg or [s for s in Config().legs.split(",") if s.strip()]
    if not tokens:
        raise SystemExit("pass --leg <broker> (e.g. --leg coinbase --leg webull)")
    brokers = []
    for tok in tokens:
        brokers.append(Broker(tok.split(":")[0].strip()))
    for name, bal in dual_mod.balances(cfg, brokers).items():
        print(f"[{name}]")
        if not bal:
            print("  (none / not implemented)")
        for key, val in bal.items():
            print(f"  {key}: {val}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Multi-timeframe trend bot")
    p.add_argument("mode",
                   choices=["poll", "webhook", "once", "dual", "flatten",
                            "status", "balances"])
    p.add_argument("--broker", choices=[b.value for b in Broker])
    p.add_argument("--instrument", choices=[i.value for i in Instrument])
    p.add_argument("--size", type=float, help="contract-size toggle")
    p.add_argument("--live", dest="dry_run", action="store_false", default=None,
                   help="place REAL orders (default is dry-run)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    # Dual-broker options.
    p.add_argument("--leg", action="append",
                   help="dual/balances: a leg as broker:instrument:size "
                        "(repeatable). For `balances`, a bare broker name works.")
    p.add_argument("--side", choices=["buy", "sell"], default="buy",
                   help="dual: open direction for every leg (default buy)")
    p.add_argument("--tp", type=float, help="combined take-profit %%")
    p.add_argument("--sl", type=float, help="combined stop-loss %% (magnitude)")
    p.add_argument("--leg-tp", dest="leg_tp", type=float, help="per-leg take-profit %%")
    p.add_argument("--leg-sl", dest="leg_sl", type=float,
                   help="per-leg stop-loss %% (magnitude)")
    args = p.parse_args(argv)

    # Single-broker modes validate the base broker/instrument pairing; the
    # multi-leg modes validate each leg on its own.
    needs_base_validate = args.mode in ("poll", "webhook", "once")
    cfg = _build_config(args, validate=needs_base_validate)
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.mode == "poll":
        _run_poll(cfg)
    elif args.mode == "webhook":
        from .webhook import run as run_webhook
        run_webhook(cfg)
    elif args.mode == "dual":
        _run_dual(cfg, args)
    elif args.mode == "flatten":
        _run_flatten(cfg)
    elif args.mode == "status":
        _run_status(cfg)
    elif args.mode == "balances":
        _run_balances(cfg, args)
    else:
        _run_once(cfg)


if __name__ == "__main__":
    main()
