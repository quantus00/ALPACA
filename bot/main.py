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
  close-all -- sell EVERY live Coinbase spot position back to USD. Dry-run
              (prints the plan) unless you pass --yes. Skips dust below the
              product minimum (use Coinbase Convert for those).
  alex     -- run Alex's market-structure strategy on ANY asset the broker
              supports (free-form symbol + asset class). Crypto on Coinbase or
              Webull; futures on Webull (official OpenAPI). Dry-run unless --live.

Examples:
  python -m bot.main poll --broker coinbase --instrument btc_usd_spot --size 0.01
  python -m bot.main alex --broker coinbase --asset crypto --symbol ETH-USD \\
                          --size 0.05 --entry-tf 15m --structure-tf 1h --once
  python -m bot.main alex --broker webull --asset futures --symbol MES \\
                          --size 1 --live
  python -m bot.main close-all                 # dry-run: show what would be sold
  python -m bot.main close-all --min-usd 1 --yes   # sell all positions >= $1 to USD
  # Backtest (keyless): crypto via Coinbase, futures via Yahoo =F tickers
  python -m bot.main alex --asset crypto  --symbol BTC-USD --backtest --bars 800
  python -m bot.main alex --asset futures --symbol MES --backtest --entry-tf 1h --trades
  python -m bot.main dual --leg coinbase:btc_usd_spot:0.01 \\
                          --leg webull:spy_options:1 --tp 2 --sl 1 --live
  python -m bot.main flatten
  python -m bot.main balances --leg coinbase --leg webull
"""
from __future__ import annotations

import argparse
import logging
import os
import time

from . import dual as dual_mod
from .brokers import get_broker
from .config import AssetClass, Broker, Config, Instrument
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
    if getattr(args, "flatten_mode", None) is not None:
        cfg.flatten_mode = args.flatten_mode
    # Free-symbol (Alex) mode: a free-form symbol + asset class.
    if getattr(args, "symbol", None):
        cfg.symbol_override = args.symbol
    if getattr(args, "asset", None):
        cfg.asset_class = AssetClass(args.asset)
    if validate:
        cfg.validate()
    return cfg


def _run_alex(cfg: Config, args) -> None:
    if not cfg.free_mode():
        raise SystemExit(
            "alex mode needs --symbol and --asset, e.g.\n"
            "  python -m bot.main alex --broker coinbase --asset crypto "
            "--symbol ETH-USD --size 0.05 --once")
    from . import alex as alex_mod
    # Timeframes reach the Alex runner via env (the app is env-configurable).
    if args.entry_tf:
        os.environ["ALEX_ENTRY_TF"] = args.entry_tf
    if args.structure_tf:
        os.environ["ALEX_STRUCTURE_TF"] = args.structure_tf
    params = alex_mod.params_from(args)
    if args.backtest:
        from . import backtest as bt_mod
        tf = args.entry_tf or "15m"
        bt_mod.run_backtest(cfg, params, tf, source=args.source or "auto",
                            csv=args.csv, bars=args.bars, warmup=args.warmup,
                            show_trades=args.trades)
    elif args.once:
        alex_mod.analyze(cfg, params)
    else:
        alex_mod.run_loop(cfg, params)


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
    dual_mod.monitor(cfg, legs, dual_mod.rules_from(cfg), cfg.flatten_mode)


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
                            "status", "balances", "alex", "close-all"])
    p.add_argument("--broker", choices=[b.value for b in Broker])
    p.add_argument("--instrument", choices=[i.value for i in Instrument])
    p.add_argument("--size", type=float, help="contract-size toggle")
    # Alex strategy mode: free-form symbol + asset class (crypto/futures).
    p.add_argument("--symbol", help="alex: any symbol the broker trades "
                                    "(e.g. BTC-USD, ETH-USD, MES)")
    p.add_argument("--asset", choices=[a.value for a in AssetClass],
                   help="alex: asset class for --symbol (crypto|futures)")
    p.add_argument("--entry-tf", dest="entry_tf", help="alex: entry timeframe")
    p.add_argument("--structure-tf", dest="structure_tf",
                   help="alex: higher timeframe for market structure")
    p.add_argument("--once", action="store_true",
                   help="alex: evaluate a single tick and print, don't loop")
    p.add_argument("--backtest", action="store_true",
                   help="alex: backtest the strategy on historical candles")
    p.add_argument("--source", choices=["auto", "coinbase", "yahoo", "csv"],
                   help="alex --backtest: data source (default: auto by asset)")
    p.add_argument("--csv", help="alex --backtest: OHLC csv file (implies --source csv)")
    p.add_argument("--bars", type=int, default=500,
                   help="alex --backtest: how many candles to pull (default 500)")
    p.add_argument("--warmup", type=int, default=60,
                   help="alex --backtest: bars skipped before the first trade")
    p.add_argument("--trades", action="store_true",
                   help="alex --backtest: print each closed trade")
    # close-all: flatten every live spot position back to USD.
    p.add_argument("--yes", action="store_true",
                   help="close-all: actually place the SELL orders (else dry-run)")
    p.add_argument("--min-usd", dest="min_usd", type=float, default=0.0,
                   help="close-all: skip positions worth less than this many USD")
    p.add_argument("--only", help="close-all: comma-separated currencies to close "
                                  "(e.g. SPX,LOKA,ROSE); default = all")
    p.add_argument("--pivot-lookback", dest="pivot_lookback", type=int, default=3)
    p.add_argument("--aoi-tol", dest="aoi_tol", type=float, default=0.0015)
    p.add_argument("--min-touches", dest="min_touches", type=int, default=3)
    p.add_argument("--wick-ratio", dest="wick_ratio", type=float, default=1.5)
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--live", dest="dry_run", action="store_false", default=None,
                   help="place REAL orders (default is dry-run)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    # Dual-broker options.
    p.add_argument("--leg", action="append",
                   help="dual/balances: a leg as broker:instrument:size "
                        "(repeatable). For `balances`, a bare broker name works.")
    p.add_argument("--side", choices=["buy", "sell"], default="buy",
                   help="dual: open direction for every leg (default buy)")
    p.add_argument("--flatten-mode", dest="flatten_mode",
                   choices=["combined", "single"],
                   help="dual: a tripped per-leg rule closes all legs "
                        "(combined) or only that leg (single)")
    p.add_argument("--tp", type=float, help="combined take-profit %%")
    p.add_argument("--sl", type=float, help="combined stop-loss %% (magnitude)")
    p.add_argument("--leg-tp", dest="leg_tp", type=float, help="per-leg take-profit %%")
    p.add_argument("--leg-sl", dest="leg_sl", type=float,
                   help="per-leg stop-loss %% (magnitude)")
    args = p.parse_args(argv)

    # Single-broker modes validate the base broker/instrument pairing; the
    # multi-leg modes validate each leg on its own. Alex validates its own
    # free-symbol/asset pairing inside _build_config.
    needs_base_validate = args.mode in ("poll", "webhook", "once", "alex")
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
    elif args.mode == "alex":
        _run_alex(cfg, args)
    elif args.mode == "close-all":
        from . import close_all as ca
        ca.plan_and_close(cfg, execute=args.yes, min_usd=args.min_usd,
                          only=(args.only.split(",") if args.only else None))
    else:
        _run_once(cfg)


if __name__ == "__main__":
    main()
