"""Webull adapter (equities), built on the unofficial `webull` package.

    pip install webull

This package supports a **paper-trading account** (`paper_webull`), which is
the recommended way to run this bot on Webull without real money.

Auth: Webull login is stateful and often requires MFA plus a 6-digit trade PIN.
Set WEBULL_EMAIL, WEBULL_PASSWORD, WEBULL_TRADE_PIN (and handle MFA the first
time). Because login/MFA is fragile and version-specific, you may prefer to
authenticate once in a REPL, save the tokens, and pass a pre-built client via
`client=`. Verify method signatures against your installed `webull` version.

Equities trade in whole shares, so sizes are floored to integers.
"""
from __future__ import annotations

import logging
import math
import os
from typing import List, Optional

from ..models import Account, Bar, Order, Side
from .base import Broker

log = logging.getLogger("icc_bot.broker.webull")

# icc_bot timeframe -> webull interval string
_INTERVAL = {"1m": "m1", "5m": "m5", "15m": "m15", "30m": "m30", "1h": "m60", "1d": "d1"}


class WebullBroker(Broker):
    name = "webull"

    def __init__(self, live: bool = False, client=None) -> None:
        self.live = live
        if client is not None:
            self.wb = client
        else:
            self.wb = self._login(live)

    def _login(self, live: bool):
        from webull import paper_webull, webull  # imported lazily

        wb = webull() if live else paper_webull()
        email = os.environ.get("WEBULL_EMAIL")
        password = os.environ.get("WEBULL_PASSWORD")
        pin = os.environ.get("WEBULL_TRADE_PIN")
        if not email or not password:
            raise RuntimeError(
                "Webull credentials missing: set WEBULL_EMAIL and WEBULL_PASSWORD "
                "(and WEBULL_TRADE_PIN for order placement)."
            )
        wb.login(email, password)
        if pin:
            wb.get_trade_token(pin)
        return wb

    def get_account(self) -> Account:
        acct = self.wb.get_account()
        eq = float(_pick(acct, "netLiquidation", "totalMarketValue", default=0.0))
        cash = float(_pick(acct, "cashBalance", "settledFunds", default=eq))
        bp = float(_pick(acct, "dayBuyingPower", "buyingPower", default=cash))
        return Account(equity=eq, cash=cash, buying_power=bp)

    def get_bars(self, symbol: str, timeframe: str, limit: int = 300) -> List[Bar]:
        if timeframe not in _INTERVAL:
            raise ValueError(f"Webull adapter does not map timeframe {timeframe!r}")
        df = self.wb.get_bars(stock=symbol, interval=_INTERVAL[timeframe], count=limit)
        bars: List[Bar] = []
        for ts, row in df.iterrows():
            bars.append(Bar(
                ts=int(ts.timestamp()),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
            ))
        bars.sort(key=lambda b: b.ts)
        return bars[-limit:]

    def place_order(self, order: Order) -> dict:
        shares = int(math.floor(order.qty))
        if shares < 1:
            return {"status": "skipped", "reason": "size < 1 share"}
        action = "BUY" if order.side is Side.BUY else "SELL"
        if not self.live:
            log.info("[webull:paper] %s %s x%d", action, order.symbol, shares)
        return self.wb.place_order(
            stock=order.symbol, action=action, orderType="MKT",
            enforce="DAY", quant=shares,
        )

    def get_positions(self) -> List[dict]:
        out = []
        for pos in self.wb.get_positions() or []:
            sym = _pick(pos, "ticker", "symbol")
            qty = _pick(pos, "position", "quantity", default=0)
            if sym and float(qty) != 0:
                out.append({"symbol": sym, "qty": float(qty)})
        return out


def _pick(obj, *names, default=None):
    """Return the first present key; Webull payloads are nested/inconsistent."""
    if obj is None:
        return default
    if not isinstance(obj, dict):
        obj = getattr(obj, "__dict__", {}) or {}
    for n in names:
        if n in obj and obj[n] is not None:
            return obj[n]
        # Webull sometimes nests under 'accountMembers' list of {key,value}
    for member in obj.get("accountMembers", []) if isinstance(obj, dict) else []:
        if member.get("key") in names:
            return member.get("value")
    return default
