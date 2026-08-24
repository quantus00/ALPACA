"""Webull broker via the **official** Webull OpenAPI SDK — paper & live.

This replaces the earlier community-library integration. It uses Webull's
official OpenAPI trade SDK and selects paper vs. live by ``WEBULL_MODE``
(which key set + account it uses):

    WEBULL_MODE=paper -> WEBULL_PAPER_APP_KEY / WEBULL_PAPER_APP_SECRET (fake $)
    WEBULL_MODE=live  -> WEBULL_APP_KEY / WEBULL_APP_SECRET          (REAL $)

Safety model (mirrors the standalone ``webull_trade.py`` connector):
  * Keys come ONLY from env vars — never hardcode, never read from a file.
  * ``dry_run`` (BOT_DRY_RUN) short-circuits: nothing is sent, an order is
    logged and a simulated OrderResult returned.
  * A live send additionally requires ``WEBULL_CONFIRM_LIVE=yes`` so flipping
    the bot to real money is a deliberate, explicit act.

Instruments: this broker is registered for SPY options in the repo's instrument
map. The single order call is isolated in ``_place_order`` and marked
``<<< VERIFY >>>`` — Webull's SDK method names differ by version, so match it to
your installed SDK's examples: https://github.com/webull-inc/openapi-python-sdk
``contract_size`` is the number of option contracts. A BUY signal opens a long
call and a SELL signal opens a long put.
"""
from __future__ import annotations

import logging
import os

from ..config import Config, Instrument
from .base import BrokerBase, OrderResult

log = logging.getLogger(__name__)

# repo instrument -> Webull OpenAPI instrument_type
_INSTRUMENT_TYPE = {
    Instrument.SPY_OPTIONS: "OPTION",
}


class WebullBroker(BrokerBase):
    supported = (Instrument.SPY_OPTIONS,)

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self._api = None
        self.mode = os.getenv("WEBULL_MODE", "paper").strip().lower()
        self.region = os.getenv("WEBULL_REGION", "us").strip().lower()

    # ---- credentials & client ------------------------------------------------
    def _creds(self) -> tuple[str, str]:
        """Return (app_key, app_secret) for the active mode. Paper falls back to
        the live key set if paper-specific keys aren't set, matching the
        standalone connector's behaviour."""
        if self.mode == "live":
            key = os.environ.get("WEBULL_APP_KEY")
            sec = os.environ.get("WEBULL_APP_SECRET")
            which = "WEBULL_APP_KEY/SECRET (LIVE)"
        else:
            key = os.environ.get("WEBULL_PAPER_APP_KEY") or os.environ.get("WEBULL_APP_KEY")
            sec = os.environ.get("WEBULL_PAPER_APP_SECRET") or os.environ.get("WEBULL_APP_SECRET")
            which = "WEBULL_PAPER_APP_KEY/SECRET (paper)"
        if not key or not sec:
            raise RuntimeError(
                f"Missing {which} in the environment. Keys are never read from a file."
            )
        return key, sec

    def _client(self):
        if self._api is not None:
            return self._api
        try:
            from webullsdkcore.client import ApiClient
            from webullsdkcore.common.region import Region
            from webullsdktrade.api import API  # webull-python-sdk-trade
        except ImportError as exc:  # noqa: BLE001
            raise RuntimeError(
                "Webull trade SDK not installed. Run:\n"
                "  pip install webull-python-sdk-core webull-python-sdk-trade\n"
                f"(import error: {exc})"
            ) from exc
        key, sec = self._creds()
        rv = getattr(Region, self.region, None)
        rv = rv.value if rv is not None else self.region
        self._api = API(ApiClient(key, sec, rv))
        return self._api

    def _list_accounts(self, api):
        """<<< VERIFY vs your SDK: account listing >>>
        Usually ``api.account.get_account_list()`` or ``api.account_info.*``."""
        acct = getattr(api, "account", api)
        for name in ("get_account_list", "list_accounts", "get_app_subscriptions"):
            fn = getattr(acct, name, None)
            if fn:
                return fn()
        raise RuntimeError(
            "Could not find an account-list method on the SDK — check examples "
            "and edit _list_accounts()."
        )

    def _account_id(self, api) -> str:
        acct_id = os.getenv("WEBULL_ACCOUNT_ID")
        if acct_id:
            return acct_id
        raise RuntimeError(
            "Set WEBULL_ACCOUNT_ID (from `python -m bot.brokers.webull` accounts, "
            "or the standalone webull_trade.py accounts command)."
        )

    def _place_order(self, api, account_id: str, symbol: str, side: str,
                     qty: float, instrument_type: str, tif: str = "DAY"):
        """<<< THE ONE ORDER CALL — verify against your SDK version >>>
        Webull's trade facade is typically ``api.order``; the method is often
        ``place_order`` (stocks/options). Build the request your SDK expects and
        return its response. Everything above this line is generic."""
        order = getattr(api, "order", api)
        fn = getattr(order, "place_order", None) or getattr(order, "place_order_v2", None)
        if fn is None:
            raise RuntimeError(
                "Could not find place_order on the SDK's order client — check the "
                "SDK examples and edit _place_order()."
            )
        req = {
            "account_id": account_id,
            "symbol": symbol,
            "instrument_type": instrument_type,   # STOCK | OPTION | FUTURE
            "side": side.upper(),                 # BUY | SELL
            "order_type": "MARKET",
            "time_in_force": tif.upper(),         # DAY | GTC
            "quantity": qty,
        }
        return fn(**req)

    # ---- public API ----------------------------------------------------------
    def place_order(self, side: str, size: float) -> OrderResult:
        self._guard(self.cfg.instrument)
        instrument_type = _INSTRUMENT_TYPE.get(self.cfg.instrument, "OPTION")
        symbol = self.cfg.symbol()  # "SPY"

        if self.cfg.dry_run:
            log.info("[DRY][webull:%s] %s %s %g x %s", self.mode, instrument_type,
                     side, size, symbol)
            return OrderResult(ok=True, broker="webull", symbol=symbol, side=side,
                               size=size, order_id=f"dry-webull-{self.mode}")

        # A live (real-money) send must be explicitly confirmed.
        if self.mode == "live" and os.getenv("WEBULL_CONFIRM_LIVE", "").lower() != "yes":
            return OrderResult(
                ok=False, broker="webull", symbol=symbol, side=side, size=size,
                error="Refusing LIVE Webull order without WEBULL_CONFIRM_LIVE=yes.",
            )

        try:
            api = self._client()
            account_id = self._account_id(api)
            resp = self._place_order(api, account_id, symbol, side, size, instrument_type)
            order_id = None
            if isinstance(resp, dict):
                order_id = resp.get("orderId") or resp.get("order_id") or resp.get("id")
            return OrderResult(ok=True, broker="webull", symbol=symbol, side=side,
                               size=size, order_id=str(order_id) if order_id else None,
                               raw=resp if isinstance(resp, dict) else {"resp": str(resp)})
        except Exception as exc:  # noqa: BLE001
            log.exception("Webull order failed")
            return OrderResult(ok=False, broker="webull", symbol=symbol, side=side,
                               size=size, error=str(exc))


def _accounts_cli() -> None:
    """`python -m bot.brokers.webull` — list accounts for the active mode so you
    can copy the WEBULL_ACCOUNT_ID. Read-only; never places an order."""
    from ..config import Config
    logging.basicConfig(level=logging.INFO)
    b = WebullBroker(Config())
    api = b._client()
    print(f"[webull:{b.mode}] accounts:")
    print(b._list_accounts(api))


if __name__ == "__main__":
    _accounts_cli()
