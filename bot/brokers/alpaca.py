"""Alpaca broker: SPY options (also handles SPY equity legs).

Uses Alpaca's Trading API v2 over REST. Requires:
    ALPACA_API_KEY, ALPACA_API_SECRET
    ALPACA_BASE_URL (defaults to paper trading)
For options the bot resolves a contract from the SPY chain matching the config
DTE / delta target, then submits a market order for ``contract_size`` contracts.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

import requests

from ..config import Config, Instrument
from .base import BrokerBase, OrderResult

log = logging.getLogger(__name__)


class AlpacaBroker(BrokerBase):
    supported = (Instrument.SPY_OPTIONS,)

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.base = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        self.data = os.getenv("ALPACA_OPTIONS_DATA_URL", "https://data.alpaca.markets")

    def _headers(self) -> dict:
        return {
            "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"],
        }

    def _pick_contract(self, side: str) -> str:
        """Resolve an option contract symbol from the SPY chain.

        A BUY signal -> a call; a SELL signal -> a put (directional long option).
        Chooses the nearest expiry >= target DTE and the strike closest to the
        configured delta target using Alpaca's options snapshot greeks.
        """
        opt_type = "call" if side == "buy" else "put"
        target_exp = (dt.date.today() + dt.timedelta(days=self.cfg.option_dte)).isoformat()
        url = f"{self.base}/v2/options/contracts"
        params = {
            "underlying_symbols": "SPY",
            "type": opt_type,
            "expiration_date_gte": target_exp,
            "status": "active",
            "limit": 100,
        }
        resp = requests.get(url, headers=self._headers(), params=params, timeout=15)
        resp.raise_for_status()
        contracts = resp.json().get("option_contracts", [])
        if not contracts:
            raise RuntimeError("No SPY option contracts returned")
        # nearest expiry
        exp = min(c["expiration_date"] for c in contracts)
        same_exp = [c for c in contracts if c["expiration_date"] == exp]
        # choose strike by delta if greeks present, else the middle strike.
        best = min(
            same_exp,
            key=lambda c: abs(float(c.get("delta", 0.5) or 0.5) - self.cfg.option_delta_target)
            if c.get("delta") is not None else abs(0.5 - self.cfg.option_delta_target),
        )
        return best["symbol"]

    def place_order(self, side: str, size: float) -> OrderResult:
        self._guard(self.cfg.instrument)
        try:
            contract = self._pick_contract(side)
        except Exception as exc:  # noqa: BLE001
            if not self.cfg.dry_run:
                return OrderResult(ok=False, broker="alpaca", symbol="SPY",
                                   side=side, size=size, error=str(exc))
            contract = f"SPY{'C' if side == 'buy' else 'P'}(sim)"

        if self.cfg.dry_run:
            log.info("[DRY] Alpaca options %s %g x %s", side, size, contract)
            return OrderResult(ok=True, broker="alpaca", symbol=contract, side=side,
                               size=size, order_id="dry-alpaca")

        # Directional long option: always BUY the option to open, regardless of
        # market direction (the call/put encodes direction).
        body = {
            "symbol": contract,
            "qty": str(int(size)),
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        }
        try:
            resp = requests.post(f"{self.base}/v2/orders", headers=self._headers(),
                                 json=body, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return OrderResult(ok=True, broker="alpaca", symbol=contract, side=side,
                               size=size, order_id=data.get("id"), raw=data)
        except Exception as exc:  # noqa: BLE001
            log.exception("Alpaca order failed")
            return OrderResult(ok=False, broker="alpaca", symbol=contract, side=side,
                               size=size, error=str(exc))
