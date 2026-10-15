"""Discover live Coinbase derivatives products and portfolios.

Expiring futures roll monthly, so their date-coded IDs go stale. Instead of
hardcoding, this resolves everything from the API:

  * Product IDs + contract sizes: GET /products?product_type=FUTURE
  * INTX portfolio_uuid:          GET /portfolios (type == INTX)

Naming (Coinbase Derivatives, US expiring): ROOT-DDMMMYY-CDE
  BIT = nano Bitcoin, BIP = nano BTC perp-style, GOL = gold, NOL = oil.
INTX perps: BTC-PERP (app shows BTC-PERP-INTX).

CLI:  icc-discover        (or: python -m icc_bot.discover)
prints the same table as a hand-rolled ids.py, using your configured creds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from .brokers.coinbase import _attr, _make_client

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def parse_expiry(product_id: str) -> Optional[datetime]:
    """Parse the expiry datetime from a ROOT-DDMMMYY-CDE product id, else None."""
    parts = product_id.split("-")
    if len(parts) != 3:
        return None
    d = parts[1].upper()
    if len(d) != 7:
        return None
    try:
        day = int(d[0:2]); mon = _MONTHS[d[2:5]]; yr = 2000 + int(d[5:7])
        return datetime(yr, mon, day, tzinfo=timezone.utc)
    except (ValueError, KeyError):
        return None


def pick_front_month(products: List[dict], root: str,
                     now: Optional[datetime] = None) -> Optional[dict]:
    """From a FUTURE product list, return the nearest not-yet-expired contract
    for `root` (e.g. 'GOL'), as {product_id, contract_size, ...}. None if absent."""
    now = now or datetime.now(timezone.utc)
    root = root.upper()
    candidates = []
    for p in products:
        pid = _attr(p, "product_id") or ""
        if not pid.upper().startswith(root + "-"):
            continue
        exp = parse_expiry(pid)
        if exp is None or exp < now:
            continue
        candidates.append((exp, p))
    if not candidates:
        return None
    _, best = min(candidates, key=lambda t: t[0])
    d = _attr(best, "future_product_details", default={}) or {}
    return {
        "product_id": _attr(best, "product_id"),
        "contract_size": _attr(d, "contract_size"),
        "unit": _attr(d, "contract_root_unit"),
        "display": _attr(d, "contract_display_name"),
    }


def perp_multiplier(symbol: str) -> float:
    """INTX perps are 1 per contract, except the 1000XXX-PERP products (1000)."""
    s = symbol.upper()
    return 1000.0 if s.startswith("1000") else 1.0


# -- live API wrappers -------------------------------------------------------
def list_futures(client) -> List[dict]:
    resp = client.get_products(product_type="FUTURE")
    return _attr(resp, "products", default=[]) or []


def list_portfolios(client) -> List[dict]:
    resp = client.get_portfolios()
    return _attr(resp, "portfolios", default=[]) or []


def intx_portfolio_uuid(client) -> Optional[str]:
    for p in list_portfolios(client):
        if (_attr(p, "type") or "").upper() == "INTX":
            return _attr(p, "uuid")
    return None


def print_ids(client) -> None:
    print("=== PORTFOLIOS ===")
    for p in list_portfolios(client):
        print(f"{(_attr(p,'type') or ''):10} {_attr(p,'uuid')}  {_attr(p,'name') or ''}")
    print("\n=== FUTURES ===")
    for p in list_futures(client):
        d = _attr(p, "future_product_details", default={}) or {}
        print(f"{(_attr(p,'product_id') or ''):22} "
              f"size={str(_attr(d,'contract_size')):>10} "
              f"unit={str(_attr(d,'contract_root_unit')):<6} "
              f"exp={str(_attr(d,'contract_expiry_type')):<10} "
              f"{_attr(d,'contract_display_name') or ''}")


def main() -> None:
    print_ids(_make_client())


if __name__ == "__main__":
    main()
