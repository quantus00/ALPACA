"""ICC hedge backtest — three variants on 1h data.

    python3 backtest/fetch_hedge_data.py      # once, on a machine with internet
    python3 backtest/hedge_backtest.py        # -> backtest/hedge_results.md + hedge_events.csv

Variants
    (a) ICC + normal stop          the hedge trigger is the stop
    (b) ICC + hedge                forex hedges only if corr(EURUSD, USDCHF) < threshold
    (c) ICC + hedge, no corr gate  (identical to (b) for BTC — the gate is forex-only)

ICC entry used here: the bot's rule — 4H structure trend (HH/HL / LH/LL, both
legs must break) aligned with the lower-timeframe structure, fired once per
alignment — evaluated on the timeframes 1h data can supply: 4H (built from 1h)
+ 1H. The live bot additionally requires 5m/15m/30m; those need sub-hour data
and are NOT in this backtest. All three variants share identical entries, so
the comparison isolates the hedge rules.

Every rule number comes from bot/hedge/hedge_config.toml.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time
from dataclasses import replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot.hedge import config as hcfg  # noqa: E402
from bot.hedge.engine import HedgeEngine  # noqa: E402
from bot.hedge.features import FeatureBuilder  # noqa: E402
from bot.trend import Candle  # noqa: E402

DATA = os.path.join(ROOT, "backtest", "data", "hedge")


# ----------------------------------------------------------------------------- data
def _parse_time(v: str) -> int:
    v = v.strip()
    try:
        x = float(v)
        return int(x / 1000) if x > 1e11 else int(x)
    except ValueError:
        return int(dt.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())


def load_csv(path: str) -> list[Candle]:
    out = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            r = {k.lower().strip(): v for k, v in r.items()}
            out.append(Candle(time=_parse_time(r["time"]), open=float(r["open"]),
                              high=float(r["high"]), low=float(r["low"]),
                              close=float(r["close"]), volume=float(r.get("volume") or 0)))
    out.sort(key=lambda c: c.time)
    dedup: dict[int, Candle] = {c.time: c for c in out}
    return [dedup[k] for k in sorted(dedup)]


def load_funding(path: str) -> dict[int, float]:
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        return {_parse_time(r["time"]): float(r["rate_pct"]) for r in csv.DictReader(fh)}


def last_months(bars: list[Candle], months: int) -> list[Candle]:
    if not bars:
        return bars
    cutoff = bars[-1].time - int(months * 30.4375 * 86400)
    return [b for b in bars if b.time >= cutoff]


# ----------------------------------------------------------------------------- run
def run(s: hcfg.PairSettings, mode: str, primary: list[Candle],
        hedge: list[Candle] | None = None, funding: dict[int, float] | None = None,
        warmup_bars: int = 300):
    """Run one variant. ``primary`` includes warm-up history before the test window;
    entries are only taken after ``warmup_bars``."""
    eng = HedgeEngine(s, mode=mode)
    fb = FeatureBuilder(s.atr_length, s.pivot_lookback, s.corr_window_days, s.corr_min_obs)
    hmap = {c.time: c for c in hedge} if hedge else None
    funding = funding or {}
    equity, peak, max_dd = 0.0, 0.0, 0.0
    entry_dir = 0
    ctx = None
    for i, c in enumerate(primary):
        h = hmap.get(c.time) if hmap is not None else None
        if hmap is not None and h is None:
            continue
        ctx = fb.update(c, h, funding.get(c.time + 3600), with_corr=not s.is_crypto)
        eng.on_bar(ctx)
        # --- ICC entry signal (unchanged rule, 1h-data version) -------------
        aligned = ctx.trend_4h if ctx.trend_4h != 0 and ctx.trend_1h == ctx.trend_4h else 0
        if aligned == 0:
            entry_dir = 0
        elif aligned != entry_dir:
            entry_dir = aligned
            want = aligned if (not s.is_crypto or aligned == 1) else 0
            if want and i >= warmup_bars and eng.is_flat and ctx.atr is not None:
                eng.open_primary(ctx, want)
        # --- equity curve (closed + open MTM) --------------------------------
        equity = sum(r.net_pnl for r in eng.results) + eng.mark_to_market(ctx)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    if ctx is not None:
        eng.force_close(ctx, "end of data")
    return eng, max_dd


def summarize(eng: HedgeEngine, max_dd: float) -> dict:
    res = eng.results
    n = len(res)
    wins = sum(1 for r in res if r.net_pnl > 0)
    hedged = [r for r in res if r.hedged]
    kinds: dict[str, int] = {}
    for e in eng.events:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    limit_reasons: dict[str, int] = {}
    for e in eng.events:
        if e.kind == "LIMIT_CLOSE":
            key = e.reason.split(" (")[0].split(" hit")[0]
            limit_reasons[key] = limit_reasons.get(key, 0) + 1
    return {
        "setups": n,
        "win_rate": 100.0 * wins / n if n else 0.0,
        "net_pnl": sum(r.net_pnl for r in res),
        "fees": sum(r.fees for r in res),
        "funding_paid": sum(r.funding_paid for r in res),
        "max_dd": max_dd,
        "hedges": kinds.get("HEDGE_OPEN", 0),
        "skipped": kinds.get("HEDGE_SKIPPED", 0),
        "lifts": kinds.get("HEDGE_LIFT", 0),
        "cuts": kinds.get("PRIMARY_CUT", 0),
        "stops": kinds.get("PRIMARY_STOP", 0),
        "limits": limit_reasons,
        "avg_hedge_h": (sum(r.hedge_hours for r in hedged) / len(hedged)) if hedged else 0.0,
        "hedged_pnl": sum(r.net_pnl for r in hedged),
    }


VARIANTS = [("a", "ICC + normal stop", "stop", None),
            ("b", "ICC + hedge", "hedge", True),
            ("c", "ICC + hedge, no corr gate", "hedge", False)]


def _fmt_row(pair: str, key: str, label: str, m: dict) -> str:
    lim = ", ".join(f"{k}: {v}" for k, v in sorted(m["limits"].items())) or "—"
    return (f"| {pair} | ({key}) {label} | {m['setups']} | {m['win_rate']:.1f}% | "
            f"{m['net_pnl']:+,.2f} | {m['max_dd']:,.2f} | {m['fees']:,.2f} | "
            f"{m['funding_paid']:+,.2f} | {m['hedges']} | {m['skipped']} | "
            f"{m['avg_hedge_h']:.1f} | {m['lifts']} | {m['cuts']} | {lim} |")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=DATA)
    ap.add_argument("--months", type=float, default=12)
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=os.path.join(ROOT, "backtest", "hedge_results.md"))
    ap.add_argument("--events", default=os.path.join(ROOT, "backtest", "hedge_events.csv"))
    a = ap.parse_args(argv)

    rows: list[str] = []
    events: list[tuple] = []
    spans: list[str] = []
    warm = 300

    def window(bars):
        test = last_months(bars, a.months)
        if not test:
            return bars, 0
        start = bars.index(test[0])
        lo = max(0, start - warm)
        return bars[lo:], start - lo

    jobs = []
    btc_path = os.path.join(a.data_dir, "BTCUSD_1h.csv")
    if os.path.exists(btc_path):
        btc, w = window(load_csv(btc_path))
        fund = load_funding(os.path.join(a.data_dir, "BTC_PERP_funding.csv"))
        jobs.append(("BTC", "crypto", btc, None, fund, w))
        note = f"{len(fund)} funding prints" if fund else "no funding file -> default rate"
        spans.append(f"BTC: {_d(btc[w].time)} → {_d(btc[-1].time)} ({len(btc) - w} bars, {note})")
    eur_path, chf_path = (os.path.join(a.data_dir, f) for f in ("EURUSD_1h.csv", "USDCHF_1h.csv"))
    if os.path.exists(eur_path) and os.path.exists(chf_path):
        eur, chf = load_csv(eur_path), load_csv(chf_path)
        common = {c.time for c in chf}
        eur = [c for c in eur if c.time in common]
        eur, w = window(eur)
        jobs.append(("EURUSD/USDCHF", "forex", eur, chf, None, w))
        spans.append(f"FX: {_d(eur[w].time)} → {_d(eur[-1].time)} ({len(eur) - w} paired bars)")
    if not jobs:
        sys.exit(f"No data in {a.data_dir}. Run backtest/fetch_hedge_data.py first "
                 f"(or drop BTCUSD_1h.csv / EURUSD_1h.csv / USDCHF_1h.csv there).")

    for name, pair, prim, hed, fund, w in jobs:
        base = hcfg.load(pair, a.config)
        for key, label, mode, gate in VARIANTS:
            s = base if gate is None else replace(base, corr_gate_enabled=gate)
            eng, dd = run(s, mode, prim, hed, fund, warmup_bars=w)
            m = summarize(eng, dd)
            rows.append(_fmt_row(name, key, label, m))
            for e in eng.events:
                if e.kind != "FUNDING":
                    events.append((name, key, _d(e.time, True), e.setup_id, e.kind, e.reason,
                                   {k: v for k, v in e.data.items()}))

    head = ("| Pair | Variant | Setups | Win rate | Net P&L $ (after fees/funding) | Max DD $ | "
            "Fees $ | Funding paid $ | Hedges | Gate skips | Avg hedge h | Lifts | Cuts | Limit closes |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    s0 = hcfg.load("crypto", a.config)
    md = "\n".join([
        "# ICC hedge backtest",
        "",
        f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}. "
        f"Account ${s0.equity_usd:,.0f}, primary notional ${s0.primary_notional_usd:,.0f}. "
        "Rules from `bot/hedge/hedge_config.toml`.",
        "",
        *[f"- {x}" for x in spans],
        "- Entries: 4H + 1H structure alignment (5m/15m/30m not available from 1h data).",
        "- Win rate = setups (primary + hedge combined) with net P&L > 0.",
        "- Funding paid: + = paid, − = received (short perp collects positive funding).",
        "",
        head,
        *rows,
        "",
    ])
    with open(a.out, "w") as fh:
        fh.write(md)
    with open(a.events, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["pair", "variant", "time_utc", "setup", "event", "reason", "data"])
        wr.writerows(events)
    print(md)
    print(f"wrote {a.out}\nwrote {a.events} ({len(events)} events)")


def _d(t: int, full: bool = False) -> str:
    return time.strftime("%Y-%m-%d %H:%M" if full else "%Y-%m-%d", time.gmtime(t))


if __name__ == "__main__":
    main()
