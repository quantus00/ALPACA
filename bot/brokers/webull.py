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

    @staticmethod
    def _opt_price(opt: dict) -> float | None:
        """Pull a usable premium (ask, else close/last) from an option quote."""
        for key in ("askList", "bidList"):
            lst = opt.get(key)
            if isinstance(lst, list) and lst and isinstance(lst[0], dict):
                p = lst[0].get("price")
                if p is not None:
                    return float(p)
        for key in ("ask", "close", "last", "lastPrice"):
            if opt.get(key) is not None:
                return float(opt[key])
        return None

    def mark_price(self, symbol: str | None = None,
                   meta: dict | None = None) -> float | None:
        """Current option premium for the open contract (needs its optionId)."""
        option_id = (meta or {}).get("option_id")
        if not option_id:
            return None
        try:
            wb = self._login()
            quote = wb.get_option_quote(stock="SPY", optionId=option_id)
            data = quote.get("data") if isinstance(quote, dict) else None
            opt = data[0] if isinstance(data, list) and data else quote
            return self._opt_price(opt if isinstance(opt, dict) else {})
        except Exception as exc:  # noqa: BLE001
            log.warning("Webull mark_price(%s) failed: %s", option_id, exc)
            return None

    def get_balances(self) -> dict:
        try:
            wb = self._login()
            acct = wb.get_account()
            data = acct if isinstance(acct, dict) else {}
            out: dict = {}
            for member in data.get("accountMembers", []):
                key = member.get("key")
                val = member.get("value")
                if key and val is not None:
                    out[key] = val
            # Common top-level fields, if the shape differs.
            for key in ("netLiquidation", "totalMarketValue", "cashBalance"):
                if data.get(key) is not None:
                    out.setdefault(key, data[key])
            return out or {"raw": str(data)[:500]}
        except Exception as exc:  # noqa: BLE001
            log.warning("Webull get_balances failed: %s", exc)
            return {"error": str(exc)}

    def place_order(self, side: str, size: float) -> OrderResult:
        self._guard(self.cfg.instrument)
        if self.cfg.dry_run:
            log.info("[DRY] Webull SPY options %s %d contracts", side, int(size))
            return OrderResult(ok=True, broker="webull", symbol="SPY", side=side,
                               size=size, order_id="dry-webull")
        try:
            wb = self._login()
            opt = self._pick_option(wb, side)
            option_id = opt.get("tickerId") or opt.get("optionId")
            entry = self._opt_price(opt)
            resp = wb.place_order_option(
                optionId=option_id,
                lmtPrice=None,
                action="BUY",         # long option; call/put encodes direction
                orderType="MKT",
                enforce="DAY",
                quant=int(size),
            )
            return OrderResult(
                ok=True, broker="webull", symbol="SPY", side=side, size=size,
                order_id=str(resp.get("orderId") if isinstance(resp, dict) else resp),
                fill_price=entry, meta={"option_id": option_id},
                raw=resp if isinstance(resp, dict) else {})
        except Exception as exc:  # noqa: BLE001
            log.exception("Webull order failed")
            return OrderResult(ok=False, broker="webull", symbol="SPY", side=side,
                               size=size, error=str(exc))

    def flatten(self, side: str | None = None, size: float | None = None,
                symbol: str | None = None, meta: dict | None = None) -> OrderResult:
        """Sell-to-close the open long option (the position is always long)."""
        self._guard(self.cfg.instrument)
        qty = size if size is not None else self.cfg.contract_size
        option_id = (meta or {}).get("option_id")

        if self.cfg.dry_run:
            log.info("[DRY] Webull FLATTEN SELL %d contracts (option %s)",
                     int(qty), option_id)
            return OrderResult(ok=True, broker="webull", symbol="SPY",
                               side="sell", size=qty, order_id="dry-flat")
        if not option_id:
            return OrderResult(ok=False, broker="webull", symbol="SPY", side="sell",
                               size=qty, error="no option_id to flatten")
        try:
            wb = self._login()
            resp = wb.place_order_option(
                optionId=option_id, lmtPrice=None, action="SELL",
                orderType="MKT", enforce="DAY", quant=int(qty))
            log.info("Webull flattened option %s x%d", option_id, int(qty))
            return OrderResult(
                ok=True, broker="webull", symbol="SPY", side="sell", size=qty,
                order_id=str(resp.get("orderId") if isinstance(resp, dict) else resp),
                meta={"option_id": option_id},
                raw=resp if isinstance(resp, dict) else {})
        except Exception as exc:  # noqa: BLE001
            log.exception("Webull flatten failed")
            return OrderResult(ok=False, broker="webull", symbol="SPY", side="sell",
                               size=qty, error=str(exc))
