"""Pair scanner — screen the whole forex universe for Alex setups at once.

On open, runs the strategy across every pair and ranks what it finds:
  SETUP  — a live buy/sell entry right now (trend + AOI + rejection/engulfing)
  WATCH  — price is at a valid area of interest, waiting for the entry candle
  TREND  — trending but no valid AOI yet
  -      — no confirmed trend
  ERR    — data/eval error for that pair

Keyless (Yahoo/Stooq data) and concurrent, so a full sweep is fast.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from alex_bot.strategy import Params, evaluate

log = logging.getLogger("alexfx.scan")

MAJORS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD",
          "NZD/USD"]
CROSSES = ["EUR/GBP", "EUR/JPY", "GBP/JPY", "EUR/CHF", "AUD/JPY", "EUR/AUD",
           "GBP/CHF", "CAD/JPY", "NZD/JPY", "AUD/NZD", "EUR/CAD", "GBP/CAD",
           "AUD/CAD", "CHF/JPY", "EUR/NZD", "GBP/AUD"]
DEFAULT_UNIVERSE = MAJORS + CROSSES

_RANK = {"SETUP": 0, "WATCH": 1, "TREND": 2, "-": 3, "ERR": 4}


@dataclass
class ScanRow:
    pair: str
    status: str                      # SETUP | WATCH | TREND | - | ERR
    trend: str = "-"
    action: str = "none"
    pattern: str | None = None
    price: float | None = None
    entry: float | None = None
    stop: float | None = None
    take_profit: float | None = None
    rr: float | None = None
    detail: str = ""

    @property
    def sort_key(self):
        return (_RANK.get(self.status, 9), self.pair)


def scan_pair(pair: str, fetch, params: Params, entry_tf: str,
              structure_tf: str) -> ScanRow:
    """Evaluate one pair. `fetch(pair, tf)` returns candles (keyless)."""
    try:
        structure = fetch(pair, structure_tf)
        entry = fetch(pair, entry_tf)
        if len(structure) < 30 or len(entry) < 5:
            return ScanRow(pair, "ERR", detail="not enough candles")
        sig = evaluate(structure, entry, params)
    except Exception as exc:  # noqa: BLE001
        return ScanRow(pair, "ERR", detail=str(exc)[:80])

    trend = sig.trend.label()
    if sig.action in ("buy", "sell"):
        rr = abs(sig.take_profit - sig.entry) / max(abs(sig.entry - sig.stop), 1e-12)
        return ScanRow(pair, "SETUP", trend, sig.action, sig.pattern, sig.price,
                       sig.entry, sig.stop, sig.take_profit, rr, sig.reason)
    if sig.zone is not None:                    # at a zone, waiting for the candle
        return ScanRow(pair, "WATCH", trend, "none", None, sig.price,
                       detail=sig.reason)
    if trend != "NEUTRAL":
        return ScanRow(pair, "TREND", trend, "none", None, sig.price,
                       detail=sig.reason)
    return ScanRow(pair, "-", trend, "none", None, sig.price, detail=sig.reason)


def scan(fetch, params: Params, entry_tf: str = "15m", structure_tf: str = "1h",
         pairs: list[str] | None = None, workers: int = 8) -> list[ScanRow]:
    pairs = pairs or DEFAULT_UNIVERSE

    def _one(pair: str) -> ScanRow:
        return scan_pair(pair, fetch, params, entry_tf, structure_tf)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        rows = list(pool.map(_one, pairs))
    rows.sort(key=lambda r: r.sort_key)
    return rows


def format_table(rows: list[ScanRow], show: str = "all") -> str:
    """`show`: 'setups' | 'actionable' (setups+watch) | 'all'."""
    keep = {"all": set(_RANK),
            "setups": {"SETUP"},
            "actionable": {"SETUP", "WATCH"}}.get(show, set(_RANK))
    lines = [f"{'PAIR':<9} {'STATUS':<6} {'TREND':<9} {'SIGNAL':<6} "
             f"{'PATTERN':<9} {'ENTRY':>9} {'STOP':>9} {'TP':>9} {'R:R':>4}"]
    n_setup = 0
    for r in rows:
        if r.status not in keep:
            continue
        if r.status == "SETUP":
            n_setup += 1
        e = f"{r.entry:.5f}" if r.entry else ""
        s = f"{r.stop:.5f}" if r.stop else ""
        tp = f"{r.take_profit:.5f}" if r.take_profit else ""
        rr = f"{r.rr:.1f}" if r.rr else ""
        sig = r.action.upper() if r.action != "none" else ""
        lines.append(f"{r.pair:<9} {r.status:<6} {r.trend:<9} {sig:<6} "
                     f"{(r.pattern or ''):<9} {e:>9} {s:>9} {tp:>9} {rr:>4}")
    lines.append(f"\n{n_setup} live setup(s) of {len(rows)} pairs scanned.")
    return "\n".join(lines)
