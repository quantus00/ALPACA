"""
MES backtest — Open + Trend + Pullback, with scale-in exit variants.
======================================================================
Backtests the agreed rules on real MESZ6 bars (data/ JSON pulled from Webull):

  ENTRIES (identical logic feeds every exit variant, one position at a time):
    * 09:30 ET open: if flat and the 4H trend is confirmed, enter 1 contract
      with the trend (UPTREND -> long, DOWNTREND -> short). Only on timeframes
      whose bars align to 09:30 (M5/M15/M30; M60 has no 09:30 bar -> pullback
      only).
    * Trend change -> WAIT, then re-enter on the pullback: after a 4H flip, arm
      an entry and take it only once price pulls back (a lower low in a new
      uptrend / higher high in a new downtrend) and then RESUMES (closes back
      above the prior bar high / below the prior bar low).
    * Entries only during RTH (09:30-16:00 ET).

  4H TREND = market structure (HH/HL = UP, LH/LL = DOWN; both legs must break to
  flip), computed by resampling the entry timeframe to 240-minute bars and
  applying delayed pivots (lookback 5) — no look-ahead.

  EXIT VARIANTS (all tested):
    opt1_30_10   : single 1 contract, +30 / -10 pt bracket (baseline).
    opt2a_60_20  : at +$100 (+20 pt) add 7 (8 total); then +60 / -20 from the
                   blended average.
    opt2b_30_10  : same scale-in; then +30 / -10 from the blend.
    opt2c_3_1    : same scale-in; then +3 / -1 from the blend.

  FILL MODEL (conservative, no tick data):
    * Open entries fill at the 09:30 bar open; pullback entries at the
      resumption bar close.
    * Stops/targets/add-trigger are checked against each bar's high/low. When a
      bar's range spans both the adverse and the favorable level, the ADVERSE
      one is assumed first (stop-before-target, add deferred) so results are not
      optimistic.
    * Commission $0.62 per contract per side. No extra slippage modeled.

MES contract: $5.00 per point, 0.25 tick.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

POINT_VALUE = 5.0
TICK = 0.25
COMMISSION = 0.62          # per contract, per side
LOOKBACK = 5
TREND_MIN = 240           # 4H
ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- data
@dataclass
class Bar:
    start: datetime        # UTC
    o: float
    h: float
    l: float
    c: float
    v: float

    @property
    def et(self) -> datetime:
        return self.start.astimezone(ET)


def load_bars(path: str) -> list[Bar]:
    data = json.load(open(path))
    rows = data[0]["result"]
    out = []
    for r in rows:
        dt = datetime.strptime(r["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
        out.append(Bar(dt, float(r["open"]), float(r["high"]), float(r["low"]),
                       float(r["close"]), float(r.get("volume", 0) or 0)))
    out.sort(key=lambda b: b.start)
    return out


# ----------------------------------------------------------------- 4H trend engine
def resample(bars: list[Bar], minutes: int) -> list[Bar]:
    bucket = minutes * 60
    out: list[Bar] = []
    cur: Bar | None = None
    cur_key = None
    for b in bars:
        key = int(b.start.timestamp()) // bucket
        if cur is None or key != cur_key:
            if cur is not None:
                out.append(cur)
            cur = Bar(b.start, b.o, b.h, b.l, b.c, b.v)
            cur_key = key
        else:
            cur.h = max(cur.h, b.h)
            cur.l = min(cur.l, b.l)
            cur.c = b.c
            cur.v += b.v
    if cur is not None:
        out.append(cur)
    return out


def trend_states(bars4h: list[Bar], L: int = LOOKBACK) -> list[int]:
    """State known as-of each 4H bar close (pivots confirmed L bars late)."""
    n = len(bars4h)
    state = [0] * n
    lastPH = prevPH = lastPL = prevPL = None
    st = 0
    for k in range(n):
        c = k - L
        if c >= L:
            hc = bars4h[c].h
            lc = bars4h[c].l
            is_ph = all(hc > bars4h[c - j].h for j in range(1, L + 1)) and \
                    all(hc > bars4h[c + j].h for j in range(1, L + 1))
            is_pl = all(lc < bars4h[c - j].l for j in range(1, L + 1)) and \
                    all(lc < bars4h[c + j].l for j in range(1, L + 1))
            if is_ph:
                prevPH, lastPH = lastPH, hc
            if is_pl:
                prevPL, lastPL = lastPL, lc
            hh = lastPH is not None and prevPH is not None and lastPH > prevPH
            lh = lastPH is not None and prevPH is not None and lastPH < prevPH
            hl = lastPL is not None and prevPL is not None and lastPL > prevPL
            ll = lastPL is not None and prevPL is not None and lastPL < prevPL
            if hh and hl and st != 1:
                st = 1
            elif lh and ll and st != -1:
                st = -1
        state[k] = st
    return state


class Trend:
    """Maps an intraday timestamp to the last CLOSED 4H bar's trend state."""
    def __init__(self, bars: list[Bar]):
        b4 = resample(bars, TREND_MIN)
        self.close_ts = [int(b.start.timestamp()) + TREND_MIN * 60 for b in b4]
        self.state = trend_states(b4)

    def at(self, t_utc: datetime) -> int:
        ts = int(t_utc.timestamp())
        s = 0
        for i, ct in enumerate(self.close_ts):
            if ct <= ts:
                s = self.state[i]
            else:
                break
        return s


# ------------------------------------------------------------------- variants
@dataclass
class Variant:
    name: str
    tp: float
    sl: float
    scale: bool = False
    add_at: float = 20.0
    add_qty: int = 7
    init_sl: float = 10.0
    flip_exit: bool = False      # close if the 4H trend flips against the position
    eod_flat: bool = False       # close at 16:00 ET


BASE_VARIANTS = [
    Variant("opt1_30_10", tp=30, sl=10, scale=False),
    Variant("opt2a_60_20", tp=60, sl=20, scale=True),
    Variant("opt2b_30_10", tp=30, sl=10, scale=True),
    Variant("opt2c_3_1", tp=3, sl=1, scale=True),
]

# Kept for backward compatibility (old callers import VARIANTS).
VARIANTS = BASE_VARIANTS


def mgmt_variants() -> list[Variant]:
    """opt2a / opt2b under four exit-management modes."""
    out = []
    for base, tp, sl in [("opt2a", 60, 20), ("opt2b", 30, 10)]:
        out += [
            Variant(f"{base}_plain", tp, sl, scale=True),
            Variant(f"{base}_flip", tp, sl, scale=True, flip_exit=True),
            Variant(f"{base}_eod", tp, sl, scale=True, eod_flat=True),
            Variant(f"{base}_flip_eod", tp, sl, scale=True, flip_exit=True, eod_flat=True),
        ]
    return out


def sweep_variants() -> list[Variant]:
    """Add-trigger sweep for the two live brackets."""
    out = []
    for base, tp, sl in [("opt2a", 60, 20), ("opt2b", 30, 10)]:
        for add in (15, 20, 25):
            out.append(Variant(f"{base}_add{add}", tp, sl, scale=True, add_at=add))
    return out


@dataclass
class Trade:
    dir: int
    entry_dt: datetime
    exit_dt: datetime
    entry_px: float
    exit_px: float
    qty_final: int
    scaled: bool
    pnl: float          # net $ after commission
    reason: str


# ------------------------------------------------------------------- simulation
def in_rth(dt_et: datetime) -> bool:
    mins = dt_et.hour * 60 + dt_et.minute
    return 9 * 60 + 30 <= mins < 16 * 60


def is_open_bar(b: Bar) -> bool:
    e = b.et
    return e.hour == 9 and e.minute == 30


def simulate(bars: list[Bar], trend: Trend, v: Variant) -> list[Trade]:
    trades: list[Trade] = []
    pos = None
    pull_dir = 0
    pulled = False
    used_day = None

    def open_pos(direction, px, dt, same_bar):
        # pre-stage bracket
        if v.scale:
            sl = px - v.init_sl if direction > 0 else px + v.init_sl
            add_trig = px + v.add_at if direction > 0 else px - v.add_at
            return {"dir": direction, "entry": px, "avg": px, "qty": 1,
                    "stage": "pre", "sl": sl, "tp": None, "add": add_trig,
                    "dt": dt, "comm": COMMISSION, "same": same_bar, "scaled": False}
        else:
            sl = px - v.sl if direction > 0 else px + v.sl
            tp = px + v.tp if direction > 0 else px - v.tp
            return {"dir": direction, "entry": px, "avg": px, "qty": 1,
                    "stage": "single", "sl": sl, "tp": tp, "add": None,
                    "dt": dt, "comm": COMMISSION, "same": same_bar, "scaled": False}

    def close_pos(p, px, dt, reason):
        pnl_pts = (px - p["avg"]) * p["dir"] * p["qty"]
        gross = pnl_pts * POINT_VALUE
        comm = p["comm"] + COMMISSION * p["qty"]   # exit commission
        net = gross - comm
        trades.append(Trade(p["dir"], p["dt"], dt, p["entry"], px, p["qty"],
                            p["scaled"], net, reason))

    for i, b in enumerate(bars):
        st = trend.at(b.start)
        ps = trend.at(bars[i - 1].start) if i > 0 else 0

        # arm pullback on a flip
        if st == 1 and ps != 1:
            pull_dir, pulled = 1, False
        elif st == -1 and ps != -1:
            pull_dir, pulled = -1, False

        # ---- manage existing position against THIS bar ----
        if pos is not None:
            dirn = pos["dir"]
            if pos["stage"] == "pre":
                adverse = (b.l <= pos["sl"]) if dirn > 0 else (b.h >= pos["sl"])
                favor = (b.h >= pos["add"]) if dirn > 0 else (b.l <= pos["add"])
                if adverse:                      # stop first (conservative)
                    close_pos(pos, pos["sl"], b.start, "init_stop")
                    pos = None
                elif favor:                      # scale in
                    add_px = pos["add"]
                    new_qty = pos["qty"] + v.add_qty
                    pos["avg"] = (pos["entry"] * pos["qty"] + add_px * v.add_qty) / new_qty
                    pos["qty"] = new_qty
                    pos["comm"] += COMMISSION * v.add_qty
                    pos["stage"] = "post"
                    pos["scaled"] = True
                    pos["sl"] = pos["avg"] - v.sl if dirn > 0 else pos["avg"] + v.sl
                    pos["tp"] = pos["avg"] + v.tp if dirn > 0 else pos["avg"] - v.tp
                    # post-stage exits checked from next bar
            elif pos is not None and pos["stage"] in ("single", "post"):
                if pos["same"] or i > 0:
                    hit_sl = (b.l <= pos["sl"]) if dirn > 0 else (b.h >= pos["sl"])
                    hit_tp = (b.h >= pos["tp"]) if dirn > 0 else (b.l <= pos["tp"])
                    if hit_sl:                   # stop before target on ties
                        close_pos(pos, pos["sl"], b.start, "stop")
                        pos = None
                    elif hit_tp:
                        close_pos(pos, pos["tp"], b.start, "target")
                        pos = None
            if pos is not None:
                pos["same"] = True   # subsequent bars fully eligible

            # management exits, evaluated at the bar close
            if pos is not None and v.flip_exit and st == -pos["dir"]:
                close_pos(pos, b.c, b.start, "flip")
                pos = None
            if pos is not None and v.eod_flat and b.et.hour >= 16:
                close_pos(pos, b.c, b.start, "eod")
                pos = None

        # ---- new entries (only when flat) ----
        if pos is None and in_rth(b.et):
            # 09:30 open entry with the trend
            if is_open_bar(b) and used_day != b.et.date() and st != 0:
                pos = open_pos(st, b.o, b.start, same_bar=True)
                used_day = b.et.date()
                # same-bar exit check for the open-entry bar
                dirn = pos["dir"]
                if pos["stage"] == "pre":
                    adverse = (b.l <= pos["sl"]) if dirn > 0 else (b.h >= pos["sl"])
                    favor = (b.h >= pos["add"]) if dirn > 0 else (b.l <= pos["add"])
                    if adverse:
                        close_pos(pos, pos["sl"], b.start, "init_stop")
                        pos = None
                    elif favor:
                        add_px = pos["add"]
                        nq = pos["qty"] + v.add_qty
                        pos["avg"] = (pos["entry"] * pos["qty"] + add_px * v.add_qty) / nq
                        pos["qty"] = nq
                        pos["comm"] += COMMISSION * v.add_qty
                        pos["stage"] = "post"; pos["scaled"] = True
                        pos["sl"] = pos["avg"] - v.sl if dirn > 0 else pos["avg"] + v.sl
                        pos["tp"] = pos["avg"] + v.tp if dirn > 0 else pos["avg"] - v.tp
                else:
                    hit_sl = (b.l <= pos["sl"]) if dirn > 0 else (b.h >= pos["sl"])
                    hit_tp = (b.h >= pos["tp"]) if dirn > 0 else (b.l <= pos["tp"])
                    if hit_sl:
                        close_pos(pos, pos["sl"], b.start, "stop"); pos = None
                    elif hit_tp:
                        close_pos(pos, pos["tp"], b.start, "target"); pos = None

            # pullback re-entry
            elif pull_dir != 0 and st == pull_dir:
                prev = bars[i - 1]
                if pull_dir == 1:
                    if b.l < prev.l:
                        pulled = True
                    if pulled and b.c > prev.h:
                        pos = open_pos(1, b.c, b.start, same_bar=False)
                        pull_dir, pulled = 0, False
                else:
                    if b.h > prev.h:
                        pulled = True
                    if pulled and b.c < prev.l:
                        pos = open_pos(-1, b.c, b.start, same_bar=False)
                        pull_dir, pulled = 0, False

    # close any residual position at the last bar
    if pos is not None:
        close_pos(pos, bars[-1].c, bars[-1].start, "eod_mark")
    return trades


# ------------------------------------------------------------------- reporting
def stats(trades: list[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "net": 0, "win%": 0, "avg": 0, "pf": 0, "maxdd": 0,
                "scaled": 0}
    net = sum(t.pnl for t in trades)
    wins = [t for t in trades if t.pnl > 0]
    gross_w = sum(t.pnl for t in wins)
    gross_l = -sum(t.pnl for t in trades if t.pnl <= 0)
    eq = 0.0
    peak = 0.0
    maxdd = 0.0
    for t in trades:
        eq += t.pnl
        peak = max(peak, eq)
        maxdd = min(maxdd, eq - peak)
    return {
        "trades": n,
        "net": net,
        "win%": 100 * len(wins) / n,
        "avg": net / n,
        "pf": (gross_w / gross_l) if gross_l > 0 else float("inf"),
        "maxdd": maxdd,
        "scaled": sum(1 for t in trades if t.scaled),
    }


def export_trades_csv(path: str, rows: list[tuple[str, str, Trade]]) -> None:
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tf", "variant", "dir", "entry_dt_utc", "exit_dt_utc",
                    "entry_px", "exit_px", "qty_final", "scaled", "reason",
                    "net_pnl_usd"])
        for tf, vname, t in rows:
            w.writerow([tf, vname, "long" if t.dir > 0 else "short",
                        t.entry_dt.isoformat(), t.exit_dt.isoformat(),
                        f"{t.entry_px:.2f}", f"{t.exit_px:.2f}", t.qty_final,
                        int(t.scaled), t.reason, f"{t.pnl:.2f}"])


def build_continuous_m60(data_dir: str) -> tuple[str, int] | None:
    """Stitch a continuous front-month M60 series from quarterly contracts.
    Each contract is used only within its front window (up to its expiry)."""
    windows = [                       # (contract, lo_exclusive, hi_inclusive)
        ("MESH6", None, "2026-03-20"),
        ("MESM6", "2026-03-20", "2026-06-18"),
        ("MESU6", "2026-06-18", "2026-09-18"),
        ("MESZ6", "2026-09-18", None),
    ]
    rows = []
    have = 0
    for c, lo, hi in windows:
        path = os.path.join(data_dir, f"{c}_M60.json")
        if not os.path.exists(path):
            continue
        have += 1
        for r in json.load(open(path))[0]["result"]:
            day = r["time"][:10]
            if lo and day <= lo:
                continue
            if hi and day > hi:
                continue
            rows.append(r)
    if have < 2:
        return None
    rows.sort(key=lambda r: r["time"])
    out = os.path.join(data_dir, "MEScont_M60.json")
    json.dump([{"result": rows}], open(out, "w"))
    return out, len(rows)


def run_block(bars, trend, variants, title):
    """Return (text_lines, csv_rows) for one labelled block of variants."""
    header = (f"{'variant':<15} {'trades':>6} {'scaled':>6} {'win%':>6} "
              f"{'net$':>10} {'avg$':>8} {'PF':>6} {'maxDD$':>10}")
    lines = [title, header, "-" * len(header)]
    csv_rows = []
    for v in variants:
        trades = simulate(bars, trend, v)
        csv_rows += [(title.split()[0], v.name, t) for t in trades]
        s = stats(trades)
        pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        lines.append(f"{v.name:<15} {s['trades']:>6} {s['scaled']:>6} "
                     f"{s['win%']:>5.1f}% {s['net']:>10.2f} {s['avg']:>8.2f} "
                     f"{pf:>6} {s['maxdd']:>10.2f}")
    lines.append("")
    return lines, csv_rows


def main():
    import argparse
    p = argparse.ArgumentParser(description="MES open+trend+pullback backtest")
    p.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    p.add_argument("--tf", default="M5,M15,M30,M60",
                   help="recent-snapshot timeframes")
    p.add_argument("--csv", default="", help="write a per-trade CSV to this path")
    p.add_argument("--out", default=os.path.join(HERE, "results.txt"))
    args = p.parse_args()

    all_files = {"M5": "MESZ6_M5.json", "M15": "MESZ6_M15.json",
                 "M30": "MESZ6_M30.json", "M60": "MESZ6_M60.json"}
    out_lines: list[str] = []
    csv_rows = []

    # ---------- Report 1: recent snapshot, base variants -----------------------
    master = load_bars(os.path.join(args.data_dir, all_files["M60"]))
    trend_recent = Trend(master)
    out_lines.append("=" * 72)
    out_lines.append("REPORT 1 — recent snapshot (single MESZ6 contract), base variants")
    out_lines.append(f"master 4H trend from M60: {master[0].et:%Y-%m-%d} -> {master[-1].et:%Y-%m-%d}")
    out_lines.append("=" * 72)
    for tf in [t.strip() for t in args.tf.split(",") if t.strip()]:
        fn = all_files.get(tf)
        if not fn:
            continue
        bars = load_bars(os.path.join(args.data_dir, fn))
        span = f"{bars[0].et:%Y-%m-%d} -> {bars[-1].et:%Y-%m-%d} ({len(bars)} bars)"
        blk, rows = run_block(bars, trend_recent, BASE_VARIANTS, f"{tf}  [{span}]")
        out_lines += blk
        csv_rows += rows

    # ---------- Report 2: extended continuous M60 series -----------------------
    cont = build_continuous_m60(args.data_dir)
    if cont:
        cont_path, ncont = cont
        cbars = load_bars(cont_path)
        ctrend = Trend(cbars)
        span = f"{cbars[0].et:%Y-%m-%d} -> {cbars[-1].et:%Y-%m-%d} ({ncont} bars)"
        out_lines.append("=" * 72)
        out_lines.append("REPORT 2 — extended continuous front-month M60 "
                         "(MESH6+MESM6+MESU6+MESZ6)")
        out_lines.append(f"span: {span}  (M60 -> pullback entries only; ~3wk roll gaps)")
        out_lines.append("=" * 72)
        for label, vs in [("CONT60 base variants", BASE_VARIANTS),
                          ("CONT60 exit-management (opt2a/opt2b x plain/flip/eod/both)", mgmt_variants()),
                          ("CONT60 add-trigger sweep (15/20/25 pt)", sweep_variants())]:
            blk, rows = run_block(cbars, ctrend, vs, label)
            out_lines += blk
            csv_rows += rows

    # ---------- Report 3: the chosen BEST hybrid strategy ----------------------
    best = [
        Variant("BEST_core_1ct_30_10", 30, 10, scale=False),
        Variant("BEST_scale_add7@15_60_20_flip", 60, 20, scale=True,
                add_at=15, add_qty=7, flip_exit=True),
        Variant("BEST_scale_add3@15_60_20_flip", 60, 20, scale=True,
                add_at=15, add_qty=3, flip_exit=True),
    ]
    out_lines += ["=" * 72,
                  "REPORT 3 — BEST STRATEGY (hybrid): CORE default + SCALE toggle",
                  "=" * 72]
    r3 = {"M15": ("MESZ6_M15.json", "MESZ6_M60.json"),
          "M30": ("MESZ6_M30.json", "MESZ6_M60.json")}
    if cont:
        r3["CONT60"] = ("MEScont_M60.json", "MEScont_M60.json")
    for ds, (ef, tf) in r3.items():
        bars = load_bars(os.path.join(args.data_dir, ef))
        trd = Trend(load_bars(os.path.join(args.data_dir, tf)))
        span = f"{bars[0].et:%Y-%m-%d} -> {bars[-1].et:%Y-%m-%d} ({len(bars)} bars)"
        blk, rows = run_block(bars, trd, best, f"{ds}  [{span}]")
        out_lines += blk
        csv_rows += rows

    text = "\n".join(out_lines) + "\n"
    print(text, end="")
    with open(args.out, "w") as f:
        f.write(text)
    if args.csv:
        export_trades_csv(args.csv, csv_rows)
        print(f"# per-trade CSV -> {args.csv}  ({len(csv_rows)} trades)")


if __name__ == "__main__":
    main()
