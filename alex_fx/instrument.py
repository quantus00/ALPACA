"""Forex instrument helpers: pip size, pip value, lot-based position sizing.

Alex's strategy is market-agnostic; only the money math differs for FX. A
"pip" is 0.0001 for most pairs and 0.01 for JPY-quoted pairs. A standard lot is
100,000 units of the base currency; pip value per standard lot is ~$10 for
USD-quoted pairs (approx for others).
"""
from __future__ import annotations

STANDARD_LOT = 100_000


def pip_size(pair: str) -> float:
    """0.01 for JPY pairs (e.g. USD/JPY), else 0.0001."""
    return 0.01 if pair.upper().endswith("JPY") else 0.0001


def to_pips(pair: str, price_delta: float) -> float:
    return price_delta / pip_size(pair)


def from_pips(pair: str, pips: float) -> float:
    return pips * pip_size(pair)


def pip_value_per_lot(pair: str, price: float, lot: float = 1.0) -> float:
    """Approximate account-currency ($) value of 1 pip for `lot` standard lots.

    Exact for XXX/USD pairs (pip_value = pip_size * 100000 = $10/lot). For
    USD/XXX and crosses this is an approximation (divides by price), which is
    fine for sizing/backtest estimates.
    """
    units = lot * STANDARD_LOT
    quote_pip = pip_size(pair) * units
    if pair.upper().endswith("USD"):        # EUR/USD, GBP/USD -> pip value in USD
        return quote_pip
    if pair.upper().startswith("USD"):      # USD/JPY, USD/CAD -> convert by price
        return quote_pip / price if price else quote_pip
    return quote_pip                        # cross: rough estimate


def lots_for_risk(pair: str, equity: float, risk_pct: float, entry: float,
                  stop: float) -> float:
    """Standard lots so that hitting the stop loses ~risk_pct of equity."""
    stop_pips = abs(to_pips(pair, entry - stop))
    if stop_pips <= 0:
        return 0.0
    dollar_risk = equity * (risk_pct / 100.0)
    per_lot = pip_value_per_lot(pair, entry, 1.0) * stop_pips
    if per_lot <= 0:
        return 0.0
    return dollar_risk / per_lot
