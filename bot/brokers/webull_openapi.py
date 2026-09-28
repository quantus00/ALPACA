"""Webull broker on the **official Webull OpenAPI** — crypto + futures.

This is the supported path (vs. the unofficial community ``webull`` package in
``webull.py``). It trades any asset class Webull exposes through their OpenAPI;
here we wire **crypto** and **futures** for the Alex strategy.

Credentials (from the Webull OpenAPI developer portal):
    WEBULL_APP_KEY      app key
    WEBULL_APP_SECRET   app secret
    WEBULL_ACCOUNT_ID   the trading account id to route orders to
    WEBULL_REGION       region id (default "us")

The SDK (``webull-python-sdk-core`` + ``webull-python-sdk-trade``) is imported
lazily and only when actually placing a LIVE order, so dry-run works with no
SDK and no credentials. Everything Webull-specific is funnelled through the two
seams ``_client()`` and ``_submit_instruction()`` — if the installed SDK version
differs, those are the only two methods to adjust.
"""
from __future__ import annotations

import logging
import os
import uuid

import requests

from ..config import AssetClass, Config
from .base import BrokerBase, OrderResult

log = logging.getLogger(__name__)

# Coinbase public product used to price a Webull crypto symbol for P/L marks
# (keyless). "BTCUSD"/"BTC-USD" -> "BTC-USD".
_CRYPTO_PRICE_HOST = "https://api.exchange.coinbase.com"


def wb_crypto_symbol(symbol: str) -> str:
    """Normalize a crypto symbol to Webull's form: 'BTC-USD' -> 'BTCUSD'."""
    return symbol.upper().replace("-", "").replace("/", "")


def coinbase_product(symbol: str) -> str:
    """'BTCUSD'/'BTC-USD' -> 'BTC-USD' for Coinbase public pricing."""
    s = symbol.upper().replace("/", "-")
    if "-" in s:
        return s
    for quote in ("USD", "USDT", "USDC", "EUR", "GBP"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}-{quote}"
    return s


class WebullOpenAPIBroker(BrokerBase):
    """Official Webull OpenAPI. Free-symbol mode only (crypto + futures)."""

    supported = ()  # free-symbol (asset-class) mode only; not for fixed Instruments
    supported_assets = (AssetClass.CRYPTO, AssetClass.FUTURES)

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self._api = None

    # -- SDK seam #1: build the authenticated trade API client ----------------
    def _client(self):
        """Return an authenticated Webull OpenAPI trade client (lazy).

        NOTE: this is one of the two Webull-SDK seams. Confirm the import path
        and constructor against your installed ``webull-python-sdk-*`` version.
        """
        if self._api is not None:
            return self._api
        from webullsdkcore.client import ApiClient  # type: ignore
        from webullsdktrade.api import API  # type: ignore

        app_key = os.environ["WEBULL_APP_KEY"]
        app_secret = os.environ["WEBULL_APP_SECRET"]
        region = os.getenv("WEBULL_REGION", "us")
        api_client = ApiClient(app_key, app_secret, region)
        self._api = API(api_client)
        return self._api

    def _account_id(self) -> str:
        acct = os.getenv("WEBULL_ACCOUNT_ID", "")
        if not acct:
            raise RuntimeError("WEBULL_ACCOUNT_ID is required for Webull OpenAPI orders")
        return acct

    def _wb_symbol(self) -> str:
        sym = self.cfg.symbol()
        return wb_crypto_symbol(sym) if self.cfg.asset_class == AssetClass.CRYPTO else sym.upper()

    def _instrument_type(self) -> str:
        return "CRYPTO" if self.cfg.asset_class == AssetClass.CRYPTO else "FUTURES"

    # -- pricing --------------------------------------------------------------
    def mark_price(self, symbol: str | None = None,
                   meta: dict | None = None) -> float | None:
        sym = symbol or self.cfg.symbol()
        if self.cfg.asset_class == AssetClass.CRYPTO:
            product = coinbase_product(sym)
            try:
                resp = requests.get(f"{_CRYPTO_PRICE_HOST}/products/{product}/ticker",
                                    timeout=10, headers={"User-Agent": "icc-alex/1.0"})
                resp.raise_for_status()
                return float(resp.json()["price"])
            except Exception as exc:  # noqa: BLE001
                log.warning("Webull crypto mark_price(%s) failed: %s", product, exc)
                return None
        # Futures: quote via the SDK (guarded; None when unavailable).
        try:
            api = self._client()
            quote = api.market_data.get_quote(self._wb_symbol(), "FUTURES")  # seam
            data = quote if isinstance(quote, dict) else getattr(quote, "__dict__", {})
            for key in ("last", "lastPrice", "close", "price"):
                if data.get(key) is not None:
                    return float(data[key])
        except Exception as exc:  # noqa: BLE001
            log.warning("Webull futures mark_price(%s) failed: %s", sym, exc)
        return None

    def get_balances(self) -> dict:
        try:
            api = self._client()
            resp = api.account.get_account_balance(self._account_id())  # seam
            data = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {})
            return data or {"raw": str(resp)[:500]}
        except Exception as exc:  # noqa: BLE001
            log.warning("Webull get_balances failed: %s", exc)
            return {"error": str(exc)}

    # -- SDK seam #2: submit a normalized order instruction -------------------
    def _submit_instruction(self, instruction: dict) -> dict:
        """Send one normalized order to Webull and return the raw response.

        NOTE: this is the second Webull-SDK seam. The ``instruction`` dict is
        broker-neutral; map it to your installed SDK's trade call here. The
        default targets the documented OpenAPI ``place_order`` trade endpoint.
        """
        api = self._client()
        # Most webull-python-sdk-trade versions expose an order namespace with a
        # place_order that takes (account_id, payload). Confirm for your version.
        place = getattr(getattr(api, "order", api), "place_order", None)
        if place is None:
            raise NotImplementedError(
                "Webull SDK trade order method not found; adjust "
                "WebullOpenAPIBroker._submit_instruction for your SDK version.")
        resp = place(self._account_id(), instruction)
        return resp if isinstance(resp, dict) else getattr(resp, "__dict__", {}) or {}

    def _order(self, side: str, size: float) -> OrderResult:
        symbol = self.cfg.symbol()
        wb_symbol = self._wb_symbol()
        instruction = {
            "client_order_id": str(uuid.uuid4()),
            "symbol": wb_symbol,
            "instrument_type": self._instrument_type(),
            "side": side.upper(),                 # BUY | SELL
            "order_type": "MARKET",
            "time_in_force": "DAY",
            "quantity": size,
        }
        data = self._submit_instruction(instruction)
        oid = (data.get("order_id") or data.get("orderId")
               or data.get("clientOrderId") or instruction["client_order_id"])
        fill = data.get("avg_price") or data.get("filledPrice") or self.mark_price(symbol)
        return OrderResult(ok=True, broker="webull", symbol=symbol, side=side,
                           size=size, order_id=str(oid) if oid else None,
                           fill_price=fill, meta={"wb_symbol": wb_symbol,
                                                  "asset": self._instrument_type()},
                           raw=data if isinstance(data, dict) else {})

    def place_order(self, side: str, size: float) -> OrderResult:
        self._guard()
        symbol = self.cfg.symbol()
        if self.cfg.dry_run:
            fill = self.mark_price(symbol)
            log.info("[DRY] Webull OpenAPI %s %s %s (%s) @ %s", side, size, symbol,
                     self._instrument_type(), fill)
            return OrderResult(ok=True, broker="webull", symbol=symbol, side=side,
                               size=size, order_id=f"dry-{uuid.uuid4()}", fill_price=fill,
                               meta={"asset": self._instrument_type()})
        try:
            return self._order(side, size)
        except Exception as exc:  # noqa: BLE001
            log.exception("Webull OpenAPI order failed")
            return OrderResult(ok=False, broker="webull", symbol=symbol, side=side,
                               size=size, error=str(exc))

    def flatten(self, side: str | None = None, size: float | None = None,
                symbol: str | None = None, meta: dict | None = None) -> OrderResult:
        """Close by sending the offsetting market order (crypto/futures)."""
        self._guard()
        sym = symbol or self.cfg.symbol()
        close_side = "sell" if (side or "buy") == "buy" else "buy"
        qty = size if size is not None else self.cfg.contract_size
        if self.cfg.dry_run:
            log.info("[DRY] Webull OpenAPI FLATTEN %s %s %s", close_side, qty, sym)
            return OrderResult(ok=True, broker="webull", symbol=sym, side=close_side,
                               size=qty, order_id="dry-flat")
        try:
            return self._order(close_side, qty)
        except Exception as exc:  # noqa: BLE001
            log.exception("Webull OpenAPI flatten failed")
            return OrderResult(ok=False, broker="webull", symbol=sym, side=close_side,
                               size=qty, error=str(exc))
