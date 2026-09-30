"""Broker abstraction. Each concrete broker implements ``place_order`` for the
instruments it supports and returns a normalized :class:`OrderResult`."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import Config, Instrument


@dataclass
class OrderResult:
    ok: bool
    broker: str
    symbol: str
    side: str
    size: float
    order_id: str | None = None
    raw: dict = field(default_factory=dict)
    error: str | None = None


class BrokerBase(ABC):
    #: instruments this broker is allowed to trade
    supported: tuple[Instrument, ...] = ()

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def supports(self, instrument: Instrument) -> bool:
        return instrument in self.supported

    def _guard(self, instrument: Instrument) -> None:
        if not self.supports(instrument):
            raise ValueError(
                f"{self.__class__.__name__} does not support {instrument.value}"
            )

    @abstractmethod
    def place_order(self, side: str, size: float) -> OrderResult:
        """Place a market order. ``side`` is 'buy' or 'sell'. ``size`` is the
        contract-size toggle interpreted per instrument."""

    # Optional; brokers that support flattening override this.
    def flatten(self) -> OrderResult:  # pragma: no cover - default
        return OrderResult(ok=False, broker=self.__class__.__name__,
                           symbol=self.cfg.symbol(), side="flat", size=0,
                           error="flatten not implemented")
