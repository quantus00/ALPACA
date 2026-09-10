"""Reproducible ORB backtest + report for the prop challenge.

Loads a CSV of 1-minute OHLC bars, runs them through the ORB engine + the
ChallengeGuard (real sizing, real trailing-drawdown accounting), and prints the
metrics the evaluation actually cares about:

    total trades, win rate, avg win / avg loss ($), net P&L, max drawdown ($),
    worst single day ($), whether the trailing drawdown FAILED the account (and
    when), whether the profit target was reached (and how many trading days it
    took), and the distribution of exit reasons.

Then it stress-tests across opening-range lengths {5, 15, 30} and target-R
{1, 2, 3} so you can see whether any single parameter is carrying the result
(a sign of overfitting rather than a real edge).

CSV format: a header row with columns time, open, high, low, close (extra
columns ignored). ``time`` may be an ISO-8601 stamp or epoch seconds. If the
stamp is timezone-aware it is converted to US/Eastern; if naive it is assumed to
already be ET (the ORB engine keys off the 09:30 ET open).

    python -m bot.orb_backtest data/mgc_1m.csv --symbol MGC --tick 0.10

This does NOT fetch data and does NOT optimise parameters — it reports what the
bars say, including when the answer is "no edge".
"""
from __future__ import annotations

import argparse
import csv as _csv
from datetime import datetime, timezone

from .config import Config
from .orb import ORBEngine, ORBParams
from .orb_bot import ORBBot
from .risk import ChallengeParams, ChallengeGuard, point_value

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None


def load_csv(path: str) -> list[tuple]:
    """Return a list of ``(et_datetime, open, high, low, close)`` bars."""
    out: list[tuple] = []
    with open(path, newline="") as fh:
        reader = _csv.DictReader(fh)
        cols = {c.lower(): c for c in (reader.fieldnames or [])}
        need = ("time", "open", "high", "low", "close")
        missing = [c for c in need if c not in cols]
        if missing:
            raise ValueError(f"CSV missing columns: {missing}; found {reader.fieldnames}")
        for row in reader:
            ts = _parse_ts(row[cols["time"]])
            out.append((ts, float(row[cols["open"]]), float(row[cols["high"]]),
                        float(row[cols["low"]]), float(row[cols["close"]])))
    out.sort(key=lambda b: b[0])
    return out


def _parse_ts(raw: str) -> datetime:
    raw = raw.strip()
    if raw.isdigit():
        dt = datetime.fromtimestamp(int(raw), tz=timezone.utc)
    else:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        return dt.astimezone(_ET).replace(tzinfo=None) if _ET else dt.replace(tzinfo=None)
    return dt  # naive: assumed already ET


def run(bars, symbol: str, tick: float, or_minutes: int, target_r: float,
        cparams: ChallengeParams) -> ORBBot:
    params = ORBParams(or_minutes=or_minutes, tick_size=tick, target_r=target_r,
                       stop="range")
    bot = ORBBot(Config(), symbol=symbol, engine=ORBEngine(params),
                 guard=ChallengeGuard(cparams))
    bot.replay(bars)
    return bot


def report(bot: ORBBot, cparams: ChallengeParams) -> dict:
    trades = bot.trades
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]

    # Max drawdown from the equity curve.
    peak = cparams.starting_balance
    max_dd = 0.0
    for _ts, eq in bot.equity_curve:
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    # Worst single day (sum of trade P&L per calendar day).
    by_day: dict = {}
    for t in trades:
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["pnl"]
    worst_day = min(by_day.values()) if by_day else 0.0

    # Days to target = distinct trading dates up to and including target_date.
    days_to_target = None
    if bot.target_date is not None:
        seen = {ts.date() for ts, _ in bot.equity_curve if ts.date() <= bot.target_date}
        days_to_target = len(seen)

    def bucket(reason: str) -> str:
        r = reason.lower()
        if "target" in r:
            return "target"
        if "stop" in r:
            return "stop"
        if "eod" in r:
            return "eod"
        if "5pm" in r or "flat by" in r:
            return "5pm_flat"
        if "daily" in r:
            return "daily_loss"
        if "trailing" in r:
            return "trailing_dd"
        return "other"

    exits: dict = {}
    for t in trades:
        b = bucket(t["reason"])
        exits[b] = exits.get(b, 0) + 1

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "avg_win": (sum(t["pnl"] for t in wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.0,
        "net_pnl": bot.realized,
        "max_drawdown": max_dd,
        "worst_day": worst_day,
        "failed": bot.guard.failed,
        "failed_date": bot.failed_date,
        "target_reached": bot.guard.target_reached,
        "days_to_target": days_to_target,
        "exits": exits,
    }


def print_report(r: dict) -> None:
    print("---- ORB + Challenge backtest --------------------------------------")
    print(f"  trades            : {r['trades']}  ({r['wins']}W / {r['losses']}L)")
    print(f"  win rate          : {r['win_rate']:.1%}")
    print(f"  avg win / loss    : ${r['avg_win']:.2f} / ${r['avg_loss']:.2f}")
    print(f"  net P&L           : ${r['net_pnl']:.2f}")
    print(f"  max drawdown      : ${r['max_drawdown']:.2f}")
    print(f"  worst single day  : ${r['worst_day']:.2f}")
    if r["failed"]:
        print(f"  TRAILING DD       : FAILED on {r['failed_date']}  <-- account blown")
    else:
        print(f"  TRAILING DD       : survived (never breached)")
    if r["target_reached"]:
        print(f"  profit target     : REACHED in {r['days_to_target']} trading day(s)")
    else:
        print(f"  profit target     : not reached")
    print(f"  exit reasons      : {r['exits']}")


def sweep(bars, symbol: str, tick: float, cparams: ChallengeParams) -> None:
    print("\n---- Stress test: OR length x target-R (net $ / trades / win% / result) ----")
    print(f"{'':>10}" + "".join(f"{'R='+str(tr):>22}" for tr in (1, 2, 3)))
    for orm in (5, 15, 30):
        cells = []
        for tr in (1, 2, 3):
            bot = run(bars, symbol, tick, orm, tr, cparams)
            r = report(bot, cparams)
            res = "FAIL" if r["failed"] else ("PASS" if r["target_reached"] else "-")
            cells.append(f"${r['net_pnl']:>8.0f}/{r['trades']:>3}/{r['win_rate']:>4.0%}/{res:>4}")
        print(f"OR={orm:>3}m   " + "".join(f"{c:>22}" for c in cells))
    print("\nIf one cell dwarfs the rest, the 'edge' is likely curve-fit, not real.")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="ORB prop-challenge backtest + report")
    p.add_argument("csv", help="path to 1-minute OHLC CSV (time,open,high,low,close)")
    p.add_argument("--symbol", default="MGC")
    p.add_argument("--tick", type=float, default=0.10)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--target-r", type=float, default=2.0)
    p.add_argument("--start", type=float, default=50_000)
    p.add_argument("--target", type=float, default=1_500)
    p.add_argument("--daily-loss", type=float, default=500)
    p.add_argument("--trailing-dd", type=float, default=1_000)
    p.add_argument("--trailing-mode", default="intraday", choices=["intraday", "eod"])
    p.add_argument("--no-sweep", action="store_true")
    args = p.parse_args(argv)

    point_value(args.symbol)  # fail fast if the instrument is unknown
    bars = load_csv(args.csv)
    if not bars:
        print("No bars loaded.")
        return
    print(f"Loaded {len(bars)} bars for {args.symbol}  "
          f"({bars[0][0]} -> {bars[-1][0]})")

    cparams = ChallengeParams(
        starting_balance=args.start, profit_target=args.target,
        daily_loss_limit=args.daily_loss, trailing_drawdown=args.trailing_dd,
        trailing_mode=args.trailing_mode)

    bot = run(bars, args.symbol, args.tick, args.or_minutes, args.target_r, cparams)
    print_report(report(bot, cparams))
    if not args.no_sweep:
        sweep(bars, args.symbol, args.tick, cparams)


if __name__ == "__main__":
    main()
