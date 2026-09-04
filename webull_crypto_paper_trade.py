#!/usr/bin/env python3
"""Place a Webull **paper** crypto trade from your terminal or VS Code.

You choose the crypto and the amount; this script logs into your Webull
*paper* account (never the live one) and submits a market order for it.

Webull has no official public trading API, so this uses the community
``webull`` package (https://github.com/tejtw/webull) and its ``paper_webull``
client. Because it is unofficial it can break when Webull changes their
endpoints; treat it accordingly and keep it on the paper account.

--------------------------------------------------------------------------
Quick start
--------------------------------------------------------------------------
    pip install webull requests
    cp .env.example .env          # then fill in WEBULL_* values

    # Simulate first (no login, no order actually sent):
    python webull_crypto_paper_trade.py --crypto BTC --amount 100 --dry-run

    # Real paper order — $100 of Bitcoin:
    python webull_crypto_paper_trade.py --crypto BTC --amount 100

    # By quantity instead of dollars — 0.25 ETH, sell side:
    python webull_crypto_paper_trade.py --crypto ETH --qty 0.25 --side sell

    # No flags? It asks you interactively:
    python webull_crypto_paper_trade.py

Credentials are read from the environment (or a local ``.env`` file):
    WEBULL_EMAIL      your Webull login email (or phone)
    WEBULL_PASSWORD   your Webull password
    WEBULL_TRADE_PIN  your 6-digit trading PIN
    WEBULL_DEVICE_ID  optional; a remembered device id to skip MFA

Nothing here is financial advice. Paper trading only.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# --------------------------------------------------------------------------
# .env loading (no hard dependency on python-dotenv)
# --------------------------------------------------------------------------
def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Populate ``os.environ`` from a simple KEY=VALUE ``.env`` file.

    Existing environment variables win, so real exported vars are never
    overwritten. Lines starting with ``#`` and blank lines are ignored.
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def normalize_symbol(crypto: str) -> str:
    """Turn user input like 'btc', 'BTC-USD', 'BTC/USD' into 'BTCUSD'.

    Webull crypto tickers are of the form ``BTCUSD``, ``ETHUSD``, etc.
    """
    s = crypto.strip().upper().replace("-", "").replace("/", "").replace(" ", "")
    if not s:
        raise ValueError("empty crypto symbol")
    if not s.endswith("USD"):
        s += "USD"
    return s


def prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{text}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or (default or "")


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Place a Webull paper crypto trade.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--crypto", "--symbol", dest="crypto",
                    help="Crypto to trade, e.g. BTC, ETH, DOGE, BTCUSD.")
    ap.add_argument("--side", choices=["buy", "sell"], default="buy",
                    help="Order side.")

    amt = ap.add_mutually_exclusive_group()
    amt.add_argument("--amount", type=float,
                     help="Dollar amount to trade (e.g. 100 = $100 of the coin).")
    amt.add_argument("--qty", type=float,
                     help="Quantity of the coin to trade instead of a dollar amount.")

    ap.add_argument("--limit", type=float, default=None,
                    help="Limit price. Omit for a market order.")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Skip the confirmation prompt.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Simulate only: validate + preview, never log in or send.")
    return ap.parse_args(argv)


def resolve_order(args: argparse.Namespace) -> tuple[str, str, str, float, float | None]:
    """Return (symbol, side, entrust_type, quant, limit) filling gaps via prompts."""
    crypto = args.crypto or prompt("Which crypto? (BTC, ETH, DOGE, ...)", "BTC")
    symbol = normalize_symbol(crypto)

    side = args.side
    if not args.crypto:  # fully interactive session — ask for side too
        side = prompt("Side (buy/sell)", side).lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be buy or sell, got {side!r}")

    amount, qty = args.amount, args.qty
    if amount is None and qty is None:
        raw = prompt("Amount in USD (or prefix a number with 'q' for quantity)", "100")
        if raw.lower().startswith("q"):
            qty = float(raw[1:].strip())
        else:
            amount = float(raw)

    if amount is not None:
        if amount <= 0:
            raise ValueError("amount must be > 0")
        return symbol, side, "CASH", float(amount), args.limit
    if qty is not None and qty <= 0:
        raise ValueError("qty must be > 0")
    return symbol, side, "QTY", float(qty), args.limit


# --------------------------------------------------------------------------
# Webull paper trading
# --------------------------------------------------------------------------
def place_paper_order(symbol: str, side: str, entrust_type: str,
                      quant: float, limit: float | None) -> dict:
    """Log into the Webull paper account and submit the crypto order."""
    try:
        from webull import paper_webull  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on env
        raise SystemExit(
            "The 'webull' package is not installed. Run:\n\n"
            "    pip install webull\n"
        ) from exc

    email = os.getenv("WEBULL_EMAIL")
    password = os.getenv("WEBULL_PASSWORD")
    pin = os.getenv("WEBULL_TRADE_PIN")
    missing = [n for n, v in
               (("WEBULL_EMAIL", email), ("WEBULL_PASSWORD", password),
                ("WEBULL_TRADE_PIN", pin)) if not v]
    if missing:
        raise SystemExit(
            "Missing credentials: " + ", ".join(missing) +
            "\nSet them in your environment or a .env file (see .env.example)."
        )

    wb = paper_webull()
    if os.getenv("WEBULL_DEVICE_ID"):
        wb._did = os.environ["WEBULL_DEVICE_ID"]

    print("Logging into Webull (paper account)...")
    wb.login(email, password)
    wb.get_trade_token(pin)

    # Show the quote so you know what you're paying.
    price = None
    try:
        quote = wb.get_crypto_quote(stock=symbol)
        price = float(quote.get("close") or quote.get("pPrice") or 0) or None
    except Exception:  # noqa: BLE001 - quote is best-effort context only
        pass
    if price:
        print(f"Current {symbol} price ~ ${price:,.2f}")
        if entrust_type == "CASH":
            print(f"  ${quant:,.2f} ≈ {quant / price:.8f} {symbol[:-3]}")

    order_type = "LMT" if limit is not None else "MKT"
    print(f"Submitting PAPER {order_type} {side.upper()} {symbol} "
          f"({'$' if entrust_type == 'CASH' else ''}{quant}"
          f"{'' if entrust_type == 'CASH' else ' units'})...")

    resp = wb.place_order_crypto(
        stock=symbol,
        price=limit if limit is not None else 0,
        action=side.upper(),
        orderType=order_type,
        enforce="GTC",              # crypto trades 24/7; GTC is the safe default
        entrust_type=entrust_type,  # CASH = dollar amount, QTY = coin quantity
        quant=quant,
    )
    return resp if isinstance(resp, dict) else {"result": resp}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        symbol, side, entrust_type, quant, limit = resolve_order(args)
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    order_type = "limit @ " + f"{limit}" if limit is not None else "market"
    size_desc = (f"${quant:,.2f}" if entrust_type == "CASH"
                 else f"{quant} {symbol[:-3]}")
    print("\n" + "=" * 52)
    print(f"  Webull PAPER trade")
    print(f"  {side.upper()}  {size_desc}  of  {symbol}  ({order_type})")
    print("=" * 52)

    if args.dry_run:
        print("\n[DRY RUN] Nothing was sent. Re-run without --dry-run to place it.")
        return 0

    if not args.yes:
        if prompt("\nSend this paper order? (yes/no)", "no").lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1

    try:
        resp = place_paper_order(symbol, side, entrust_type, quant, limit)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"\nOrder failed: {exc}", file=sys.stderr)
        return 1

    order_id = resp.get("orderId") or resp.get("result")
    if resp.get("success") is False or (order_id is None and "msg" in resp):
        print(f"\nWebull rejected the order: {resp.get('msg', resp)}", file=sys.stderr)
        return 1

    print("\nPaper order submitted ✔")
    if order_id:
        print(f"  order id: {order_id}")
    print(f"  response: {resp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
