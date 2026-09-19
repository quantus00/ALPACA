"""Position bookkeeping and P/L math for the dual-broker layer.

This module is deliberately broker-agnostic and free of network I/O so the P/L
and exit logic can be unit-tested with plain numbers. A :class:`Leg` is one
open position on one broker; the helpers compute each leg's *single* P/L and the
*combined* basket P/L, and decide when a configured threshold says "flatten
everything". Open legs are persisted to a small JSON file so ``flatten`` /
``status`` work across processes and restarts.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Leg:
    """One open position on one broker."""
    broker: str
    instrument: str
    symbol: str
    side: str                     # open direction: "buy" (long) or "sell" (short)
    size: float
    entry_price: float | None
    multiplier: float = 1.0
    order_id: str | None = None
    meta: dict = field(default_factory=dict)
    opened_at: float = field(default_factory=time.time)

    # -- P/L ------------------------------------------------------------------
    def cost_basis(self) -> float | None:
        """Absolute dollar cost of the position (the P/L denominator)."""
        if self.entry_price is None:
            return None
        return abs(self.entry_price * self.size * self.multiplier)

    def pnl_dollars(self, mark: float | None) -> float | None:
        """Unrealized P/L in dollars given the current ``mark`` price."""
        if self.entry_price is None or mark is None:
            return None
        sign = 1.0 if self.side == "buy" else -1.0
        return (mark - self.entry_price) * self.size * self.multiplier * sign

    def pnl_pct(self, mark: float | None) -> float | None:
        """Single-leg P/L as a percent of this leg's own cost basis."""
        pnl = self.pnl_dollars(mark)
        cost = self.cost_basis()
        if pnl is None or not cost:
            return None
        return pnl / cost * 100.0


def combined_pnl_pct(legs: list[Leg], marks: list[float | None]) -> float | None:
    """Basket P/L: total dollar P/L over total cost basis, across all priced
    legs. Legs whose mark or entry is unknown are skipped. Returns ``None`` when
    nothing can be priced."""
    total_pnl = 0.0
    total_cost = 0.0
    priced = False
    for leg, mark in zip(legs, marks):
        pnl = leg.pnl_dollars(mark)
        cost = leg.cost_basis()
        if pnl is None or cost is None:
            continue
        total_pnl += pnl
        total_cost += cost
        priced = True
    if not priced or total_cost == 0.0:
        return None
    return total_pnl / total_cost * 100.0


@dataclass
class ExitRules:
    """Flatten thresholds (percent; 0 disables the rule)."""
    tp_pct: float = 0.0        # combined take-profit
    sl_pct: float = 0.0        # combined stop-loss (magnitude, e.g. 1.5 => -1.5%)
    leg_tp_pct: float = 0.0    # per-leg take-profit
    leg_sl_pct: float = 0.0    # per-leg stop-loss (magnitude)

    def active(self) -> bool:
        return any((self.tp_pct, self.sl_pct, self.leg_tp_pct, self.leg_sl_pct))


def flatten_plan(legs: list[Leg], marks: list[float | None], rules: ExitRules,
                 mode: str = "combined") -> list[tuple[int, str]]:
    """Decide which legs to flatten, as ``[(leg_index, reason), ...]``.

    A **combined** basket rule (tp/sl) always flattens every leg. A **per-leg**
    rule flattens per ``mode``:
      * ``"combined"`` — any leg hitting its threshold flattens *all* legs;
      * ``"single"``   — flatten only the leg(s) that hit, leaving the rest open.
    An empty list means "hold".
    """
    combined = combined_pnl_pct(legs, marks)
    if combined is not None:
        if rules.tp_pct > 0 and combined >= rules.tp_pct:
            reason = f"combined P/L {combined:+.2f}% >= take-profit {rules.tp_pct:g}%"
            return [(i, reason) for i in range(len(legs))]
        if rules.sl_pct > 0 and combined <= -rules.sl_pct:
            reason = f"combined P/L {combined:+.2f}% <= stop-loss -{rules.sl_pct:g}%"
            return [(i, reason) for i in range(len(legs))]

    hits: list[tuple[int, str]] = []
    for i, (leg, mark) in enumerate(zip(legs, marks)):
        pct = leg.pnl_pct(mark)
        if pct is None:
            continue
        if rules.leg_tp_pct > 0 and pct >= rules.leg_tp_pct:
            hits.append((i, f"{leg.broker} leg P/L {pct:+.2f}% >= leg take-profit {rules.leg_tp_pct:g}%"))
        elif rules.leg_sl_pct > 0 and pct <= -rules.leg_sl_pct:
            hits.append((i, f"{leg.broker} leg P/L {pct:+.2f}% <= leg stop-loss -{rules.leg_sl_pct:g}%"))

    if not hits:
        return []
    if mode == "single":
        return hits
    # combined mode: any per-leg hit flattens the whole basket.
    reason = "; ".join(r for _, r in hits) + " -> flatten all (combined mode)"
    return [(i, reason) for i in range(len(legs))]


def should_flatten(legs: list[Leg], marks: list[float | None],
                   rules: ExitRules) -> tuple[bool, str]:
    """Back-compat helper: ``(flatten_all?, reason)`` under combined mode."""
    plan = flatten_plan(legs, marks, rules, mode="combined")
    if not plan:
        return False, ""
    return True, plan[0][1]


# -- persistence -------------------------------------------------------------
def save_state(path: str, legs: list[Leg]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump([asdict(leg) for leg in legs], fh, indent=2)
    os.replace(tmp, path)


def load_state(path: str) -> list[Leg]:
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            rows = json.load(fh)
        return [Leg(**row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read state file %s: %s", path, exc)
        return []


def clear_state(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        log.warning("Could not remove state file %s: %s", path, exc)
