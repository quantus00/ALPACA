"""Opening Range Breakout bot: ORB engine + challenge guard + broker.

This is the end-to-end runner that ties the pieces together into something you
can actually point at a funded-account evaluation:

    candles --> ORBEngine (direction + levels)
            --> ChallengeGuard (may veto entry / force flatten; sizes the trade)
            --> broker (place / flatten)

The guard sizes every entry to the *risk of that specific breakout* (entry-to-
stop distance) and to the remaining daily / trailing buffers, so a single
stop-out can never breach a limit. It force-flattens at 5pm ET (and on any daily
or trailing breach), and halts once the profit target is banked.

Equity is tracked locally from realized + unrealized P&L on top of the
configured starting balance, so the guard works without a live account-equity
feed. Dry-run by default: pass ``--live`` to send real orders.

    python -m bot.orb_bot                                 # offline sanity run (selftest)
    python -m bot.orb_bot poll --broker tradovate --instrument mes_futures --symbol MES
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .orb import ORBEngine
from .risk import ChallengeGuard, point_value

log = logging.getLogger("orb_bot")


@dataclass
class Position:
    side: int = 0          # -1 short, +1 long, 0 flat
    contracts: int = 0
    entry: float = 0.0


class ORBBot:
    """Bar-driven ORB runner. Feed closed bars to :meth:`on_bar`."""

    def __init__(self, cfg: Config, symbol: str, engine: ORBEngine | None = None,
                 guard: ChallengeGuard | None = None, broker=None):
        self.cfg = cfg
        self.symbol = symbol.upper()
        self.pv = point_value(self.symbol)
        self.engine = engine or ORBEngine(cfg.build_orb_params())
        self.guard = guard or ChallengeGuard(cfg.build_challenge_params())
        self.broker = broker
        self.pos = Position()
        self.realized = 0.0        # realized $ P&L since start

    # -- equity ---------------------------------------------------------------
    def _unrealized(self, price: float) -> float:
        if self.pos.side == 0:
            return 0.0
        pts = (price - self.pos.entry) * self.pos.side
        return pts * self.pv * self.pos.contracts

    def equity(self, price: float) -> float:
        return self.guard.p.starting_balance + self.realized + self._unrealized(price)

    # -- order plumbing -------------------------------------------------------
    def _flatten(self, price: float, why: str) -> None:
        if self.pos.side == 0:
            return
        pnl = self._unrealized(price)
        self.realized += pnl
        log.info("FLATTEN %s @ %.2f  (%s)  trade P&L=%+.2f  realized=%+.2f",
                 "long" if self.pos.side > 0 else "short", price, why, pnl, self.realized)
        if self.broker is not None:
            self.broker.flatten()
        self.pos = Position()

    def _enter(self, side: int, price: float, contracts: int) -> None:
        self.pos = Position(side=side, contracts=contracts, entry=price)
        log.info("ENTER %s %d @ %.2f", "long" if side > 0 else "short", contracts, price)
        if self.broker is not None:
            self.broker.place_order("buy" if side > 0 else "sell", contracts)

    # -- main tick ------------------------------------------------------------
    def on_bar(self, ts: datetime, o: float, h: float, l: float, c: float) -> None:
        """Process one closed ET-stamped bar."""
        # 1) Account-level guard first (uses equity marked at this bar's close).
        signed = self.pos.side * self.pos.contracts
        decision = self.guard.update(ts, self.equity(c), signed)
        if decision.must_flatten:
            self._flatten(c, decision.reason)
            return

        # 2) Strategy engine.
        sig = self.engine.on_bar(ts, o, h, l, c)

        if sig.action == "exit":
            self._flatten(sig.price, sig.reason)
        elif sig.action in ("enter_long", "enter_short") and self.pos.side == 0:
            if not decision.can_enter:
                log.info("breakout blocked by guard: %s", decision.reason)
                return
            contracts = self.guard.size_for(decision, sig.risk_points, self.symbol)
            if contracts <= 0:
                log.info("breakout skipped: buffer too small to size a contract")
                return
            side = 1 if sig.action == "enter_long" else -1
            self._enter(side, sig.price, contracts)

    # -- batch replay ---------------------------------------------------------
    def replay(self, bars) -> dict:
        """Run a list of ``(ts, o, h, l, c)`` bars; return a summary."""
        for b in bars:
            self.on_bar(*b)
        return {
            "realized": self.realized,
            "equity": self.equity(bars[-1][4]) if bars else self.guard.p.starting_balance,
            "target_reached": self.guard.target_reached,
            "failed": self.guard.failed,
        }


# ---------------------------------------------------------------------------
# Offline self-test: two clean +2R breakout days, sized by the guard.
# ---------------------------------------------------------------------------
def _selftest_bars():
    out = []
    for day in (10, 11):
        out += [
            (datetime(2026, 9, day, 9, 30), 5005, 5008, 5000, 5006),
            (datetime(2026, 9, day, 9, 35), 5006, 5010, 5003, 5009),
            (datetime(2026, 9, day, 9, 40), 5009, 5010, 5005, 5008),
            (datetime(2026, 9, day, 9, 45), 5008, 5011, 5008, 5010.5),   # breakout
            (datetime(2026, 9, day, 9, 50), 5011, 5031, 5011, 5030),     # +2R target
            (datetime(2026, 9, day, 15, 55), 5030, 5031, 5029, 5030),    # EOD
        ]
    return out


def selftest() -> dict:
    cfg = Config()
    cfg.challenge_stop_points = 0     # use the range stop for the demo
    bot = ORBBot(cfg, symbol="MES")
    res = bot.replay(_selftest_bars())
    print(f"realized P&L : ${res['realized']:.2f}")
    print(f"final equity : ${res['equity']:.2f}")
    print(f"target hit   : {res['target_reached']}")
    print(f"failed (DD)  : {res['failed']}")
    return res


def _to_et(epoch: int, tz):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(tz)


def run_poll(cfg: Config, symbol: str) -> None:  # pragma: no cover - live loop
    import time as _time

    from .brokers import get_broker
    from .data import get_candles

    guard = ChallengeGuard(cfg.build_challenge_params())
    bot = ORBBot(cfg, symbol=symbol, guard=guard, broker=get_broker(cfg))
    tz = guard._tz
    last_ts = None
    log.info("ORB poll every %ss  (%s)", cfg.poll_seconds, cfg.describe())
    while True:
        try:
            candles = get_candles(symbol, "1m", limit=400)
            # Act only on the most recent *closed* bar we have not seen yet.
            for cd in candles[:-1]:
                ts = _to_et(cd.time, tz) if tz else datetime.fromtimestamp(cd.time)
                if last_ts is not None and ts <= last_ts:
                    continue
                bot.on_bar(ts, cd.open, cd.high, cd.low, cd.close)
                last_ts = ts
        except Exception:  # noqa: BLE001
            log.exception("orb poll tick failed")
        _time.sleep(cfg.poll_seconds)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Opening Range Breakout bot")
    p.add_argument("mode", nargs="?", default="selftest", choices=["selftest", "poll"])
    p.add_argument("--broker")
    p.add_argument("--instrument")
    p.add_argument("--symbol", default="MES")
    p.add_argument("--live", dest="dry_run", action="store_false", default=True)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.mode == "selftest":
        selftest()
        return

    from .config import Broker, Instrument
    cfg = Config()
    cfg.challenge_enabled = True
    if args.broker:
        cfg.broker = Broker(args.broker)
    if args.instrument:
        cfg.instrument = Instrument(args.instrument)
    cfg.dry_run = args.dry_run
    cfg.validate()
    run_poll(cfg, args.symbol)


if __name__ == "__main__":
    main()
