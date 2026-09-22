"""Order execution for the Alex bot.

Two executors behind one interface:
  * PaperBroker  — simulates fills, sends nothing. Always safe.
  * CockpitBroker — routes through the ICC Cockpit's EXISTING broker plumbing
    (its `registry` + `Order`), so Webull/Coinbase live & paper connections are
    reused as-is. It bypasses the cockpit's HTTP-layer CONFIRM/OFF-LIMITS gate
    by calling the broker directly (per "no gate"); the single master switch in
    BotConfig is the only live guard.

The CockpitBroker is written against the cockpit's documented interface and is
imported lazily, so this module runs standalone (paper) with the cockpit absent.
When you drop this bot into the cockpit repo (or add it to PYTHONPATH), the live
path resolves. If the running cockpit's interface differs, only this file changes.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from .config import BotConfig

log = logging.getLogger("alex.exec")


@dataclass
class Fill:
    ok: bool
    broker: str
    symbol: str
    side: str
    qty: float
    price: float | None = None
    order_id: str | None = None
    dry_run: bool = True
    error: str | None = None
    raw: dict = field(default_factory=dict)


class Broker:
    """Execution interface the runner talks to."""
    def market(self, cfg: BotConfig, side: str, qty: float,
               price_hint: float | None = None) -> Fill:  # pragma: no cover
        raise NotImplementedError

    def flatten(self, cfg: BotConfig, open_side: str, qty: float,
                price_hint: float | None = None) -> Fill:
        close = "sell" if open_side == "buy" else "buy"
        return self.market(cfg, close, qty, price_hint)


class PaperBroker(Broker):
    """Simulated fills — never contacts a broker."""
    def market(self, cfg: BotConfig, side: str, qty: float,
               price_hint: float | None = None) -> Fill:
        log.info("[PAPER] %s %s %g %s @ %s", cfg.broker, side, qty, cfg.symbol,
                 price_hint)
        return Fill(ok=True, broker=cfg.broker, symbol=cfg.symbol, side=side,
                    qty=qty, price=price_hint, order_id=f"paper-{uuid.uuid4()}",
                    dry_run=True)


class CockpitBroker(Broker):
    """Routes through the cockpit's own brokers (Webull/Coinbase, live+paper)."""
    def _order_cls(self):
        from brokers.base import Order  # cockpit module  # type: ignore
        return Order

    def _registry(self):
        from brokers import registry  # cockpit module  # type: ignore
        return registry

    def market(self, cfg: BotConfig, side: str, qty: float,
               price_hint: float | None = None) -> Fill:
        dry = cfg.dry_run()
        try:
            Order = self._order_cls()
            registry = self._registry()
            order = Order(
                broker=cfg.broker,
                mode=cfg.mode,
                account_id=cfg.account_id or None,
                symbol=cfg.symbol,
                side=side,
                qty=qty,
                notional=None,
                type="market",
                price=price_hint,
                instrument=cfg.instrument,
                fractional=(cfg.instrument == "crypto"),
            )
            broker = registry.get_broker(cfg.broker, cfg.mode)
            # Direct broker call bypasses the cockpit HTTP CONFIRM/OFF-LIMITS
            # gate by design; `dry` is the only live guard.
            resp = broker.place_order(order, dry_run=dry)
            data = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {}) or {}
            oid = data.get("order_id") or data.get("id") or data.get("orderId")
            fill = data.get("price") or data.get("avg_price") or price_hint
            log.info("%s cockpit %s %g %s (%s) -> %s",
                     "[DRY]" if dry else "[LIVE]", side, qty, cfg.symbol,
                     cfg.instrument, oid or data)
            return Fill(ok=True, broker=cfg.broker, symbol=cfg.symbol, side=side,
                        qty=qty, price=fill, order_id=str(oid) if oid else None,
                        dry_run=dry, raw=data if isinstance(data, dict) else {})
        except ImportError as exc:
            return Fill(ok=False, broker=cfg.broker, symbol=cfg.symbol, side=side,
                        qty=qty, dry_run=dry,
                        error=f"cockpit plumbing not importable ({exc}); run "
                              "this bot inside the cockpit repo / PYTHONPATH.")
        except Exception as exc:  # noqa: BLE001
            log.exception("cockpit order failed")
            return Fill(ok=False, broker=cfg.broker, symbol=cfg.symbol, side=side,
                        qty=qty, dry_run=dry, error=str(exc))


def get_executor(cfg: BotConfig, force_paper: bool = False) -> Broker:
    """PaperBroker unless we're genuinely armed for live through the cockpit."""
    if force_paper or cfg.dry_run():
        return PaperBroker()
    return CockpitBroker()
