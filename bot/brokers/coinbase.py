"""Coinbase broker: BTC/USD spot and BTC nano perpetual futures.

Uses the Coinbase Advanced Trade REST API with a Cloud API key (JWT-signed).
Requires ``coinbase-advanced-py`` (imported lazily) plus:
    COINBASE_API_KEY, COINBASE_API_SECRET
For the nano perp, the account must be enabled for Coinbase Financial Markets
(INTX) perpetual futures.
"""
from __future__ import annotations

import logging
import uuid

import requests

from ..config import Config, Instrument
from .base import BrokerBase, OrderResult

log = logging.getLogger(__name__)

# Public product used to price a symbol (the INTX perp tracks BTC-USD spot).
_PRICE_PRODUCT = {"BTC-PERP-INTX": "BTC-USD"}


class CoinbaseBroker(BrokerBase):
    supported = (Instrument.BTC_USD_SPOT, Instrument.BTC_NANO_PERP)

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        import os

        from coinbase.rest import RESTClient  # type: ignore

        key = os.environ["COINBASE_API_KEY"]
        secret = os.environ["COINBASE_API_SECRET"]
        self._client = RESTClient(api_key=key, api_secret=secret)
        return self._client

    # -- pricing --------------------------------------------------------------
    def mark_price(self, symbol: str | None = None,
                   meta: dict | None = None) -> float | None:
        """Spot mark from Coinbase's public ticker (no auth needed)."""
        sym = symbol or self.cfg.symbol()
        product = _PRICE_PRODUCT.get(sym, sym)
        try:
            resp = requests.get(
                f"https://api.exchange.coinbase.com/products/{product}/ticker",
                timeout=10, headers={"User-Agent": "mtf-trend-bot/1.0"})
            resp.raise_for_status()
            return float(resp.json()["price"])
        except Exception as exc:  # noqa: BLE001
            log.warning("Coinbase mark_price(%s) failed: %s", product, exc)
            return None

    def get_balances(self) -> dict:
        try:
            client = self._get_client()
            resp = client.get_accounts()
            data = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {})
            out: dict = {}
            for acct in (data.get("accounts", []) if isinstance(data, dict) else []):
                cur = acct.get("currency")
                avail = acct.get("available_balance", {})
                val = avail.get("value") if isinstance(avail, dict) else avail
                if cur and val is not None and float(val) != 0.0:
                    out[cur] = float(val)
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("Coinbase get_balances failed: %s", exc)
            return {"error": str(exc)}

    # -- orders ---------------------------------------------------------------
    def _market(self, side: str, size: float) -> OrderResult:
        symbol = self.cfg.symbol()
        coid = str(uuid.uuid4())
        client = self._get_client()
        cfg = {"market_market_ioc": {"base_size": str(size)}}
        resp = client.create_order(
            client_order_id=coid,
            product_id=symbol,
            side=side.upper(),
            order_configuration=cfg,
        )
        data = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {})
        if not isinstance(data, dict):
            data = {}
        oid = (data.get("order_id")
               or data.get("success_response", {}).get("order_id"))
        # Market IOC rarely returns an average fill inline; use the live mark.
        fill = self.mark_price(symbol)
        return OrderResult(ok=True, broker="coinbase", symbol=symbol, side=side,
                           size=size, order_id=oid, fill_price=fill, raw=data)

    def place_order(self, side: str, size: float) -> OrderResult:
        self._guard(self.cfg.instrument)
        symbol = self.cfg.symbol()

        if self.cfg.dry_run:
            fill = self.mark_price(symbol)
            log.info("[DRY] Coinbase %s %s %s (%s) @ %s", side, size, symbol,
                     self.cfg.instrument.value, fill)
            return OrderResult(ok=True, broker="coinbase", symbol=symbol,
                               side=side, size=size, order_id=f"dry-{uuid.uuid4()}",
                               fill_price=fill)
        try:
            return self._market(side, size)
        except Exception as exc:  # noqa: BLE001
            log.exception("Coinbase order failed")
            return OrderResult(ok=False, broker="coinbase", symbol=symbol, side=side,
                               size=size, error=str(exc))

    def flatten(self, side: str | None = None, size: float | None = None,
                symbol: str | None = None, meta: dict | None = None) -> OrderResult:
        """Close a spot/perp position by sending the offsetting market order."""
        self._guard(self.cfg.instrument)
        sym = symbol or self.cfg.symbol()
        close_side = "sell" if (side or "buy") == "buy" else "buy"
        qty = size if size is not None else self.cfg.contract_size

        if self.cfg.dry_run:
            log.info("[DRY] Coinbase FLATTEN %s %s %s", close_side, qty, sym)
            return OrderResult(ok=True, broker="coinbase", symbol=sym,
                               side=close_side, size=qty, order_id="dry-flat")
        try:
            res = self._market(close_side, qty)
            log.info("Coinbase flattened %s %s %s", close_side, qty, sym)
            return res
        except Exception as exc:  # noqa: BLE001
            log.exception("Coinbase flatten failed")
            return OrderResult(ok=False, broker="coinbase", symbol=sym,
                               side=close_side, size=qty, error=str(exc))
