"""Broker factory: map the ``Broker`` toggle to a concrete implementation."""
from __future__ import annotations

from ..config import Broker, Config
from .alpaca import AlpacaBroker
from .base import BrokerBase
from .coinbase import CoinbaseBroker
from .tradovate import TradovateBroker
from .webull import WebullBroker

_REGISTRY: dict[Broker, type[BrokerBase]] = {
    Broker.COINBASE: CoinbaseBroker,
    Broker.ALPACA: AlpacaBroker,
    Broker.TRADOVATE: TradovateBroker,
    Broker.WEBULL: WebullBroker,
}


def get_broker(cfg: Config) -> BrokerBase:
    cfg.validate()
    broker_cls = _REGISTRY[cfg.broker]
    broker = broker_cls(cfg)
    if not broker.supports(cfg.instrument):
        raise ValueError(
            f"{cfg.broker.value} cannot trade {cfg.instrument.value}"
        )
    return broker
