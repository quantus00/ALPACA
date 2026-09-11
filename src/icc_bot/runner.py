"""Orchestration: wire config + broker + strategy + risk into a run loop.

`run_cycle` does one pass (fetch -> evaluate -> gate -> size -> order) and is
pure enough to unit-test with a fake broker. `run_forever` polls it.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Optional

from .brokers.base import Broker, DryRunBroker
from .config import BotConfig, load_config
from .models import Mode, Order, Side, Signal
from .risk import RiskManager
from .strategy import evaluate

log = logging.getLogger("icc_bot.runner")


def build_broker(cfg: BotConfig) -> Broker:
    """Construct the venue adapter, wrapping it in DryRunBroker unless truly live/paper."""
    want_live = cfg.mode is Mode.LIVE
    real: Optional[Broker] = None

    if cfg.broker == "coinbase":
        if cfg.venue in ("futures", "perp"):
            from .brokers.coinbase import CoinbaseDerivativesBroker
            real = CoinbaseDerivativesBroker(
                venue=cfg.venue, live=want_live, portfolio_uuid=cfg.portfolio_uuid or None,
                leverage=cfg.leverage or None, margin_type=cfg.margin_type)
        else:
            from .brokers.coinbase import CoinbaseBroker
            real = CoinbaseBroker(live=want_live)
    elif cfg.broker == "webull":
        from .brokers.webull import WebullBroker
        real = WebullBroker(live=want_live)
    else:
        raise ValueError(f"unknown broker {cfg.broker!r}")

    if cfg.mode is Mode.DRY_RUN:
        return DryRunBroker(data_source=real)
    if cfg.mode is Mode.LIVE and not cfg.live_confirmed:
        log.error("LIVE mode requested but ICC_I_UNDERSTAND_LIVE_RISK != 'yes'; "
                  "falling back to DRY-RUN.")
        return DryRunBroker(data_source=real)
    return real


def in_session(cfg: BotConfig, now: Optional[datetime] = None) -> bool:
    if not cfg.session_enabled:
        return True
    now = now or datetime.now(timezone.utc)
    return cfg.session_start_utc <= now.hour < cfg.session_end_utc


def size_for_order(cfg: BotConfig, symbol: str, risk_units: float) -> float:
    """Convert risk-based underlying units into the order size for the venue.

    Spot: order size = underlying units. Derivatives: whole contracts =
    floor(units / contract_multiplier); returns 0 if it rounds below 1 contract.
    """
    if not cfg.is_derivatives:
        return risk_units
    mult = cfg.multiplier_for(symbol)
    if mult <= 0:
        return 0.0
    return float(math.floor(risk_units / mult))


def _signal_to_order(sig: Signal, qty: float) -> Order:
    side = Side.BUY if sig.direction.value == "long" else Side.SELL
    return Order(
        symbol=sig.symbol, side=side, qty=qty, type="market",
        stop_price=sig.stop, take_profit=sig.target,
        meta={"reason": sig.reason, "rr": round(sig.rr, 2)},
    )


def run_cycle(cfg: BotConfig, broker: Broker, risk: RiskManager,
              now: Optional[datetime] = None) -> list[dict]:
    """One evaluation pass over all symbols. Returns a list of action records."""
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    if risk.state.day != day:
        risk.start_day(day, broker.get_account().equity)

    actions: list[dict] = []
    if not in_session(cfg, now):
        return [{"status": "skip", "reason": "out of session"}]

    for symbol in cfg.symbols:
        htf = broker.get_bars(symbol, cfg.htf, limit=300)
        ltf = broker.get_bars(symbol, cfg.ltf, limit=300)
        sig = evaluate(symbol, htf, ltf, cfg.params)
        if sig is None:
            actions.append({"symbol": symbol, "status": "no_signal"})
            continue

        account = broker.get_account()
        ok, why = risk.can_trade(account)
        if not ok:
            actions.append({"symbol": symbol, "status": "blocked", "reason": why})
            continue

        # Derivatives: resolve the per-contract multiplier from the venue if the
        # config didn't pin one (so monthly-rolled contracts self-configure).
        if cfg.is_derivatives and symbol not in cfg.contract_specs:
            try:
                m = broker.contract_multiplier(symbol)
                if m and m > 0:
                    cfg.contract_specs[symbol] = m
            except Exception as exc:
                actions.append({"symbol": symbol, "status": "error",
                                "reason": f"contract resolve failed: {exc}"})
                continue

        risk_units = risk.position_size(account, sig)
        qty = size_for_order(cfg, symbol, risk_units)
        if qty <= 0:
            actions.append({"symbol": symbol, "status": "zero_size",
                            "reason": "below 1 contract" if cfg.is_derivatives else "no size"})
            continue

        order = _signal_to_order(sig, qty)
        result = broker.place_bracket(order)   # entry + on-venue stop & target
        risk.register_open()
        actions.append({
            "symbol": symbol, "status": "ordered", "direction": sig.direction.value,
            "entry": sig.entry, "stop": sig.stop, "target": sig.target,
            "rr": round(sig.rr, 2), "qty": qty, "broker_result": result,
        })
        log.info("SIGNAL %s %s entry=%.4f stop=%.4f target=%.4f rr=%.2f qty=%.6f",
                 sig.direction.value, symbol, sig.entry, sig.stop, sig.target, sig.rr, qty)
    return actions


def run_forever(cfg: Optional[BotConfig] = None) -> None:
    cfg = cfg or load_config()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    broker = build_broker(cfg)
    risk = RiskManager(cfg.risk)
    log.info("ICC bot starting: broker=%s mode=%s symbols=%s htf=%s ltf=%s",
             broker.name, cfg.mode.value, cfg.symbols, cfg.htf, cfg.ltf)
    while True:
        try:
            for a in run_cycle(cfg, broker, risk):
                if a.get("status") not in ("no_signal", "skip"):
                    log.info("action: %s", a)
        except Exception:  # keep the loop alive; a bad cycle shouldn't kill the bot
            log.exception("cycle error")
        time.sleep(cfg.poll_seconds)
