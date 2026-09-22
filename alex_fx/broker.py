"""FOREX.com (GAIN Capital) REST Trading API adapter + offline simulator.

Paper trading = a FOREX.com **demo** account through the SAME REST API (demo
credentials + demo app key). Live = a funded account. A fully-offline SimBroker
is also provided so backtests/dry-runs need no network or credentials.

Auth flow (GAIN Capital Trading API):
  POST {BASE}/session            {UserName, Password, AppKey} -> {Session}
  then every request carries headers: UserName, Session
  GET  {BASE}/useraccount/ClientAndTradingAccount            -> trading accounts
  GET  {BASE}/cfd/markets?MarketName=EUR/USD                 -> MarketId
  GET  {BASE}/market/{id}/barhistory?interval=..&span=..&PriceBars=..
  POST {BASE}/order/newtradeorder {MarketId, Direction, Quantity, ...}

NOTE: FOREX.com US vs international and demo vs live use different hosts, and
GAIN has revised paths over time. Set FOREXCOM_BASE_URL and confirm the exact
endpoints against the API docs your account is issued. Everything here is
guarded so a wrong host fails cleanly instead of trading by accident.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from alex_bot.strategy import Candle
from .instrument import to_pips

log = logging.getLogger("alexfx.broker")

# Demo/live hosts differ; override with FOREXCOM_BASE_URL. Confirm before live.
DEFAULT_BASE = os.getenv("FOREXCOM_BASE_URL", "https://ciapi.cityindex.com/TradingAPI")
_INTERVAL = {"1m": ("MINUTE", 1), "5m": ("MINUTE", 5), "15m": ("MINUTE", 15),
             "30m": ("MINUTE", 30), "1h": ("HOUR", 1), "4h": ("HOUR", 4),
             "1d": ("DAY", 1)}


@dataclass
class Fill:
    ok: bool
    pair: str
    side: str
    lots: float
    price: float | None = None
    order_id: str | None = None
    demo: bool = True
    error: str | None = None
    raw: dict = field(default_factory=dict)


class SimBroker:
    """Offline paper broker — simulates fills at the given price, sends nothing."""
    def __init__(self):
        self.positions: dict = {}

    def market(self, pair: str, side: str, lots: float, price: float) -> Fill:
        log.info("[SIM] %s %s %g lots @ %s", side, pair, lots, price)
        return Fill(ok=True, pair=pair, side=side, lots=lots, price=price,
                    order_id="sim", demo=True)


class ForexComBroker:
    """Live/demo FOREX.com via REST. mode='demo' is paper trading."""
    def __init__(self, mode: str = "demo", base_url: str | None = None):
        self.mode = mode
        self.base = (base_url or DEFAULT_BASE).rstrip("/")
        self._session: str | None = None
        self._user: str | None = None
        self._trading_account_id: str | None = None
        self._market_ids: dict[str, int] = {}

    # -- auth -----------------------------------------------------------------
    def login(self) -> None:
        import requests
        self._user = os.environ["FOREXCOM_USERNAME"]
        body = {"UserName": self._user,
                "Password": os.environ["FOREXCOM_PASSWORD"],
                "AppKey": os.environ["FOREXCOM_APPKEY"]}
        r = requests.post(f"{self.base}/session", json=body, timeout=20)
        r.raise_for_status()
        self._session = r.json()["Session"]

    def _headers(self) -> dict:
        if not self._session:
            self.login()
        return {"UserName": self._user, "Session": self._session,
                "Content-Type": "application/json"}

    def trading_account_id(self) -> str:
        import requests
        if self._trading_account_id:
            return self._trading_account_id
        r = requests.get(f"{self.base}/useraccount/ClientAndTradingAccount",
                         headers=self._headers(), timeout=20)
        r.raise_for_status()
        accts = r.json().get("TradingAccounts", [])
        if not accts:
            raise RuntimeError("no FOREX.com trading accounts returned")
        self._trading_account_id = str(accts[0]["TradingAccountId"])
        return self._trading_account_id

    def market_id(self, pair: str) -> int:
        import requests
        if pair in self._market_ids:
            return self._market_ids[pair]
        r = requests.get(f"{self.base}/cfd/markets",
                         params={"MarketName": pair}, headers=self._headers(),
                         timeout=20)
        r.raise_for_status()
        markets = r.json().get("Markets", [])
        if not markets:
            raise RuntimeError(f"FOREX.com market not found: {pair}")
        mid = int(markets[0]["MarketId"])
        self._market_ids[pair] = mid
        return mid

    # -- data -----------------------------------------------------------------
    def price_history(self, pair: str, tf: str, bars: int = 300) -> list[Candle]:
        import requests
        interval, span = _INTERVAL[tf]
        mid = self.market_id(pair)
        r = requests.get(f"{self.base}/market/{mid}/barhistory",
                         params={"interval": interval, "span": span,
                                 "PriceBars": bars}, headers=self._headers(),
                         timeout=20)
        r.raise_for_status()
        out = []
        for b in r.json().get("PriceBars", []):
            t = b.get("BarDate", "")
            secs = int(t[6:19]) // 1000 if "/Date(" in str(t) else 0
            out.append(Candle(time=secs, open=float(b["Open"]),
                              high=float(b["High"]), low=float(b["Low"]),
                              close=float(b["Close"])))
        return out

    # -- orders ---------------------------------------------------------------
    def market(self, pair: str, side: str, lots: float, price: float) -> Fill:
        import requests
        try:
            body = {
                "MarketId": self.market_id(pair),
                "Direction": "buy" if side == "buy" else "sell",
                "Quantity": lots * 100_000,           # units of base currency
                "TradingAccountId": self.trading_account_id(),
                "OrderId": 0,
                "AutoRollover": False,
            }
            r = requests.post(f"{self.base}/order/newtradeorder", json=body,
                              headers=self._headers(), timeout=20)
            r.raise_for_status()
            data = r.json()
            oid = data.get("OrderId") or data.get("Orders", [{}])[0].get("OrderId")
            log.info("[%s] FOREX.com %s %s %g lots -> %s",
                     self.mode.upper(), side, pair, lots, oid)
            return Fill(ok=bool(oid), pair=pair, side=side, lots=lots,
                        price=price, order_id=str(oid) if oid else None,
                        demo=(self.mode == "demo"), raw=data)
        except Exception as exc:  # noqa: BLE001
            log.exception("FOREX.com order failed")
            return Fill(ok=False, pair=pair, side=side, lots=lots,
                        demo=(self.mode == "demo"), error=str(exc))


def get_broker(mode: str):
    """mode: 'sim' (offline) | 'demo' (FOREX.com paper) | 'live' (FOREX.com real)."""
    if mode == "sim":
        return SimBroker()
    return ForexComBroker(mode=mode)
