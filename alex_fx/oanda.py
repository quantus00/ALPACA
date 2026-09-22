"""OANDA v20 REST adapter — data + paper (practice) + live, one interface.

Practice account = paper trading. Set:
    OANDA_API_TOKEN   (generate in the OANDA dashboard)
    OANDA_ACCOUNT_ID  (e.g. 101-001-1234567-001)
    OANDA_ENV         practice | live   (default practice)

Self-contained (raw requests). The `oandapyV20` package works too, but a plain
REST client keeps the bot dependency-free. Practice and live differ only by host.
"""
from __future__ import annotations

import logging
import os

import requests

from alex_bot.strategy import Candle

log = logging.getLogger("alexfx.oanda")

HOSTS = {"practice": "https://api-fxpractice.oanda.com",
         "live": "https://api-fxtrade.oanda.com"}
GRAN = {"1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30", "1h": "H1",
        "4h": "H4", "1d": "D"}
STANDARD_LOT = 100_000


def instrument(pair: str) -> str:
    """'EUR/USD' -> 'EUR_USD' (OANDA instrument name)."""
    return pair.replace("/", "_").upper()


def parse_candles(payload: dict, drop_incomplete: bool = True) -> list[Candle]:
    """Turn an OANDA candles response into Candle objects (pure/testable).

    Expects mid prices and UNIX datetime format (time is a unix-seconds string).
    """
    out: list[Candle] = []
    for row in payload.get("candles", []):
        if drop_incomplete and not row.get("complete", True):
            continue
        mid = row.get("mid") or row.get("bid") or row.get("ask") or {}
        if not mid:
            continue
        t = str(row.get("time", "0"))
        secs = int(float(t)) if t.replace(".", "").isdigit() else 0
        out.append(Candle(time=secs, open=float(mid["o"]), high=float(mid["h"]),
                          low=float(mid["l"]), close=float(mid["c"]),
                          volume=float(row.get("volume", 0) or 0)))
    return out


class OandaClient:
    def __init__(self, env: str | None = None, token: str | None = None,
                 account_id: str | None = None):
        self.env = env or os.getenv("OANDA_ENV", "practice")
        self.base = HOSTS[self.env]
        self._token = token or os.environ.get("OANDA_API_TOKEN", "")
        self.account_id = account_id or os.environ.get("OANDA_ACCOUNT_ID", "")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept-Datetime-Format": "UNIX"}

    # -- data -----------------------------------------------------------------
    def candles(self, pair: str, tf: str, count: int = 300) -> list[Candle]:
        gran = GRAN.get(tf, "M15")
        url = f"{self.base}/v3/instruments/{instrument(pair)}/candles"
        r = requests.get(url, headers=self._headers(),
                         params={"granularity": gran, "count": count, "price": "M"},
                         timeout=20)
        r.raise_for_status()
        return parse_candles(r.json())

    def account_summary(self) -> dict:
        url = f"{self.base}/v3/accounts/{self.account_id}/summary"
        r = requests.get(url, headers=self._headers(), timeout=20)
        r.raise_for_status()
        return r.json().get("account", {})

    # -- orders ---------------------------------------------------------------
    def market_order(self, pair: str, units: int, stop: float | None = None,
                     take_profit: float | None = None) -> dict:
        """Positive units = buy, negative = sell. Optional server-side SL/TP."""
        order = {"type": "MARKET", "instrument": instrument(pair),
                 "units": str(int(units)), "timeInForce": "FOK",
                 "positionFill": "DEFAULT"}
        if stop is not None:
            order["stopLossOnFill"] = {"price": f"{stop:.5f}", "timeInForce": "GTC"}
        if take_profit is not None:
            order["takeProfitOnFill"] = {"price": f"{take_profit:.5f}", "timeInForce": "GTC"}
        url = f"{self.base}/v3/accounts/{self.account_id}/orders"
        r = requests.post(url, headers=self._headers(), json={"order": order},
                          timeout=20)
        r.raise_for_status()
        return r.json()


# -- broker adapter matching the alex_fx broker interface --------------------
class Fill:
    def __init__(self, ok, pair, side, lots, price=None, order_id=None,
                 demo=True, error=None, raw=None):
        self.ok, self.pair, self.side, self.lots = ok, pair, side, lots
        self.price, self.order_id, self.demo = price, order_id, demo
        self.error, self.raw = error, raw or {}


class OandaBroker:
    """Uses OANDA for both quotes/candles and order execution. env='practice'
    is paper; env='live' is real money."""
    def __init__(self, env: str = "practice"):
        self.client = OandaClient(env=env)
        self.env = env

    def price_history(self, pair: str, tf: str, count: int = 300) -> list[Candle]:
        return self.client.candles(pair, tf, count)

    def market(self, pair: str, side: str, lots: float, price: float,
               stop: float | None = None, take_profit: float | None = None) -> Fill:
        units = int(round(lots * STANDARD_LOT)) * (1 if side == "buy" else -1)
        try:
            resp = self.client.market_order(pair, units, stop, take_profit)
            fill = resp.get("orderFillTransaction", {})
            oid = fill.get("id") or resp.get("orderCreateTransaction", {}).get("id")
            filled_price = float(fill["price"]) if fill.get("price") else price
            log.info("[OANDA %s] %s %s %g lots (%d units) -> %s @ %s",
                     self.env.upper(), side, pair, lots, units, oid, filled_price)
            return Fill(ok=bool(oid), pair=pair, side=side, lots=lots,
                        price=filled_price, order_id=str(oid) if oid else None,
                        demo=(self.env == "practice"), raw=resp)
        except Exception as exc:  # noqa: BLE001
            log.exception("OANDA order failed")
            return Fill(ok=False, pair=pair, side=side, lots=lots,
                        demo=(self.env == "practice"), error=str(exc))
