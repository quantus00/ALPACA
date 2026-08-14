"""Webull broker: SPY options.

Webull has no official public trading API; this uses the community
``webull`` package (imported lazily). Requires:
    WEBULL_EMAIL, WEBULL_PASSWORD, WEBULL_TRADE_PIN
    WEBULL_DEVICE_ID (optional, for MFA-remembered device)
``contract_size`` is the number of option contracts. As with Alpaca, a BUY
signal opens a long call and a SELL signal opens a long put.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from ..config import Config, Instrument
from .base import BrokerBase, OrderResult

log = logging.getLogger(__name__)


class WebullBroker(BrokerBase):
    supported = (Instrument.SPY_OPTIONS,)

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self._wb = None

    def _login(self):
        if self._wb is not None:
            return self._wb
        from webull import webull  # type: ignore

        wb = webull()
        if os.getenv("WEBULL_DEVICE_ID"):
            wb._did = os.environ["WEBULL_DEVICE_ID"]
        wb.login(os.environ["WEBULL_EMAIL"], os.environ["WEBULL_PASSWORD"])
        wb.get_trade_token(os.environ["WEBULL_TRADE_PIN"])
        self._wb = wb
        return wb

    def _pick_option(self, wb, side: str):
        opt_dir = "call" if side == "buy" else "put"
        target_exp = (dt.date.today() + dt.timedelta(days=self.cfg.option_dte))
        chain = wb.get_options(stock="SPY", direction=opt_dir)
        if not chain:
            raise RuntimeError("Empty Webull SPY option chain")
        # nearest expiry >= target
        def exp_of(o):
            return o.get("expireDate") or o.get("expDate", "")
        exps = sorted({exp_of(o) for o in chain if exp_of(o)})
        want = next((e for e in exps if e >= target_exp.isoformat()), exps[0])
        candidates = [o for o in chain if exp_of(o) == want]
        # strike closest to ATM (delta target proxy if available)
        best = min(candidates, key=lambda o: abs(
            float(o.get("delta", 0.5) or 0.5) - self.cfg.option_delta_target))
        return best

    def place_order(self, side: str, size: float) -> OrderResult:
        self._guard(self.cfg.instrument)
        if self.cfg.dry_run:
            log.info("[DRY] Webull SPY options %s %d contracts", side, int(size))
            return OrderResult(ok=True, broker="webull", symbol="SPY", side=side,
                               size=size, order_id="dry-webull")
        try:
            wb = self._login()
            opt = self._pick_option(wb, side)
            resp = wb.place_order_option(
                optionId=opt.get("tickerId") or opt.get("optionId"),
                lmtPrice=None,
                action="BUY",         # long option; call/put encodes direction
                orderType="MKT",
                enforce="DAY",
                quant=int(size),
            )
            return OrderResult(ok=True, broker="webull", symbol="SPY", side=side,
                               size=size, order_id=str(resp.get("orderId") if isinstance(resp, dict) else resp),
                               raw=resp if isinstance(resp, dict) else {})
        except Exception as exc:  # noqa: BLE001
            log.exception("Webull order failed")
            return OrderResult(ok=False, broker="webull", symbol="SPY", side=side,
                               size=size, error=str(exc))
