"""Signal worker: runs the trend engine for each instrument on a timer, stores
4H trend flips and alignment entries, and fans them out to subscribers.

    python -m saas.engine            # loop forever (the 'worker' container)
    python -m saas.engine --once     # one tick, then exit
"""
from __future__ import annotations

import argparse
import logging
import time

from bot.config import INSTRUMENT_BROKERS, Config, Instrument
from bot.strategy import StrategyRunner
from bot.trend import Trend

from . import db
from .delivery import deliver
from .settings import Settings

log = logging.getLogger("saas.engine")


def _runner_for(instrument: str) -> StrategyRunner:
    cfg = Config()
    cfg.instrument = Instrument(instrument)
    cfg.broker = INSTRUMENT_BROKERS[cfg.instrument][0]   # unused: analysis only
    cfg.dry_run = True
    return StrategyRunner(cfg, notifier=None)


def _seed_last_trend(conn, runner: StrategyRunner, instrument: str) -> None:
    """Restore the last published trend so a restart doesn't re-announce it."""
    last = db.latest_trends(conn).get(instrument)
    if last:
        runner._last_trend = Trend.UP if last["direction"] == "UPTREND" else Trend.DOWN


def tick(conn, settings: Settings, runners: dict[str, StrategyRunner]) -> list[dict]:
    published: list[dict] = []
    for instrument, runner in runners.items():
        before = runner._last_trend
        try:
            sig = runner.evaluate()
        except Exception:  # noqa: BLE001
            log.exception("evaluate failed for %s", instrument)
            continue
        detail = " ".join(f"{tf}:{state[0]}" for tf, state in sig.aligned.items())
        log.info("%s 4H=%s align=%s action=%s", instrument, sig.trend.label(), detail, sig.action)

        if sig.trend != Trend.NEUTRAL and sig.trend != before:
            published.append(db.insert_signal(conn, instrument, "trend", sig.trend.label(),
                                              sig.price, "", "engine"))
        if sig.action in ("buy", "sell"):
            published.append(db.insert_signal(conn, instrument, "entry", sig.action.upper(),
                                              sig.price, f"Aligned {detail}", "engine"))
    for s in published:
        deliver(s, db.subscribers_for(conn, s["instrument"]), settings.brand,
                settings.telegram_bot_token)
    return published


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Signal worker")
    p.add_argument("--once", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    conn = db.connect(settings.db_path)
    runners = {i: _runner_for(i) for i in settings.instruments}
    for i, r in runners.items():
        _seed_last_trend(conn, r, i)
    log.info("worker started: instruments=%s every %ss", list(runners), settings.poll_seconds)
    while True:
        tick(conn, settings, runners)
        if args.once:
            return
        time.sleep(settings.poll_seconds)


if __name__ == "__main__":
    main()
