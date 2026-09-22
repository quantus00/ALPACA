"""Dual-broker execution: open a position on several brokers at once, watch the
single and combined P/L, and flatten every leg together when a threshold trips.

Each leg is chosen at run time as ``broker:instrument:size`` (e.g.
``coinbase:btc_usd_spot:0.01`` and ``webull:spy_options:1``), so any broker keeps
trading its own instrument — this fires and flattens them together, it does not
make one broker trade the other's asset.
"""
from __future__ import annotations

import dataclasses
import logging
from concurrent.futures import ThreadPoolExecutor

from .brokers import get_broker
from .brokers.base import BrokerBase
from .config import (INSTRUMENT_BROKERS, INSTRUMENT_MULTIPLIER, Broker, Config,
                     Instrument)
from .portfolio import (ExitRules, Leg, clear_state, combined_pnl_pct,
                        flatten_plan, load_state, save_state)

log = logging.getLogger(__name__)


@dataclasses.dataclass
class LegSpec:
    broker: Broker
    instrument: Instrument
    size: float

    @classmethod
    def parse(cls, text: str) -> "LegSpec":
        parts = [p.strip() for p in text.split(":")]
        if len(parts) != 3:
            raise ValueError(
                f"leg spec {text!r} must be 'broker:instrument:size' "
                "(e.g. coinbase:btc_usd_spot:0.01)")
        broker, instrument, size = parts
        return cls(Broker(broker), Instrument(instrument), float(size))


def parse_legs(specs: list[str] | str) -> list[LegSpec]:
    """Accept a list of specs or a single comma-separated string."""
    if isinstance(specs, str):
        specs = [s for s in specs.split(",") if s.strip()]
    return [LegSpec.parse(s) for s in specs]


def _cfg_for(base: Config, spec: LegSpec) -> Config:
    """Clone the base config, pinned to one broker/instrument/size."""
    return dataclasses.replace(
        base, broker=spec.broker, instrument=spec.instrument,
        contract_size=spec.size)


def _broker_for(base: Config, broker: Broker, instrument: Instrument,
                size: float = 1.0) -> BrokerBase:
    return get_broker(_cfg_for(base, LegSpec(broker, instrument, size)))


def rules_from(cfg: Config) -> ExitRules:
    return ExitRules(tp_pct=cfg.tp_pct, sl_pct=cfg.sl_pct,
                     leg_tp_pct=cfg.leg_tp_pct, leg_sl_pct=cfg.leg_sl_pct)


# -- open --------------------------------------------------------------------
def open_all(base: Config, specs: list[LegSpec], side: str) -> list[Leg]:
    """Place ``side`` orders on every leg concurrently and record open legs."""

    def _open(spec: LegSpec) -> Leg | None:
        broker = _broker_for(base, spec.broker, spec.instrument, spec.size)
        res = broker.place_order(side, spec.size)
        if not res.ok:
            log.error("%s open failed: %s", spec.broker.value, res.error)
            return None
        leg = Leg(
            broker=spec.broker.value,
            instrument=spec.instrument.value,
            symbol=res.symbol,
            side=side,
            size=spec.size,
            entry_price=res.fill_price,
            multiplier=INSTRUMENT_MULTIPLIER.get(spec.instrument, 1.0),
            order_id=res.order_id,
            meta=res.meta or {},
        )
        log.info("Opened %s %s %g %s @ %s (order %s)", spec.broker.value, side,
                 spec.size, res.symbol, leg.entry_price, res.order_id)
        return leg

    with ThreadPoolExecutor(max_workers=max(1, len(specs))) as pool:
        legs = [leg for leg in pool.map(_open, specs) if leg is not None]

    if legs:
        save_state(base.state_file, legs)
    return legs


# -- marks / reporting -------------------------------------------------------
def marks_for(base: Config, legs: list[Leg]) -> list[float | None]:
    """Fetch the current mark for each leg concurrently."""

    def _mark(leg: Leg) -> float | None:
        broker = _broker_for(base, Broker(leg.broker), Instrument(leg.instrument),
                             leg.size)
        return broker.mark_price(leg.symbol, leg.meta)

    with ThreadPoolExecutor(max_workers=max(1, len(legs))) as pool:
        return list(pool.map(_mark, legs))


def report(legs: list[Leg], marks: list[float | None]) -> str:
    lines = []
    for leg, mark in zip(legs, marks):
        pct = leg.pnl_pct(mark)
        pct_s = f"{pct:+.2f}%" if pct is not None else "n/a"
        mark_s = f"{mark:g}" if mark is not None else "n/a"
        lines.append(
            f"  {leg.broker:<9} {leg.symbol:<14} entry={leg.entry_price} "
            f"mark={mark_s} single P/L={pct_s}")
    combined = combined_pnl_pct(legs, marks)
    combined_s = f"{combined:+.2f}%" if combined is not None else "n/a"
    lines.append(f"  COMBINED P/L = {combined_s}")
    return "\n".join(lines)


# -- flatten -----------------------------------------------------------------
def _flatten_legs(base: Config, legs: list[Leg]) -> None:
    """Send the offsetting order for each given leg, concurrently."""

    def _flat(leg: Leg):
        broker = _broker_for(base, Broker(leg.broker), Instrument(leg.instrument),
                             leg.size)
        res = broker.flatten(side=leg.side, size=leg.size, symbol=leg.symbol,
                             meta=leg.meta)
        if res.ok:
            log.info("Flattened %s %s", leg.broker, leg.symbol)
        else:
            log.error("Flatten %s failed: %s", leg.broker, res.error)
        return res

    if not legs:
        return
    with ThreadPoolExecutor(max_workers=max(1, len(legs))) as pool:
        list(pool.map(_flat, legs))


def flatten_all(base: Config, legs: list[Leg]) -> None:
    """Flatten every open leg, then clear saved state."""
    _flatten_legs(base, legs)
    clear_state(base.state_file)


# -- monitor -----------------------------------------------------------------
def monitor(base: Config, legs: list[Leg], rules: ExitRules,
            mode: str = "combined") -> None:
    """Poll marks; log single + combined P/L; flatten legs when a rule trips.

    ``mode`` ("combined" | "single") controls whether a tripped per-leg rule
    closes every leg or only the leg that hit. In single mode the surviving legs
    keep being monitored until they too exit."""
    import time

    if not rules.active():
        log.info("No P/L exit rules set (BOT_TP_PCT / BOT_SL_PCT / BOT_LEG_*); "
                 "positions left open. Use `flatten` to close.")
        return
    log.info("Monitoring %d leg(s) every %ss; mode=%s rules: tp=%g sl=%g "
             "leg_tp=%g leg_sl=%g", len(legs), base.poll_seconds, mode,
             rules.tp_pct, rules.sl_pct, rules.leg_tp_pct, rules.leg_sl_pct)
    open_legs = list(legs)
    while open_legs:
        try:
            marks = marks_for(base, open_legs)
            log.info("P/L:\n%s", report(open_legs, marks))
            plan = flatten_plan(open_legs, marks, rules, mode)
            if plan:
                idxs = sorted({i for i, _ in plan})
                for _, reason in plan:
                    log.info("EXIT TRIGGERED: %s", reason)
                to_close = [open_legs[i] for i in idxs]
                _flatten_legs(base, to_close)
                open_legs = [leg for j, leg in enumerate(open_legs) if j not in idxs]
                if open_legs:
                    save_state(base.state_file, open_legs)
                    log.info("%d leg(s) still open (single mode).", len(open_legs))
                else:
                    clear_state(base.state_file)
                    return
        except Exception:  # noqa: BLE001
            log.exception("monitor tick failed")
        time.sleep(base.poll_seconds)


# -- balances ----------------------------------------------------------------
def _any_instrument_for(broker: Broker) -> Instrument:
    for instrument, brokers in INSTRUMENT_BROKERS.items():
        if broker in brokers:
            return instrument
    raise ValueError(f"no instrument configured for broker {broker.value}")


def balances(base: Config, brokers: list[Broker]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for broker in brokers:
        instrument = _any_instrument_for(broker)
        impl = _broker_for(base, broker, instrument)
        out[broker.value] = impl.get_balances()
    return out
