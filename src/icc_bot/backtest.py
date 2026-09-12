"""Backtest the ICC strategy over a CSV of candles (e.g. a TradingView export).

Feeds an entry-timeframe (LTF) CSV, resamples it up to the structure timeframe
(HTF), and replays it bar-by-bar through the same `evaluate()` + `Simulator`
used live/paper — no lookahead (pivots are confirmed, HTF bars are only used
once closed). Prints performance stats and optionally writes the trade list.

    icc-backtest --ltf-csv COINBASE_BTCUSD,15.csv --htf-seconds 3600
    icc-backtest --ltf-csv btc_15m.csv --equity 10000 --risk-pct 1 --out trades.csv

The course discourages backtesting; treat results as a sanity check on the
mechanical rules, not a promise of live performance.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from typing import List, Optional

from .models import Bar
from .sim import Simulator, stats
from .strategy import ICCParams, evaluate

_TIME_KEYS = ("time", "timestamp", "date", "datetime", "unix", "open time")


def _parse_ts(raw: str) -> int:
    raw = raw.strip().strip('"')
    try:
        v = float(raw)
        return int(v / 1000) if v > 1e12 else int(v)   # ms vs s
    except ValueError:
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())


def load_csv(path: str) -> List[Bar]:
    """Load OHLCV bars from a CSV with a header (case-insensitive columns)."""
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
        tkey = next((cols[k] for k in _TIME_KEYS if k in cols), None)
        need = {}
        for name in ("open", "high", "low", "close"):
            match = next((cols[k] for k in cols if k == name), None)
            if match is None:
                raise ValueError(f"CSV missing an '{name}' column (have {list(cols)})")
            need[name] = match
        if tkey is None:
            raise ValueError(f"CSV missing a time column (have {list(cols)})")
        vkey = next((cols[k] for k in cols if k in ("volume", "vol")), None)

        bars: List[Bar] = []
        for row in reader:
            try:
                bars.append(Bar(
                    ts=_parse_ts(row[tkey]),
                    open=float(row[need["open"]]), high=float(row[need["high"]]),
                    low=float(row[need["low"]]), close=float(row[need["close"]]),
                    volume=float(row[vkey]) if vkey and row.get(vkey) not in (None, "") else 0.0,
                ))
            except (ValueError, KeyError):
                continue   # skip malformed rows
    bars.sort(key=lambda b: b.ts)
    return bars


def resample(ltf: List[Bar], seconds: int) -> List[Bar]:
    """Aggregate LTF bars into higher-timeframe bars of `seconds` each."""
    buckets: dict[int, List[Bar]] = {}
    for b in ltf:
        buckets.setdefault((b.ts // seconds) * seconds, []).append(b)
    out = []
    for start in sorted(buckets):
        g = buckets[start]
        out.append(Bar(ts=start, open=g[0].open, high=max(x.high for x in g),
                       low=min(x.low for x in g), close=g[-1].close,
                       volume=sum(x.volume for x in g)))
    return out


def run_backtest(symbol: str, ltf: List[Bar], htf_seconds: int,
                 params: ICCParams, equity: float, risk_pct: float) -> Simulator:
    htf_all = resample(ltf, htf_seconds)
    sim = Simulator(equity=equity, risk_per_trade_pct=risk_pct)
    need = max(params.htf_lookback, params.ltf_lookback) * 2 + 3
    j = 0  # count of HTF bars closed as of the current LTF time
    for i in range(len(ltf)):
        t = ltf[i].ts
        while j < len(htf_all) and htf_all[j].ts + htf_seconds <= t:
            j += 1
        if i + 1 >= need and j >= need and sim.open_trade is None:
            sig = evaluate(symbol, htf_all[:j], ltf[:i + 1], params)
            if sig is not None:
                sim.enter(sig)
        sim.update(ltf[i])
    if sim.open_trade is not None:
        sim.close_at(ltf[-1].close, ltf[-1].ts)
    return sim


def _write_trades(path: str, sim: Simulator) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "direction", "qty", "entry", "stop", "target",
                    "exit", "outcome", "pnl", "r_multiple", "entry_ts", "exit_ts"])
        for t in sim.trades:
            w.writerow([t.symbol, t.direction.value, f"{t.qty:.6f}", t.entry, t.stop,
                        t.target, t.exit, t.outcome, round(t.pnl, 2),
                        round(t.r_multiple, 2), t.entry_ts, t.exit_ts])


def main() -> None:
    ap = argparse.ArgumentParser(prog="icc-backtest",
                                 description="Backtest the ICC strategy over a candle CSV.")
    ap.add_argument("--ltf-csv", required=True, help="entry-timeframe OHLCV CSV")
    ap.add_argument("--htf-seconds", type=int, default=3600,
                    help="structure timeframe in seconds to resample to (default 3600 = 1h)")
    ap.add_argument("--symbol", default="BACKTEST")
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--target-rr", type=float, default=3.0)
    ap.add_argument("--htf-lookback", type=int, default=5)
    ap.add_argument("--ltf-lookback", type=int, default=3)
    ap.add_argument("--out", help="optional path to write the trade list CSV")
    args = ap.parse_args()

    ltf = load_csv(args.ltf_csv)
    if not ltf:
        print("No bars loaded from", args.ltf_csv)
        return
    params = ICCParams(htf_lookback=args.htf_lookback, ltf_lookback=args.ltf_lookback,
                       target_rr=args.target_rr)
    sim = run_backtest(args.symbol, ltf, args.htf_seconds, params, args.equity, args.risk_pct)

    span = f"{datetime.utcfromtimestamp(ltf[0].ts):%Y-%m-%d} .. {datetime.utcfromtimestamp(ltf[-1].ts):%Y-%m-%d}"
    print(f"\nICC backtest — {args.symbol}  ({len(ltf)} LTF bars, {span}, "
          f"HTF={args.htf_seconds}s, RR={args.target_rr})")
    print("-" * 60)
    for k, v in stats(sim).items():
        print(f"  {k:18} {v}")
    if args.out:
        _write_trades(args.out, sim)
        print(f"\n  wrote {len(sim.trades)} trades -> {args.out}")


if __name__ == "__main__":
    main()
