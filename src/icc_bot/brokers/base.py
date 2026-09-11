"""Broker adapter interface and a safe dry-run implementation.

Every concrete broker (Webull, Coinbase) implements `Broker`. The strategy and
runner only ever talk to this interface, so switching venues — or running fully
offline in dry-run — changes nothing upstream.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from ..models import Account, Bar, Order

log = logging.getLogger("icc_bot.broker")


class Broker(ABC):
    """Minimal surface the ICC bot needs from a venue."""

    name: str = "broker"

    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_bars(self, symbol: str, timeframe: str, limit: int = 300) -> List[Bar]:
        """Return recent OHLCV bars, oldest first. `timeframe` e.g. '1h','15m','5m'."""

    @abstractmethod
    def place_order(self, order: Order) -> dict:
        """Submit an order. Implementations must honor dry-run by not sending."""

    def place_bracket(self, order: Order) -> dict:
        """Submit entry + on-venue stop-loss and take-profit as one bracket.

        Default fallback: venues without native brackets place the entry only and
        loudly warn that exits are NOT enforced on the exchange. Override where a
        native bracket/OCO exists (Coinbase does).
        """
        log.warning("%s has no native bracket; placing ENTRY ONLY — stop/target "
                    "are NOT enforced on-venue (stop=%s target=%s)",
                    self.name, order.stop_price, order.take_profit)
        return self.place_order(order)

    @abstractmethod
    def get_positions(self) -> List[dict]: ...


class DryRunBroker(Broker):
    """Wraps a real broker for data but never places orders — just logs them.

    Use this to watch the strategy fire against live data with zero risk. If no
    inner broker is supplied, data methods return empty (useful for unit tests).
    """

    name = "dry_run"

    def __init__(self, data_source: Optional[Broker] = None, equity: float = 10_000.0) -> None:
        self._data = data_source
        self._equity = equity
        self.placed: List[Order] = []

    def get_account(self) -> Account:
        if self._data is not None:
            return self._data.get_account()
        return Account(equity=self._equity, cash=self._equity, buying_power=self._equity)

    def get_bars(self, symbol: str, timeframe: str, limit: int = 300) -> List[Bar]:
        return self._data.get_bars(symbol, timeframe, limit) if self._data else []

    def place_order(self, order: Order) -> dict:
        self.placed.append(order)
        log.warning("[DRY-RUN] would place: %s %s qty=%.6f type=%s sl=%s tp=%s",
                    order.side.value, order.symbol, order.qty, order.type,
                    order.stop_price, order.take_profit)
        return {"status": "dry_run", "order": order.__dict__}

    def place_bracket(self, order: Order) -> dict:
        self.placed.append(order)
        log.warning("[DRY-RUN] would place BRACKET: %s %s qty=%.6f entry=mkt "
                    "stop=%s target=%s", order.side.value, order.symbol, order.qty,
                    order.stop_price, order.take_profit)
        return {"status": "dry_run", "bracket": True, "order": order.__dict__}

    def get_positions(self) -> List[dict]:
        return self._data.get_positions() if self._data else []
