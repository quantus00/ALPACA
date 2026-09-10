"""Core data types shared across the ICC bot."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Mode(str, Enum):
    """Execution mode, from safest to riskiest."""

    DRY_RUN = "dry_run"   # compute signals, place nothing (default)
    PAPER = "paper"       # place orders against the broker's paper/demo account
    LIVE = "live"         # place real orders with real money


@dataclass(frozen=True)
class Bar:
    """A single OHLCV candle. `ts` is epoch seconds (UTC)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class SwingType(str, Enum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class Swing:
    """A confirmed pivot high or low."""

    type: SwingType
    index: int      # index into the bar series it was detected on
    ts: int
    price: float


@dataclass
class Signal:
    """A trade decision produced by the strategy."""

    direction: Direction
    symbol: str
    entry: float
    stop: float
    target: float
    reason: str
    ts: int

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.target - self.entry)

    @property
    def rr(self) -> float:
        r = self.risk_per_unit
        return (self.reward_per_unit / r) if r > 0 else 0.0


@dataclass
class Order:
    """A normalized order request handed to a broker adapter."""

    symbol: str
    side: Side
    qty: float
    type: str = "market"          # "market" or "limit"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    client_id: Optional[str] = None
    meta: dict = field(default_factory=dict)


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float
