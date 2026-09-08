#!/usr/bin/env python3
"""cb_trade.py — place a Coinbase market order (paper by default, or --live).

This is the "bot-runner" order script the Trader app calls from
Server ▸ Open Terminal. Paper mode simulates against the live public price and
needs no keys; --live places a real order via the Coinbase Advanced Trade API.

    # paper (no keys needed) — what $5 of BTC would buy right now:
    python cb_trade.py buy --product BTC-USD --usd 5

    # live real-money order (needs keys + a funded account):
    python cb_trade.py buy --product BTC-USD --usd 5 --live
    python cb_trade.py sell --product BTC-USD --base 0.0001 --live --yes

Live credentials (env or .env):
    COINBASE_API_KEY     Coinbase Advanced / CDP key name
    COINBASE_API_SECRET  its EC private key (PEM)

Notes
    • Coinbase is crypto-only: there is NO gold on Coinbase, so a "gold perp"
      product is not listed and will error. Use BTC-USD, ETH-USD, etc.
    • BTC perps (…-PERP-INTX) need a Coinbase INTX account (not US retail).
    • Live orders are REAL money and effectively irreversible. Paper first.
Not financial advice.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import requests

PUBLIC_SPOT = "https://api.coinbase.com/v2/prices/{product}/spot"


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def public_price(product: str) -> float:
    """Latest spot price from Coinbase's public endpoint (no auth)."""
    try:
        r = requests.get(PUBLIC_SPOT.format(product=product), timeout=15)
    except requests.RequestException as exc:
        raise SystemExit(f"Couldn't reach Coinbase: {exc}") from exc
    if r.status_code == 404:
        raise SystemExit(f"'{product}' is not a Coinbase product (not listed). "
                         "Coinbase has no gold — try BTC-USD, ETH-USD, SOL-USD…")
    r.raise_for_status()
    return float(r.json()["data"]["amount"])


def live_client():
    try:
        from coinbase.rest import RESTClient  # type: ignore
    except ImportError as exc:
        raise SystemExit("Live needs the SDK:  pip install coinbase-advanced-py") from exc
    key, secret = os.getenv("COINBASE_API_KEY"), os.getenv("COINBASE_API_SECRET")
    if not key or not secret:
        raise SystemExit("Live needs COINBASE_API_KEY and COINBASE_API_SECRET (env or .env).")
    return RESTClient(api_key=key, api_secret=secret)


def place_live(product: str, side: str, usd: float | None, base: float | None) -> dict:
    client = live_client()
    if usd is not None:
        # Market BUY sized in USD (quote_size). Sells must be base-sized.
        if side == "sell":
            raise SystemExit("Use --base for a sell (quote_size is buy-only).")
        order_cfg = {"market_market_ioc": {"quote_size": f"{usd:.2f}"}}
    else:
        order_cfg = {"market_market_ioc": {"base_size": str(base)}}
    resp = client.create_order(client_order_id=str(uuid.uuid4()), product_id=product,
                               side=side.upper(), order_configuration=order_cfg)
    return resp if isinstance(resp, dict) else getattr(resp, "__dict__", {"resp": str(resp)})


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Coinbase market order (paper or live).")
    ap.add_argument("side", choices=["buy", "sell"])
    ap.add_argument("--product", default="BTC-USD", help="Coinbase product id, e.g. BTC-USD")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--usd", type=float, help="order size in USD (buy only)")
    g.add_argument("--base", type=float, help="order size in the base asset (e.g. BTC)")
    ap.add_argument("--live", action="store_true", help="place a REAL order (default: paper)")
    ap.add_argument("--yes", action="store_true", help="skip the live confirmation prompt")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if a.usd is None and a.base is None:
        a.usd = 5.0  # matches the app default; buy $5

    price = public_price(a.product)
    if a.usd is not None:
        qty, usd = a.usd / price, a.usd
    else:
        qty, usd = a.base, a.base * price
    print(f"{a.product} ≈ ${price:,.2f}  →  {a.side.upper()} ~{qty:.8f} "
          f"({a.product.split('-')[0]}) ≈ ${usd:,.2f}")

    if not a.live:
        print("[PAPER] simulated only — no order sent. Add --live to trade for real.")
        return 0

    if not a.yes:
        ans = input(f"SEND LIVE {a.side.upper()} ${usd:,.2f} of {a.product}? (yes/no): ")
        if ans.strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1
    try:
        resp = place_live(a.product, a.side, a.usd, a.base)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"Live order failed: {exc}", file=sys.stderr)
        return 1
    ok = bool(resp.get("success", True)) if isinstance(resp, dict) else True
    print(("✔ live order sent" if ok else "✖ order rejected"))
    print(json.dumps(resp, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
