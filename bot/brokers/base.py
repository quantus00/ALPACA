"""Broker abstraction. Each concrete broker implements ``place_order`` for the
instruments it supports and returns a normalized :class:`OrderResult`."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import AssetClass, Config, Instrument


@dataclass
class OrderResult:
    ok: bool
    broker: str
    symbol: str
    side: str
    size: float
    order_id: str | None = None
    #: average fill / entry price when known (used as the P/L cost basis).
    fill_price: float | None = None
    #: extra per-order context the flatten path needs (e.g. an option id).
    meta: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    error: str | None = None


class BrokerBase(ABC):
    #: instruments this broker is allowed to trade (fixed-Instrument mode)
    supported: tuple[Instrument, ...] = ()
    #: asset classes this broker can trade in free-symbol (Alex) mode
    supported_assets: tuple[AssetClass, ...] = ()

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def supports(self, instrument: Instrument) -> bool:
        return instrument in self.supported

    def supports_cfg(self) -> bool:
        """Whether this broker can trade what ``cfg`` currently selects, in
        whichever mode (free-symbol asset class, or fixed Instrument)."""
        if self.cfg.free_mode():
            return self.cfg.asset_class in self.supported_assets
        return self.supports(self.cfg.instrument)

    def _guard(self, instrument: Instrument | None = None) -> None:
        if self.cfg.free_mode():
            if self.cfg.asset_class not in self.supported_assets:
                allowed = ", ".join(a.value for a in self.supported_assets) or "(none)"
                raise ValueError(
                    f"{self.__class__.__name__} does not trade "
                    f"{self.cfg.asset_class.value} (supports: {allowed})"
                )
            return
        inst = instrument if instrument is not None else self.cfg.instrument
        if not self.supports(inst):
            raise ValueError(
                f"{self.__class__.__name__} does not support {inst.value}"
            )

    @abstractmethod
    def place_order(self, side: str, size: float) -> OrderResult:
        """Place a market order. ``side`` is 'buy' or 'sell'. ``size`` is the
        contract-size toggle interpreted per instrument."""

    # -- Position / account reads (optional; override where supported) --------
    def mark_price(self, symbol: str | None = None,
                   meta: dict | None = None) -> float | None:
        """Current price of an open position, for P/L. ``symbol`` / ``meta``
        carry the open leg's context (e.g. an option id). Returns ``None`` when
        the broker cannot price it."""
        return None

    def get_balances(self) -> dict:  # pragma: no cover - default
        """Return account balances as ``{label: amount}``. Empty when the broker
        does not implement it."""
        return {}

    # Optional; brokers that support flattening override this.
    def flatten(self, side: str | None = None, size: float | None = None,
                symbol: str | None = None, meta: dict | None = None) -> OrderResult:
        """Close an open position. ``side``/``size`` describe the *open* leg so
        the broker can send the offsetting order; ``symbol``/``meta`` carry any
        extra context (e.g. an option id)."""
        return OrderResult(ok=False, broker=self.__class__.__name__,
                           symbol=symbol or self.cfg.symbol(), side="flat", size=0,
                           error="flatten not implemented")
