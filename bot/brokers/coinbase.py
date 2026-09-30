"""Coinbase broker: BTC spot/perp, US 500 index future & perp, and FX.

Uses the Coinbase Advanced Trade REST API with a Cloud API key (JWT-signed).
Requires ``coinbase-advanced-py`` (imported lazily) plus:
    COINBASE_API_KEY, COINBASE_API_SECRET
Product ids for the US 500 future/perp and FX vary by account/venue — set the
real ids the droplet discovery prints via BOT_US500_FUTURE_PRODUCT,
BOT_US500_PERP_PRODUCT, BOT_COINBASE_FX_PRODUCT, or the generic BOT_COINBASE_PRODUCT.
Perps/futures need the account enabled for Coinbase Financial Markets / INTX.
"""
from __future__ import annotations

import logging
import uuid

from ..config import Config, Instrument
from .base import BrokerBase, OrderResult

log = logging.getLogger(__name__)


class CoinbaseBroker(BrokerBase):
    supported = (
        Instrument.BTC_USD_SPOT,
        Instrument.BTC_NANO_PERP,
        Instrument.US500_FUTURE,
        Instrument.US500_PERP,
        Instrument.COINBASE_FX,
    )

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

    def place_order(self, side: str, size: float) -> OrderResult:
        self._guard(self.cfg.instrument)
        symbol = self.cfg.symbol()
        ptype = self.cfg.coinbase_product_type()   # SPOT / PERP / FUTURE
        coid = str(uuid.uuid4())

        if self.cfg.dry_run:
            log.info("[DRY] Coinbase %s %s %s (%s / %s)", side, size, symbol,
                     self.cfg.instrument.value, ptype)
            return OrderResult(ok=True, broker="coinbase", symbol=symbol,
                               side=side, size=size, order_id=f"dry-{coid}",
                               raw={"product_type": ptype})

        client = self._get_client()
        # Spot markets size in base asset; perps/futures size in base contracts.
        # Coinbase Advanced Trade takes base_size for all three product types.
        cfg = {"market_market_ioc": {"base_size": str(size)}}
        try:
            resp = client.create_order(
                client_order_id=coid,
                product_id=symbol,
                side=side.upper(),
                order_configuration=cfg,
            )
            data = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {})
            oid = (data.get("order_id") or data.get("success_response", {})
                   .get("order_id") if isinstance(data, dict) else None)
            return OrderResult(ok=True, broker="coinbase", symbol=symbol, side=side,
                               size=size, order_id=oid, raw=data if isinstance(data, dict) else {})
        except Exception as exc:  # noqa: BLE001
            log.exception("Coinbase order failed")
            return OrderResult(ok=False, broker="coinbase", symbol=symbol, side=side,
                               size=size, error=str(exc))
