"""Stripe billing over plain REST (no SDK): Checkout, Customer Portal, webhooks."""
from __future__ import annotations

import hashlib
import hmac
import logging
import sqlite3
import time

import requests

from .settings import Settings

log = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"


class BillingError(RuntimeError):
    pass


def _post(settings: Settings, path: str, data: dict) -> dict:
    resp = requests.post(f"{STRIPE_API}{path}", data=data, timeout=20,
                         auth=(settings.stripe_secret_key, ""))
    body = resp.json()
    if resp.status_code >= 400:
        raise BillingError(body.get("error", {}).get("message", "Stripe request failed"))
    return body


def create_checkout_session(settings: Settings, user: sqlite3.Row) -> str:
    data = {
        "mode": "subscription",
        "line_items[0][price]": settings.stripe_price_id,
        "line_items[0][quantity]": "1",
        "client_reference_id": str(user["id"]),
        "success_url": f"{settings.base_url}/billing?checkout=success",
        "cancel_url": f"{settings.base_url}/billing?checkout=cancelled",
        "allow_promotion_codes": "true",
    }
    if user["stripe_customer_id"]:
        data["customer"] = user["stripe_customer_id"]
    else:
        data["customer_email"] = user["email"]
    if settings.trial_days > 0 and user["subscription_status"] == "none":
        data["subscription_data[trial_period_days]"] = str(settings.trial_days)
    return _post(settings, "/checkout/sessions", data)["url"]


def create_portal_session(settings: Settings, user: sqlite3.Row) -> str:
    if not user["stripe_customer_id"]:
        raise BillingError("No billing account yet — subscribe first.")
    return _post(settings, "/billing_portal/sessions", {
        "customer": user["stripe_customer_id"],
        "return_url": f"{settings.base_url}/billing",
    })["url"]


def verify_signature(payload: bytes, header: str, secret: str, tolerance: int = 300,
                     now: int | None = None) -> bool:
    """Check a ``Stripe-Signature`` header (t=..., v1=...) against the raw body."""
    if not secret or not header:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts["t"][0])
    except (KeyError, ValueError):
        return False
    if abs((now or int(time.time())) - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", []))


def _period_end(sub: dict) -> int | None:
    # Newer API versions moved current_period_end onto the subscription items.
    if sub.get("current_period_end"):
        return int(sub["current_period_end"])
    items = (sub.get("items") or {}).get("data") or []
    if items and items[0].get("current_period_end"):
        return int(items[0]["current_period_end"])
    return None


def handle_event(conn: sqlite3.Connection, event: dict) -> str:
    """Apply a verified Stripe event to the users table. Idempotent per event id."""
    event_id = event.get("id", "")
    if event_id:
        seen = conn.execute("SELECT 1 FROM stripe_events WHERE id=?", (event_id,)).fetchone()
        if seen:
            return "duplicate"
        conn.execute("INSERT INTO stripe_events (id, received_at) VALUES (?, ?)",
                     (event_id, int(time.time())))

    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    outcome = "ignored"

    if etype == "checkout.session.completed" and obj.get("mode") == "subscription":
        user_id = obj.get("client_reference_id")
        customer = obj.get("customer")
        if user_id and customer:
            conn.execute(
                "UPDATE users SET stripe_customer_id=?, subscription_status="
                " CASE WHEN subscription_status IN ('active','trialing')"
                " THEN subscription_status ELSE 'active' END WHERE id=?",
                (customer, int(user_id)))
            outcome = "linked"
    elif etype in ("customer.subscription.created", "customer.subscription.updated",
                   "customer.subscription.deleted"):
        status = "canceled" if etype.endswith("deleted") else obj.get("status", "none")
        conn.execute(
            "UPDATE users SET subscription_status=?, current_period_end=?"
            " WHERE stripe_customer_id=?",
            (status, _period_end(obj), obj.get("customer")))
        outcome = f"status:{status}"

    conn.commit()
    log.info("stripe event %s %s -> %s", event_id, etype, outcome)
    return outcome
