"""Broker factory: map the ``Broker`` toggle to a concrete implementation."""
from __future__ import annotations

from ..config import Broker, Config
from .alpaca import AlpacaBroker
from .base import BrokerBase
from .coinbase import CoinbaseBroker
from .tradovate import TradovateBroker
from .webull import WebullBroker
from .webull_openapi import WebullOpenAPIBroker

_REGISTRY: dict[Broker, type[BrokerBase]] = {
    Broker.COINBASE: CoinbaseBroker,
    Broker.ALPACA: AlpacaBroker,
    Broker.TRADOVATE: TradovateBroker,
    Broker.WEBULL: WebullBroker,
}


def _broker_cls(cfg: Config) -> type[BrokerBase]:
    # In free-symbol (Alex) mode, Webull routes through the official OpenAPI
    # (crypto/futures) unless the community backend is explicitly forced. The
    # fixed-Instrument path (e.g. SPY options in `dual`) keeps the legacy broker.
    if (cfg.broker == Broker.WEBULL and cfg.free_mode()
            and cfg.webull_backend == "openapi"):
        return WebullOpenAPIBroker
    return _REGISTRY[cfg.broker]


def get_broker(cfg: Config) -> BrokerBase:
    cfg.validate()
    broker = _broker_cls(cfg)(cfg)
    if not broker.supports_cfg():
        target = (cfg.asset_class.value if cfg.free_mode() else cfg.instrument.value)
        raise ValueError(f"{cfg.broker.value} cannot trade {target}")
    return broker
