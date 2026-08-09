"""Tradovate broker: MES (Micro E-mini S&P 500) futures.

Uses the Tradovate REST API. Requires:
    TRADOVATE_USERNAME, TRADOVATE_PASSWORD,
    TRADOVATE_APP_ID, TRADOVATE_APP_SECRET (a.k.a. cid / sec),
    TRADOVATE_ACCOUNT_SPEC, TRADOVATE_ACCOUNT_ID
    TRADOVATE_ENV = "demo" | "live"
Auth is a two-step access-token exchange; the token is cached until expiry.
``contract_size`` is the number of MES contracts.
"""
from __future__ import annotations

import logging
import os
import time

import requests

from ..config import Config, Instrument
from .base import BrokerBase, OrderResult

log = logging.getLogger(__name__)

_HOSTS = {
    "demo": "https://demo.tradovateapi.com/v1",
    "live": "https://live.tradovateapi.com/v1",
}


class TradovateBroker(BrokerBase):
    supported = (Instrument.MES_FUTURES,)

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.env = os.getenv("TRADOVATE_ENV", "demo")
        self.host = _HOSTS[self.env]
        self._token: str | None = None
        self._token_exp: float = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        body = {
            "name": os.environ["TRADOVATE_USERNAME"],
            "password": os.environ["TRADOVATE_PASSWORD"],
            "appId": os.environ["TRADOVATE_APP_ID"],
            "appVersion": "1.0",
            "cid": os.environ["TRADOVATE_APP_ID"],
            "sec": os.environ["TRADOVATE_APP_SECRET"],
        }
        resp = requests.post(f"{self.host}/auth/accessTokenRequest", json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "accessToken" not in data:
            raise RuntimeError(f"Tradovate auth failed: {data}")
        self._token = data["accessToken"]
        self._token_exp = time.time() + 60 * 60  # ~ token lifetime
        return self._token

    def _front_month_symbol(self) -> str:
        """Return the active MES contract symbol (e.g. MESU5). Operators can
        pin one via TRADOVATE_MES_SYMBOL; otherwise we ask Tradovate."""
        pinned = os.getenv("TRADOVATE_MES_SYMBOL")
        if pinned:
            return pinned
        token = self._access_token()
        resp = requests.get(
            f"{self.host}/contract/find",
            params={"name": "MES"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("name", "MES") if isinstance(data, dict) else "MES"

    def place_order(self, side: str, size: float) -> OrderResult:
        self._guard(self.cfg.instrument)
        if self.cfg.dry_run:
            log.info("[DRY] Tradovate MES %s %d contracts", side, int(size))
            return OrderResult(ok=True, broker="tradovate", symbol="MES", side=side,
                               size=size, order_id="dry-tradovate")
        try:
            token = self._access_token()
            symbol = self._front_month_symbol()
            body = {
                "accountSpec": os.environ["TRADOVATE_ACCOUNT_SPEC"],
                "accountId": int(os.environ["TRADOVATE_ACCOUNT_ID"]),
                "action": "Buy" if side == "buy" else "Sell",
                "symbol": symbol,
                "orderQty": int(size),
                "orderType": "Market",
                "isAutomated": True,
            }
            resp = requests.post(f"{self.host}/order/placeorder", json=body,
                                 headers={"Authorization": f"Bearer {token}"}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return OrderResult(ok=True, broker="tradovate", symbol=symbol, side=side,
                               size=size, order_id=str(data.get("orderId")), raw=data)
        except Exception as exc:  # noqa: BLE001
            log.exception("Tradovate order failed")
            return OrderResult(ok=False, broker="tradovate", symbol="MES", side=side,
                               size=size, error=str(exc))
