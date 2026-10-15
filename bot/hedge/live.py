"""Live executor: mirrors the engine's simulated fills as real broker orders.

Only built when BOTH switches are on (see ``live_allowed``):
  * ``safety.live_trading_approved = true`` in hedge_config.toml
  * the bot was started with ``--live`` (``cfg.dry_run`` is False)

Orders go through the bot's existing broker adapters:
  * primary closes (stop / cut / limit) -> the bot's BTC spot broker (sell)
  * hedge open/close                    -> Coinbase BTC perp (hedge_symbol), market IOC
The primary ENTRY is never sent from here; the existing ICC logic already placed it.

Fail-safe: if any order fails or a size guard trips, the executor HALTS. It sends
nothing more, logs CRITICAL and notifies. Positions are then left as they are
for you to manage by hand. It never retries or "fixes" an order automatically.
"""
from __future__ import annotations

import logging
import math
from dataclasses import replace

from ..config import Config, Instrument
from .config import PairSettings
from .engine import Fill

log = logging.getLogger("hedge.live")


def live_allowed(cfg: Config, s: PairSettings) -> bool:
    return bool(s.live_trading_approved) and not cfg.dry_run


class LiveExecutor:
    def __init__(self, cfg: Config, s: PairSettings, primary_broker=None, hedge_broker=None,
                 notifier=None) -> None:
        if not live_allowed(cfg, s):
            raise RuntimeError("live hedge orders need live_trading_approved=true AND --live")
        self.s = s
        self.notifier = notifier
        if primary_broker is None or hedge_broker is None:
            from ..brokers import get_broker
            primary_broker = primary_broker or get_broker(cfg)
            hcfg = replace(cfg, instrument=Instrument.BTC_NANO_PERP,
                           coinbase_product=s.hedge_symbol)
            hedge_broker = hedge_broker or get_broker(hcfg)
        self.primary_broker = primary_broker
        self.hedge_broker = hedge_broker
        self.halted = False
        self._hedge_size: float | None = None     # size actually sent on the open

    # ------------------------------------------------------------------ sizing
    def _hedge_order_size(self, btc: float) -> float:
        s = self.s
        if btc > s.max_hedge_btc:
            raise ValueError(f"hedge {btc:.6f} BTC exceeds max_hedge_btc {s.max_hedge_btc}")
        qty = btc / s.hedge_contract_btc if s.hedge_contract_btc > 0 else btc
        inc = s.hedge_size_increment
        qty = math.floor(qty / inc + 1e-9) * inc
        if qty <= 0:
            raise ValueError(f"hedge size rounds to 0 ({btc:.8f} BTC, increment {inc})")
        return round(qty, 10)

    # ------------------------------------------------------------------ execute
    def execute(self, fill: Fill) -> bool:
        """Send the real order for one fill. Returns False (and halts) on failure."""
        if self.halted:
            log.error("[LIVE] halted — NOT sending %s %s %s", fill.action, fill.leg, fill.symbol)
            return False
        if fill.leg == "primary" and fill.opening:
            return True   # the existing ICC logic already placed the entry
        try:
            if fill.leg == "primary":
                res = self.primary_broker.place_order(fill.action, fill.units)
            else:
                if fill.opening:
                    size = self._hedge_order_size(fill.units)
                else:
                    size = self._hedge_size
                    if size is None:
                        raise RuntimeError("closing a hedge that was never opened live")
                res = self.hedge_broker.place_order(fill.action, size)
                if res.ok:
                    self._hedge_size = size if fill.opening else None
        except Exception as exc:  # noqa: BLE001
            return self._halt(fill, str(exc))
        if not res.ok:
            return self._halt(fill, res.error or "order rejected")
        log.warning("[LIVE] setup#%s %s %s %s %s -> order %s", fill.setup_id, fill.leg,
                    fill.action, res.size, res.symbol, res.order_id)
        return True

    def _halt(self, fill: Fill, why: str) -> bool:
        self.halted = True
        msg = (f"[LIVE] HEDGE MODULE HALTED: {fill.leg} {fill.action} {fill.units:.6f} "
               f"{fill.symbol} failed: {why}. No further hedge orders will be sent. "
               f"CHECK POSITIONS MANUALLY.")
        log.critical(msg)
        if self.notifier:
            try:
                self.notifier._push(msg)
            except Exception:  # noqa: BLE001
                log.warning("halt notify failed", exc_info=True)
        return False
