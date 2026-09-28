"""Flatten ALL live spot positions on a broker — sell every non-stablecoin
balance back to USD. Coinbase only for now.

SAFETY: dry-run by default. It prints the plan and sells NOTHING unless you pass
``--yes``. Dust below the product's minimum order size is skipped (Coinbase
rejects it) — clear those with Coinbase "Convert". Only ever SELLS; never buys.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation

from .config import Config

log = logging.getLogger("close_all")

# Never "close" cash / stablecoins.
STABLES = {"USD", "USDC", "USDT", "DAI", "PYUSD", "GUSD", "USDP"}


def _dec(x) -> Decimal:
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def coinbase_positions(client) -> list[tuple[str, Decimal]]:
    """[(currency, available_amount)] for every non-stablecoin with a balance."""
    resp = client.get_accounts(limit=250)
    data = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {}) or {}
    out: list[tuple[str, Decimal]] = []
    for a in (data.get("accounts", []) if isinstance(data, dict) else []):
        cur = a.get("currency")
        ab = a.get("available_balance", {})
        val = ab.get("value") if isinstance(ab, dict) else ab
        amt = _dec(val)
        if cur and cur.upper() not in STABLES and amt > 0:
            out.append((cur, amt))
    return out


def _product(client, product_id: str) -> dict:
    p = client.get_product(product_id)
    return p if isinstance(p, dict) else getattr(p, "__dict__", {}) or {}


def _quantize_down(amount: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        return amount
    return (amount // increment) * increment


def plan_and_close(cfg: Config, execute: bool = False, min_usd: float = 0.0,
                   only: list[str] | None = None) -> list[dict]:
    """Build (and optionally send) market-SELL-to-USD orders for open positions.

    Returns the list of planned/sent orders as dicts. ``execute=False`` (default)
    is a dry run that sends nothing.
    """
    from .brokers.coinbase import CoinbaseBroker
    broker = CoinbaseBroker(cfg)
    client = broker._get_client()  # noqa: SLF001 - reuse the broker's auth

    positions = coinbase_positions(client)
    if only:
        want = {s.strip().upper() for s in only}
        positions = [(c, a) for c, a in positions if c.upper() in want]

    print(f"{'PRODUCT':<12}{'SIZE':>24}{'~USD':>11}  ACTION")
    to_sell: list[dict] = []
    for cur, amt in positions:
        product_id = f"{cur}-USD"
        try:
            p = _product(client, product_id)
        except Exception as exc:  # noqa: BLE001
            print(f"{product_id:<12}{str(amt):>24}{'':>11}  skip: no product ({exc})")
            continue
        inc = _dec(p.get("base_increment") or "0.00000001")
        min_size = _dec(p.get("base_min_size") or inc)
        price = _dec(p.get("price") or 0)
        qty = _quantize_down(amt, inc)
        usd = qty * price
        if qty <= 0 or qty < min_size or (min_usd > 0 and usd < _dec(min_usd)):
            print(f"{product_id:<12}{str(qty):>24}{float(usd):>11.2f}  skip: dust (<min) — use Convert")
            continue
        print(f"{product_id:<12}{str(qty):>24}{float(usd):>11.2f}  SELL")
        to_sell.append({"product_id": product_id, "size": qty, "usd": float(usd)})

    if not to_sell:
        print("\nNothing sellable (all dust or no positions).")
        return []

    if not execute:
        print(f"\nDRY-RUN: {len(to_sell)} SELL order(s) NOT sent. "
              f"Re-run with --yes to execute.")
        return to_sell

    print(f"\nEXECUTING {len(to_sell)} market SELL order(s) to USD...")
    for o in to_sell:
        try:
            resp = client.create_order(
                client_order_id=str(uuid.uuid4()),
                product_id=o["product_id"], side="SELL",
                order_configuration={"market_market_ioc": {"base_size": format(o["size"], "f")}})
            data = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {}) or {}
            oid = (data.get("order_id")
                   or data.get("success_response", {}).get("order_id"))
            ok = bool(data.get("success", oid is not None))
            o["ok"], o["order_id"] = ok, oid
            print(f"  {o['product_id']:<12} {'OK ' if ok else 'FAIL'} {oid or data}")
        except Exception as exc:  # noqa: BLE001
            o["ok"], o["error"] = False, str(exc)
            print(f"  {o['product_id']:<12} ERROR {exc}")
    return to_sell
