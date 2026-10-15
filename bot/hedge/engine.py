"""Hedge rule engine — one primary ICC trade + at most one hedge.

Driven one CLOSED 1h bar at a time (``on_bar``). It does not decide entries:
the existing ICC logic does, and calls ``open_primary``. Fills are simulated at
the bar close (trailing-stop exits fill at the stop, or the open if gapped).

Phases of a setup::

    PRIMARY ──trigger──► HEDGED ──lift──► PRIMARY (hedge_used=True; trigger again = normal stop)
       │                   │
       │                   ├─cut (4h break)─► HEDGE_ONLY ──trail stop / limit──► closed
       │                   └─limit (loss / duration / funding)──► closed
       ├─4h break against──► closed   (existing ICC exit)
       └─trigger, no hedge allowed (mode=stop, corr gate fail, hedge already used) ─► closed

Modes: ``"stop"`` = ICC with the normal stop (the trigger is the stop),
``"hedge"`` = ICC + hedge. The forex correlation gate follows
``PairSettings.corr_gate_enabled``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import PairSettings
from .features import BarContext

PIP = 0.0001


@dataclass
class Leg:
    symbol: str
    side: int              # +1 long / -1 short
    units: float           # crypto: BTC; EURUSD: EUR; USDCHF: USD
    entry: float
    quote_is_usd: bool     # False for USD/CHF (P&L arrives in CHF)
    open_time: int

    def pnl(self, price: float) -> float:
        raw = self.side * self.units * (price - self.entry)
        return raw if self.quote_is_usd else raw / price

    def notional(self, price: float) -> float:
        return self.units * price if self.quote_is_usd else self.units


@dataclass
class Event:
    time: int
    setup_id: int
    kind: str              # PRIMARY_OPEN, HEDGE_OPEN, HEDGE_SKIPPED, HEDGE_LIFT, PRIMARY_CUT,
                           # HEDGE_TRAIL_EXIT, LIMIT_CLOSE, PRIMARY_STOP, PRIMARY_EXIT,
                           # FUNDING, SETUP_CLOSED
    reason: str
    data: dict = field(default_factory=dict)


@dataclass
class Fill:
    """One simulated leg fill. The live executor mirrors these as real orders."""
    time: int
    setup_id: int
    leg: str               # "primary" | "hedge"
    symbol: str
    action: str            # "buy" | "sell"
    units: float
    price: float
    opening: bool          # True = opens the leg, False = closes it


@dataclass
class SetupResult:
    setup_id: int
    side: int
    open_time: int
    close_time: int
    net_pnl: float
    fees: float
    funding_paid: float
    hedged: bool
    hedge_hours: float
    outcome: str           # last closing reason


class HedgeEngine:
    def __init__(self, s: PairSettings, mode: str = "hedge") -> None:
        if mode not in ("stop", "hedge"):
            raise ValueError("mode must be 'stop' or 'hedge'")
        self.s = s
        self.mode = mode
        self._next_id = 1
        self.events: list[Event] = []
        self.fills: list[Fill] = []
        self.results: list[SetupResult] = []
        self._reset()

    # ------------------------------------------------------------------ state
    def _reset(self) -> None:
        self.setup_id: int | None = None
        self.primary: Leg | None = None
        self.hedge: Leg | None = None
        self.phase = "flat"            # flat | primary | hedged | hedge_only
        self.hedge_used = False
        self.atr_entry: float | None = None
        self.correction: float | None = None
        self.trigger_level: float | None = None
        self.trail_stop: float | None = None
        self.hedge_open_time: int | None = None
        self.hedge_bars = 0
        self.hedge_hours_total = 0.0
        self.realized = 0.0
        self.fees = 0.0
        self.funding_paid = 0.0
        self.setup_open_time = 0
        self._side = 0

    @property
    def is_flat(self) -> bool:
        return self.phase == "flat"

    # ------------------------------------------------------------------ costs
    def _cost(self, leg_is_primary: bool, units: float, price: float, quote_is_usd: bool) -> float:
        s = self.s
        if s.is_crypto:
            pct = s.primary_fee_pct if leg_is_primary else s.hedge_fee_pct
            return units * price * pct / 100.0
        pips = s.primary_cost_pips if leg_is_primary else s.hedge_cost_pips
        cost_quote = units * PIP * pips
        return cost_quote if quote_is_usd else cost_quote / price

    def _emit(self, ctx_time: int, kind: str, reason: str, **data) -> Event:
        ev = Event(ctx_time, self.setup_id or 0, kind, reason, data)
        self.events.append(ev)
        return ev

    # ------------------------------------------------------------------ entry
    def open_primary(self, ctx: BarContext, side: int, units: float | None = None) -> list[Event]:
        """Called by the ICC logic when it enters. One setup at a time."""
        if not self.is_flat:
            raise RuntimeError("a setup is already open — never stack")
        if self.s.is_crypto and side != 1:
            raise ValueError("crypto primary is BTC spot LONG only")
        if ctx.atr is None:
            raise ValueError("ATR not warmed up yet")
        start = len(self.events)
        self.setup_id = self._next_id
        self._next_id += 1
        units = units if units is not None else self.s.primary_notional_usd / ctx.close
        self.primary = Leg(self.s.primary_symbol, side, units, ctx.close, True, ctx.close_time)
        self._fill("primary", self.primary, ctx.close_time, ctx.close, opening=True)
        self.fees += self._cost(True, units, ctx.close, True)
        self.phase = "primary"
        self._side = side
        self.setup_open_time = ctx.close_time
        self.atr_entry = ctx.atr
        # ICC correction low (long) / high (short) = last confirmed 1h swing on
        # the wrong side of entry.
        corr = ctx.swing_low_1h if side == 1 else ctx.swing_high_1h
        if corr is not None and (corr - ctx.close) * side < 0:
            self.correction = corr
        self._emit(ctx.close_time, "PRIMARY_OPEN", "ICC entry", side=side, price=ctx.close,
                   units=units, atr=ctx.atr, correction_level=self.correction,
                   atr_trigger=self._atr_level(ctx))
        return self.events[start:]

    # ------------------------------------------------------------------ helpers
    def _atr_level(self, ctx: BarContext) -> float:
        atr = self.atr_entry if self.s.atr_reference == "entry" else (ctx.atr or self.atr_entry)
        return self.primary.entry - self.primary.side * self.s.atr_mult * atr

    def _triggered(self, ctx: BarContext) -> tuple[str, float] | None:
        """(reason, level) if the hedge trigger fired on this close."""
        side = self.primary.side
        hits: list[tuple[str, float]] = []
        lvl = self._atr_level(ctx)
        if (ctx.close - lvl) * side <= 0:
            hits.append((f"moved {self.s.atr_mult:g}x ATR(1h) against", lvl))
        if self.s.use_correction_break and self.correction is not None:
            if (ctx.close - self.correction) * side < 0:
                hits.append(("closed beyond ICC correction " + ("low" if side == 1 else "high"),
                             self.correction))
        if not hits:
            return None
        # Reclaim level = the level nearest the entry (the stricter one to win back).
        reason = " + ".join(h[0] for h in hits)
        level = max(h[1] for h in hits) if side == 1 else min(h[1] for h in hits)
        return reason, level

    def _setup_pnl(self, ctx: BarContext) -> float:
        p = self.realized - self.fees - self.funding_paid
        if self.primary:
            p += self.primary.pnl(ctx.close)
        if self.hedge:
            p += self.hedge.pnl(ctx.h_close)
        return p

    def mark_to_market(self, ctx: BarContext) -> float:
        return 0.0 if self.is_flat else self._setup_pnl(ctx)

    def _fill(self, leg_name: str, leg: Leg, t: int, price: float, opening: bool) -> None:
        buy = (leg.side == 1) == opening
        self.fills.append(Fill(t, self.setup_id or 0, leg_name, leg.symbol,
                               "buy" if buy else "sell", leg.units, price, opening))

    def _close_primary(self, t: int, price: float) -> float:
        leg = self.primary
        self._fill("primary", leg, t, price, opening=False)
        pnl = leg.pnl(price)
        self.realized += pnl
        self.fees += self._cost(True, leg.units, price, True)
        self.primary = None
        return pnl

    def _close_hedge(self, t: int, price: float) -> float:
        leg = self.hedge
        self._fill("hedge", leg, t, price, opening=False)
        pnl = leg.pnl(price)
        self.realized += pnl
        self.fees += self._cost(False, leg.units, price, leg.quote_is_usd)
        self.hedge = None
        self.hedge_hours_total += (t - self.hedge_open_time) / 3600.0
        return pnl

    def _finish(self, t: int, outcome: str) -> None:
        net = self.realized - self.fees - self.funding_paid
        self.results.append(SetupResult(
            setup_id=self.setup_id, side=self._side, open_time=self.setup_open_time,
            close_time=t, net_pnl=net, fees=self.fees, funding_paid=self.funding_paid,
            hedged=self.hedge_used, hedge_hours=self.hedge_hours_total, outcome=outcome))
        self._emit(t, "SETUP_CLOSED", outcome, net_pnl=round(net, 2), fees=round(self.fees, 2),
                   funding_paid=round(self.funding_paid, 4), hedged=self.hedge_used,
                   hedge_hours=round(self.hedge_hours_total, 1))
        self._reset()

    def _open_hedge(self, ctx: BarContext, reason: str, level: float) -> None:
        s, p = self.s, self.primary
        if s.is_crypto:
            # 1:1 USD notional at the current price, opposite side (short perp).
            usd = s.hedge_ratio * p.notional(ctx.close)
            self.hedge = Leg(s.hedge_symbol, -p.side, usd / ctx.h_close, ctx.h_close, True,
                             ctx.close_time)
        else:
            # Same direction USD/CHF; its USD units offset EUR/USD's USD exposure.
            usd = s.hedge_ratio * p.notional(ctx.close)
            self.hedge = Leg(s.hedge_symbol, p.side, usd, ctx.h_close, False, ctx.close_time)
        self._fill("hedge", self.hedge, ctx.close_time, ctx.h_close, opening=True)
        self.fees += self._cost(False, self.hedge.units, ctx.h_close, self.hedge.quote_is_usd)
        self.phase = "hedged"
        self.hedge_used = True
        self.trigger_level = level
        self.hedge_open_time = ctx.close_time
        self.hedge_bars = 0
        self._emit(ctx.close_time, "HEDGE_OPEN", reason, trigger_level=level,
                   primary_price=ctx.close, hedge_symbol=self.hedge.symbol,
                   hedge_side=self.hedge.side, hedge_units=self.hedge.units,
                   hedge_price=ctx.h_close, hedge_usd_notional=round(usd, 2), corr=ctx.corr,
                   setup_pnl=round(self._setup_pnl(ctx), 2))

    def _close_both(self, ctx: BarContext, kind: str, reason: str) -> None:
        if self.primary:
            self._close_primary(ctx.close_time, ctx.close)
        if self.hedge:
            self._close_hedge(ctx.close_time, ctx.h_close)
        self._emit(ctx.close_time, kind, reason, primary_price=ctx.close, hedge_price=ctx.h_close)
        self._finish(ctx.close_time, reason)

    # ------------------------------------------------------------------ per bar
    def on_bar(self, ctx: BarContext) -> list[Event]:
        start = len(self.events)
        if self.is_flat:
            return []
        if self.phase == "primary":
            self._bar_primary(ctx)
        elif self.phase == "hedged":
            self._bar_hedged(ctx)
        elif self.phase == "hedge_only":
            self._bar_hedge_only(ctx)
        return self.events[start:]

    def _bar_primary(self, ctx: BarContext) -> None:
        side = self.primary.side
        if ctx.trend_4h == -side:
            pnl = self._close_primary(ctx.close_time, ctx.close)
            self._emit(ctx.close_time, "PRIMARY_EXIT", "4h structure broke against primary",
                       price=ctx.close, pnl=round(pnl, 2))
            self._finish(ctx.close_time, "4h structure exit")
            return
        hit = self._triggered(ctx)
        if not hit:
            return
        reason, level = hit
        allow, why_not = self._hedge_allowed(ctx)
        if allow:
            self._open_hedge(ctx, reason, level)
            return
        if why_not.startswith("correlation"):
            self._emit(ctx.close_time, "HEDGE_SKIPPED", why_not, corr=ctx.corr,
                       threshold=self.s.corr_threshold)
        pnl = self._close_primary(ctx.close_time, ctx.close)
        self._emit(ctx.close_time, "PRIMARY_STOP", f"normal stop: {reason} ({why_not})",
                   price=ctx.close, pnl=round(pnl, 2))
        self._finish(ctx.close_time, "normal stop")

    def _hedge_allowed(self, ctx: BarContext) -> tuple[bool, str]:
        if self.mode == "stop":
            return False, "hedging off"
        if self.hedge_used:
            return False, "one hedge per primary already used"
        if not self.s.is_crypto and self.s.corr_gate_enabled:
            if ctx.corr is None:
                return False, "correlation unavailable (not enough data)"
            if not ctx.corr < self.s.corr_threshold:
                return False, (f"correlation gate: corr {ctx.corr:.3f} not below "
                               f"{self.s.corr_threshold:g}")
        return True, ""

    def _accrue_funding(self, ctx: BarContext) -> None:
        if not (self.s.is_crypto and self.hedge):
            return
        interval = self.s.funding_interval_hours * 3600
        if ctx.close_time % interval != 0:
            return
        rate = ctx.funding_rate_pct
        if rate is None:
            rate = self.s.default_funding_rate_pct
        # Positive rate: longs pay shorts. paid > 0 means the hedge PAID funding.
        paid = self.hedge.side * rate / 100.0 * self.hedge.notional(ctx.h_close)
        self.funding_paid += paid
        self._emit(ctx.close_time, "FUNDING", "perp funding settlement", rate_pct=rate,
                   paid=round(paid, 4), cumulative_paid=round(self.funding_paid, 4))

    def _check_limits(self, ctx: BarContext) -> bool:
        s = self.s
        pnl = self._setup_pnl(ctx)
        if pnl <= -s.max_combined_loss_pct / 100.0 * s.equity_usd:
            self._close_both(ctx, "LIMIT_CLOSE",
                             f"max combined loss {s.max_combined_loss_pct:g}% hit (setup P&L {pnl:.2f})")
            return True
        if s.is_crypto:
            hours = (ctx.close_time - self.hedge_open_time) / 3600.0
            over = hours >= s.max_hedge_bars
            span = f"{hours:.0f}h"
        else:
            over = self.hedge_bars >= s.max_hedge_bars
            span = f"{self.hedge_bars / 24:.1f} trading days"
        if over:
            self._close_both(ctx, "LIMIT_CLOSE", f"max hedge duration hit ({span})")
            return True
        if s.is_crypto and self.hedge:
            cap = s.max_funding_pct / 100.0 * self.hedge.notional(ctx.h_close)
            if self.funding_paid > cap:
                self._close_both(ctx, "LIMIT_CLOSE",
                                 f"funding paid {self.funding_paid:.2f} > {s.max_funding_pct:g}% of notional")
                return True
        return False

    def _continuation(self, ctx: BarContext) -> bool:
        side = self.primary.side
        if self.s.require_continuation_1h_trend and ctx.trend_1h != side:
            return False
        swing = ctx.swing_high_1h if side == 1 else ctx.swing_low_1h
        return swing is not None and (ctx.close - swing) * side > 0

    def _bar_hedged(self, ctx: BarContext) -> None:
        self.hedge_bars += 1
        self._accrue_funding(ctx)
        if self._check_limits(ctx):
            return
        side = self.primary.side
        if ctx.trend_4h == -side:
            pnl = self._close_primary(ctx.close_time, ctx.close)
            mult = self.s.trail_atr_mult * (ctx.atr or self.atr_entry)
            self.trail_stop = ctx.h_close - self.hedge.side * mult
            self.phase = "hedge_only"
            self._emit(ctx.close_time, "PRIMARY_CUT", "4h structure broke against primary",
                       price=ctx.close, pnl=round(pnl, 2), hedge_trail_stop=self.trail_stop)
            return
        cont = (not self.s.require_continuation) or self._continuation(ctx)
        reclaim = (not self.s.require_reclaim) or (ctx.close - self.trigger_level) * side > 0
        if cont and reclaim:
            pnl = self._close_hedge(ctx.close_time, ctx.h_close)
            self.phase = "primary"
            self._emit(ctx.close_time, "HEDGE_LIFT",
                       "1h ICC continuation + reclaimed trigger level",
                       price=ctx.close, trigger_level=self.trigger_level,
                       hedge_price=ctx.h_close, hedge_pnl=round(pnl, 2),
                       hours=round((ctx.close_time - self.hedge_open_time) / 3600, 1))

    def _bar_hedge_only(self, ctx: BarContext) -> None:
        self.hedge_bars += 1
        h = self.hedge
        # Stop from the previous bar, checked intrabar on this bar.
        hit = (ctx.h_high >= self.trail_stop) if h.side == -1 else (ctx.h_low <= self.trail_stop)
        if hit:
            fill = max(ctx.h_open, self.trail_stop) if h.side == -1 else min(ctx.h_open, self.trail_stop)
            pnl = self._close_hedge(ctx.close_time, fill)
            self._emit(ctx.close_time, "HEDGE_TRAIL_EXIT",
                       f"hedge trailing stop ({self.s.trail_atr_mult:g}x ATR) hit",
                       price=fill, pnl=round(pnl, 2))
            self._finish(ctx.close_time, "hedge trailed out after cut")
            return
        self._accrue_funding(ctx)
        if self._check_limits(ctx):
            return
        dist = self.s.trail_atr_mult * (ctx.atr or self.atr_entry)
        new = ctx.h_close - h.side * dist
        self.trail_stop = min(self.trail_stop, new) if h.side == -1 else max(self.trail_stop, new)

    def force_close(self, ctx: BarContext, reason: str = "end of data") -> None:
        if not self.is_flat:
            self._close_both(ctx, "LIMIT_CLOSE", reason)
