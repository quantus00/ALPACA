#!/usr/bin/env python3
"""Official Webull OpenAPI client — BTC (crypto) quotes and order placement.

Uses Webull's official OpenAPI Python SDK (app_key / app_secret), not the
community login. Runs from your terminal or the VS Code Run button.

    pip install webull-python-sdk-core webull-python-sdk-trade webull-python-sdk-mdata

Credentials come from the environment or a local .env file:
    WEBULL_APP_KEY, WEBULL_APP_SECRET
    WEBULL_REGION      (optional: us | hk | jp, default us)
    WEBULL_ACCOUNT_ID  (optional; if unset, the first account is used)

Commands
    python webull_openapi.py selftest                 # verify creds + account + BTC quote
    python webull_openapi.py accounts                 # list accounts + balances
    python webull_openapi.py quote BTCUSD             # latest snapshot (crypto)
    python webull_openapi.py quote AAPL --category US_STOCK
    python webull_openapi.py buy  BTCUSD --qty 0.001  # PREVIEW by default (no send)
    python webull_openapi.py buy  BTCUSD --qty 0.001 --send --yes   # actually place

IMPORTANT
    • Webull OpenAPI market data covers CRYPTO, US_STOCK, US_ETF, US_OPTION.
      It does NOT expose futures — MGC / MES quotes are not available here.
    • Orders placed with --send are REAL orders on the account tied to your keys.
      Without --send the order is only previewed/validated, never submitted.
    • Not financial advice.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


# --------------------------------------------------------------------------
def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Populate os.environ from a simple KEY=VALUE .env (existing vars win)."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# --------------------------------------------------------------------------
class Webull:
    """Thin wrapper over the official Webull OpenAPI SDK."""

    def __init__(self) -> None:
        self._api = None
        self._account_id = os.getenv("WEBULL_ACCOUNT_ID") or None

    def _connect(self):
        if self._api is not None:
            return self._api
        try:
            from webullsdkcore.client import ApiClient
            from webullsdktrade.api import API
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "Webull OpenAPI SDK not installed. Run:\n"
                "  pip install webull-python-sdk-core webull-python-sdk-trade "
                "webull-python-sdk-mdata"
            ) from exc

        app_key = os.getenv("WEBULL_APP_KEY")
        app_secret = os.getenv("WEBULL_APP_SECRET")
        if not app_key or not app_secret:
            raise SystemExit("Set WEBULL_APP_KEY and WEBULL_APP_SECRET (env or .env).")
        region = (os.getenv("WEBULL_REGION") or "us").lower()

        client = ApiClient(app_key, app_secret, region)
        self._api = API(client)
        return self._api

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _json(resp):
        """Unwrap an SDK response into a Python object."""
        if resp is None:
            return None
        for attr in ("json",):
            fn = getattr(resp, attr, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:  # noqa: BLE001
                    pass
        body = getattr(resp, "content", None) or getattr(resp, "text", None)
        if body is None:
            return resp
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", "replace")
        try:
            return json.loads(body)
        except Exception:  # noqa: BLE001
            return body

    def _category(self, name: str):
        from webullsdkmdata.common.category import Category
        try:
            return getattr(Category, name.upper()).value
        except AttributeError:
            valid = [c for c in dir(Category) if c.isupper()]
            raise SystemExit(f"Unknown category {name!r}. Valid: {', '.join(valid)}")

    def account_id(self) -> str:
        if self._account_id:
            return self._account_id
        api = self._connect()
        data = self._json(api.account_v2.get_account_list())
        accounts = data.get("data") if isinstance(data, dict) else data
        if not accounts:
            raise SystemExit(f"No accounts returned: {data}")
        first = accounts[0]
        self._account_id = str(first.get("account_id") or first.get("accountId"))
        return self._account_id

    # ---- operations ------------------------------------------------------
    def accounts(self):
        api = self._connect()
        return self._json(api.account_v2.get_account_list())

    def balance(self, account_id: str | None = None):
        api = self._connect()
        return self._json(api.account_v2.get_account_balance(account_id or self.account_id()))

    def snapshot(self, symbols: str, category: str = "CRYPTO"):
        api = self._connect()
        return self._json(api.market_data.get_snapshot(symbols, self._category(category)))

    def instrument_id(self, symbol: str, category: str = "CRYPTO") -> str:
        api = self._connect()
        data = self._json(api.instrument.get_instrument(symbol, self._category(category)))
        rows = data.get("data") if isinstance(data, dict) else data
        if not rows:
            raise SystemExit(f"Instrument not found for {symbol} ({category}): {data}")
        row = rows[0]
        return str(row.get("instrument_id") or row.get("instrumentId"))

    def order(self, symbol: str, side: str, qty: float | None, amount: float | None,
              limit: float | None, category: str, send: bool):
        api = self._connect()
        acct = self.account_id()
        iid = self.instrument_id(symbol, category)

        stock_order = {
            "instrument_id": iid,
            "side": side.upper(),                         # BUY / SELL
            "order_type": "LIMIT" if limit is not None else "MARKET",
            "time_in_force": "GTC",                        # crypto trades 24/7
        }
        if limit is not None:
            stock_order["limit_price"] = str(limit)
        if amount is not None:
            stock_order["amount"] = str(amount)            # notional (cash) order
            stock_order["entrust_type"] = "CASH"
        else:
            stock_order["qty"] = str(qty)
            stock_order["entrust_type"] = "QTY"

        if not send:
            # Validate without submitting.
            try:
                preview = self._json(api.order_v2.preview_order(acct, [stock_order]))
                return {"mode": "preview", "order": stock_order, "preview": preview}
            except Exception as exc:  # noqa: BLE001
                return {"mode": "preview-unavailable", "order": stock_order, "note": str(exc)}
        resp = self._json(api.order.place_order_v2(acct, stock_order))
        return {"mode": "sent", "order": stock_order, "response": resp}


# --------------------------------------------------------------------------
def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str) if isinstance(obj, (dict, list)) else obj)


def _last_price(snapshot) -> str | None:
    rows = snapshot.get("data") if isinstance(snapshot, dict) else snapshot
    if isinstance(rows, list) and rows:
        r = rows[0]
        for k in ("close", "price", "last", "lastPrice", "trade_price", "tradePrice"):
            if r.get(k) not in (None, ""):
                return str(r[k])
    return None


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Official Webull OpenAPI client.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest", help="verify credentials, account, and a BTC quote")
    sub.add_parser("accounts", help="list accounts and balances")

    q = sub.add_parser("quote", help="latest snapshot for a symbol")
    q.add_argument("symbol")
    q.add_argument("--category", default="CRYPTO")

    for name in ("buy", "sell"):
        o = sub.add_parser(name, help=f"{name} an instrument (PREVIEW unless --send)")
        o.add_argument("symbol")
        o.add_argument("--category", default="CRYPTO")
        g = o.add_mutually_exclusive_group()
        g.add_argument("--qty", type=float, help="quantity of the asset")
        g.add_argument("--amount", type=float, help="cash notional instead of qty")
        o.add_argument("--limit", type=float, default=None, help="limit price (else market)")
        o.add_argument("--send", action="store_true", help="actually submit (default: preview only)")
        o.add_argument("--yes", action="store_true", help="skip confirmation with --send")

    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    wb = Webull()

    try:
        if args.cmd == "selftest":
            print("• accounts:")
            _print(wb.accounts())
            print("\n• balance:")
            _print(wb.balance())
            print("\n• BTCUSD snapshot:")
            snap = wb.snapshot("BTCUSD", "CRYPTO")
            _print(snap)
            price = _last_price(snap)
            print(f"\n✔ Connected. BTC last ≈ {price}" if price else "\n✔ Connected.")
            return 0

        if args.cmd == "accounts":
            _print(wb.accounts())
            return 0

        if args.cmd == "quote":
            snap = wb.snapshot(args.symbol, args.category)
            _print(snap)
            price = _last_price(snap)
            if price:
                print(f"\n{args.symbol} last ≈ {price}")
            return 0

        if args.cmd in ("buy", "sell"):
            if args.qty is None and args.amount is None:
                print("Specify --qty or --amount.", file=sys.stderr)
                return 2
            if args.send and not args.yes:
                ans = input(f"Really SEND a live {args.cmd.upper()} {args.symbol}? (yes/no): ")
                if ans.strip().lower() not in ("y", "yes"):
                    print("Cancelled.")
                    return 1
            result = wb.order(args.symbol, args.cmd, args.qty, args.amount,
                              args.limit, args.category, args.send)
            _print(result)
            if result.get("mode") == "preview":
                print("\n[PREVIEW] Not submitted. Add --send --yes to place it for real.")
            return 0
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
