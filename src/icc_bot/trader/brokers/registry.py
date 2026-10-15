"""Maps a broker name + mode to an adapter instance."""
from __future__ import annotations

from .coinbase_broker import CoinbaseBroker
from .mock_broker import MockBroker
from .webull_broker import WebullBroker

_ADAPTERS = {
    "mock": MockBroker,
    "coinbase": CoinbaseBroker,
    "webull": WebullBroker,
}


def get_broker(name: str, mode: str = "paper"):
    if name not in _ADAPTERS:
        raise ValueError(f"unknown broker '{name}'")
    return _ADAPTERS[name](mode=mode)


def names():
    return list(_ADAPTERS)
