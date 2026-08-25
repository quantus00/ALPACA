"""Coinbase 1-minute Fair-Value-Gap live bot.

Self-contained runnable bot: pulls 1-minute Coinbase candles, feeds each newly
CLOSED candle to :class:`~bot.fvg.FVGStrategy`, and routes the resulting
enter/exit signals to the Coinbase broker as market orders.

Instruments:
  * ``btc_usd_spot``  -> long/flat only (no shorting on spot); bearish FVGs are
    entries only while flat and are skipped (a bearish FVG never opens a short).
  * ``btc_nano_perp`` -> long and short (requires an INTX perp-enabled account).

Safety:
  * DRY-RUN by default. Real orders require ``--live`` (or BOT_DRY_RUN=false).
  * API keys come only from the environment (COINBASE_API_KEY / _SECRET).

Modes:
  python -m bot.fvg_bot                     # live 1m data, DRY-RUN (no keys needed)
  python -m bot.fvg_bot --live              # live data + REAL Coinbase orders
  python -m bot.fvg_bot --replay btc_1m.csv # drive from a CSV bar-by-bar (offline)
  python -m bot.fvg_bot --selftest          # synthetic logic check, no net/keys

CSV columns: time,open,high,low,close[,volume] (time = unix seconds).
"""
from __future__ import annotations

import argparse
import csv
import logging
import time

from .brokers import get_broker
from .config import Broker, Config, Instrument
from .data import get_candles, timeframe_seconds
from .fvg import FVGStrategy
from .notifier import Notifier
from .trend import Candle

log = logging.getLogger("fvg")


# --------------------------------------------------------------------------- io
def load_csv(path: str) -> list[Candle]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    keys = {k.lower().strip(): k for k in rows[0]}

    def col(*names):
        for n in names:
            if n in keys:
                return keys[n]
        return None

    kt = col("time", "date", "timestamp", "datetime")
    ko, kh, kl, kc = col("open"), col("high"), col("low"), col("close")
    kv = col("volume", "vol")
    out: list[Candle] = []
    for r in rows:
        try:
            out.append(Candle(
                time=int(float(r[kt])), open=float(r[ko]), high=float(r[kh]),
                low=float(r[kl]), close=float(r[kc]),
                volume=float(r[kv]) if kv and r[kv] else 0.0))
        except (ValueError, TypeError, KeyError):
            continue
    out.sort(key=lambda c: c.time)
    return out


# ---------------------------------------------------------------- strategy glue
def _make_strategy(cfg: Config) -> FVGStrategy:
    allow_short = cfg.instrument == Instrument.BTC_NANO_PERP
    return FVGStrategy(rr=cfg.fvg_rr, buffer=cfg.fvg_buffer,
                       max_hold=cfg.fvg_max_hold, allow_short=allow_short,
                       min_gap_frac=cfg.fvg_min_gap_frac)


def _act(cfg: Config, broker, notifier: Notifier, sig, position_side: list) -> None:
    """Translate an FVG signal into a broker order. ``position_side`` is a
    one-element list holding the current side ('long'/'short'/None) so exits
    send the correct closing side and we never stack positions."""
    if sig.action == "enter":
        order_side = "buy" if sig.side == "long" else "sell"
        res = broker.place_order(order_side, cfg.contract_size)
        if res.ok:
            position_side[0] = sig.side
            log.info("ENTER %s %s @ %.2f stop=%.2f target=%.2f (%s) id=%s",
                     sig.side, res.symbol, sig.price, sig.stop or 0, sig.target or 0,
                     sig.reason, res.order_id)
            notifier.notify_entry(order_side, res.symbol, sig.price, cfg.contract_size)
        else:
            log.error("ENTER failed: %s", res.error)
    elif sig.action == "exit":
        # close: opposite side of the position we hold
        order_side = "sell" if position_side[0] == "long" else "buy"
        res = broker.place_order(order_side, cfg.contract_size)
        if res.ok:
            log.info("EXIT %s @ %.2f (%s) id=%s", res.symbol, sig.price, sig.reason,
                     res.order_id)
            position_side[0] = None
        else:
            log.error("EXIT failed: %s", res.error)


# --------------------------------------------------------------------- run loops
def run_replay(cfg: Config, path: str) -> None:
    strat = _make_strategy(cfg)
    broker = get_broker(cfg)
    notifier = Notifier()
    candles = load_csv(path)
    log.info("Replay %d candles from %s (%s)", len(candles), path, cfg.describe())
    position_side = [None]
    for c in candles:
        sig = strat.update(c)
        if sig.action in ("enter", "exit"):
            _act(cfg, broker, notifier, sig, position_side)
    if position_side[0] is not None:
        log.info("Replay ended with an OPEN %s position (not auto-closed).",
                 position_side[0])


def run_live(cfg: Config) -> None:
    strat = _make_strategy(cfg)
    broker = get_broker(cfg)
    notifier = Notifier()
    symbol = cfg.symbol()
    tf = cfg.fvg_timeframe
    tf_sec = timeframe_seconds(tf)
    position_side = [None]
    last_seen = 0  # unix time of the last closed candle we processed

    log.info("FVG live: %s %s poll=%ss dry_run=%s (%s)", symbol, tf,
             cfg.poll_seconds, cfg.dry_run, cfg.describe())
    while True:
        try:
            candles = get_candles(symbol, tf, limit=200)
            now = time.time()
            # Only act on FULLY CLOSED candles: a candle for bucket t closes at t+tf_sec.
            closed = [c for c in candles if c.time + tf_sec <= now]
            fresh = [c for c in closed if c.time > last_seen]
            for c in fresh:
                sig = strat.update(c)
                last_seen = c.time
                if sig.action in ("enter", "exit"):
                    _act(cfg, broker, notifier, sig, position_side)
                else:
                    log.debug("candle %s close=%.2f -> no signal", c.time, c.close)
        except Exception:  # noqa: BLE001
            log.exception("fvg tick failed")
        time.sleep(cfg.poll_seconds)


# ---------------------------------------------------------------------- selftest
def selftest() -> bool:
    """Synthetic candles that must produce a bullish FVG long, then a target
    exit. Returns True on success."""
    def c(t, o, h, l, cl):
        return Candle(time=t * 60, open=o, high=h, low=l, close=cl, volume=1.0)

    strat = FVGStrategy(rr=1.0, buffer=0.0, allow_short=True)
    # bars 0,1 baseline; bar 2 gaps up (low 105 > bar0 high 101) -> bullish FVG, enter long @108
    seq = [
        c(0, 100, 101, 99, 100),
        c(1, 100, 102, 99, 101),
        c(2, 106, 109, 105, 108),   # FVG: low 105 > high[0]=101 ; enter long @108, stop=101, target=115
        c(3, 108, 116, 107, 115),   # high 116 >= target 115 -> exit target
    ]
    sigs = [strat.update(x) for x in seq]
    enter = sigs[2]
    exit_ = sigs[3]
    ok = (enter.action == "enter" and enter.side == "long"
          and abs(enter.stop - 101) < 1e-9 and abs(enter.target - 115) < 1e-9
          and exit_.action == "exit" and exit_.reason == "target")
    print("selftest:", "PASS" if ok else "FAIL")
    if not ok:
        print("  enter:", enter)
        print("  exit :", exit_)
    return ok


# --------------------------------------------------------------------------- cli
def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Coinbase 1-min FVG live bot")
    p.add_argument("--broker", choices=[b.value for b in Broker], default=None)
    p.add_argument("--instrument", choices=[i.value for i in Instrument], default=None)
    p.add_argument("--size", type=float, default=None, help="base-asset qty per order")
    p.add_argument("--live", dest="dry_run", action="store_false", default=None,
                   help="place REAL orders (default is dry-run)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    p.add_argument("--replay", metavar="CSV", help="drive from a 1m CSV, offline")
    p.add_argument("--selftest", action="store_true", help="run the synthetic logic check")
    args = p.parse_args(argv)

    if args.selftest:
        raise SystemExit(0 if selftest() else 1)

    cfg = Config()
    # default this bot to Coinbase spot unless overridden
    cfg.broker = Broker(args.broker) if args.broker else Broker.COINBASE
    cfg.instrument = Instrument(args.instrument) if args.instrument else Instrument.BTC_USD_SPOT
    if args.size is not None:
        cfg.contract_size = args.size
    if args.dry_run is not None:
        cfg.dry_run = args.dry_run
    cfg.validate()

    logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.replay:
        run_replay(cfg, args.replay)
    else:
        run_live(cfg)


if __name__ == "__main__":
    main()
