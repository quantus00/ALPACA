"""CLI entry point.

Modes:
  poll     -- run the Python strategy on a timer; notify UPTREND/DOWNTREND and
              place orders when all timeframes align (self-contained, no TV).
  webhook  -- run the Flask server and let TradingView alerts drive orders.
  once     -- evaluate a single tick and print the trend + any signal.

Examples:
  python -m bot.main poll --broker coinbase --instrument btc_usd_spot --size 0.01
  python -m bot.main webhook --broker tradovate --instrument mes_futures --size 1
  python -m bot.main once --broker alpaca --instrument spy_options --size 1
"""
from __future__ import annotations

import argparse
import logging
import time

from .brokers import get_broker
from .config import Broker, Config, Instrument
from .notifier import Notifier
from .strategy import StrategyRunner


def _build_config(args) -> Config:
    cfg = Config()
    if args.broker:
        cfg.broker = Broker(args.broker)
    if args.instrument:
        cfg.instrument = Instrument(args.instrument)
    if args.size is not None:
        cfg.contract_size = args.size
    if args.dry_run is not None:
        cfg.dry_run = args.dry_run
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


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Multi-timeframe trend bot")
    p.add_argument("mode", choices=["poll", "webhook", "once"])
    p.add_argument("--broker", choices=[b.value for b in Broker])
    p.add_argument("--instrument", choices=[i.value for i in Instrument])
    p.add_argument("--size", type=float, help="contract-size toggle")
    p.add_argument("--live", dest="dry_run", action="store_false", default=None,
                   help="place REAL orders (default is dry-run)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    args = p.parse_args(argv)

    cfg = _build_config(args)
    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.mode == "poll":
        _run_poll(cfg)
    elif args.mode == "webhook":
        from .webhook import run as run_webhook
        run_webhook(cfg)
    else:
        _run_once(cfg)


if __name__ == "__main__":
    main()
