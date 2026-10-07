"""Tests for the subscription signal service (saas/)."""
import hashlib
import hmac
import json
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

flask = pytest.importorskip("flask")

from bot.trend import Trend  # noqa: E402
from saas import billing, db, delivery, engine  # noqa: E402
from saas.app import create_app  # noqa: E402
from saas.settings import Settings  # noqa: E402

WH_SECRET = "whsec_test"


@pytest.fixture
def settings(tmp_path):
    return Settings(secret_key="x" * 40, db_path=str(tmp_path / "t.db"),
                    admin_emails=("boss@example.com",), secure_cookies=False,
                    stripe_webhook_secret=WH_SECRET, ingest_secret="tv-secret",
                    telegram_bot_token="", instruments=("btc_usd_spot", "mes_futures"))


@pytest.fixture
def client(settings):
    app = create_app(settings)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    sent = []
    monkeypatch.setattr(delivery, "_send_discord", lambda url, text: sent.append((url, text)))
    monkeypatch.setattr(delivery, "_send_telegram", lambda *a: sent.append(a))
    monkeypatch.setattr(delivery, "_pool", _InlinePool())
    return sent


class _InlinePool:
    """Runs deliveries synchronously so tests can assert on them."""

    def submit(self, fn, *args):
        from concurrent.futures import Future
        fut = Future()
        fut.set_result(fn(*args))
        return fut


def _csrf(client, path="/signup"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def _signup(client, email="a@example.com"):
    token = _csrf(client)
    return client.post("/signup", data={"email": email, "password": "correct-horse-1",
                                        "accept_terms": "1", "csrf": token})


def _user(settings, email="a@example.com"):
    return db.connect(settings.db_path).execute(
        "SELECT * FROM users WHERE email=?", (email,)).fetchone()


def _signed(payload: bytes, secret=WH_SECRET, ts=None):
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _stripe_post(client, event):
    body = json.dumps(event).encode()
    return client.post("/stripe/webhook", data=body,
                       headers={"Stripe-Signature": _signed(body)})


def test_settings_require_strong_secret():
    with pytest.raises(ValueError):
        Settings(secret_key="short").validate()


def test_signup_requires_csrf(client):
    r = client.post("/signup", data={"email": "a@example.com", "password": "correct-horse-1",
                                     "accept_terms": "1"})
    assert r.status_code == 400


def test_signup_then_dashboard_gated_until_paid(client, settings):
    r = _signup(client)
    assert r.status_code == 302 and r.headers["Location"].endswith("/billing")
    assert client.get("/dashboard").headers["Location"].endswith("/billing")
    u = _user(settings)
    assert u["subscription_status"] == "none" and u["api_token"]


def test_login_rejects_bad_password_and_open_redirect(client):
    _signup(client)
    client.post("/logout", data={"csrf": _csrf(client, "/billing")})
    token = _csrf(client, "/login")
    r = client.post("/login", data={"email": "a@example.com", "password": "nope", "csrf": token})
    assert "Wrong email or password" in r.get_data(as_text=True)
    r = client.post("/login?next=//evil.com", data={"email": "a@example.com",
                                                    "password": "correct-horse-1", "csrf": token})
    assert r.headers["Location"].endswith("/dashboard")


def test_stripe_webhook_signature(client):
    assert client.post("/stripe/webhook", data=b"{}",
                       headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400
    old = int(time.time()) - 3600
    assert not billing.verify_signature(b"{}", _signed(b"{}", ts=old), WH_SECRET)


def test_stripe_lifecycle_grants_and_revokes_access(client, settings):
    _signup(client)
    uid = _user(settings)["id"]
    r = _stripe_post(client, {"id": "evt_1", "type": "checkout.session.completed", "data": {
        "object": {"mode": "subscription", "client_reference_id": str(uid), "customer": "cus_1"}}})
    assert r.get_json()["outcome"] == "linked"
    assert client.get("/dashboard").status_code == 200

    _stripe_post(client, {"id": "evt_2", "type": "customer.subscription.updated", "data": {
        "object": {"customer": "cus_1", "status": "past_due",
                   "items": {"data": [{"current_period_end": 1900000000}]}}}})
    u = _user(settings)
    assert u["subscription_status"] == "past_due" and u["current_period_end"] == 1900000000
    assert client.get("/dashboard").status_code == 302

    _stripe_post(client, {"id": "evt_3", "type": "customer.subscription.deleted",
                          "data": {"object": {"customer": "cus_1", "status": "canceled"}}})
    assert _user(settings)["subscription_status"] == "canceled"
    # Replays of an already-processed event are ignored.
    assert _stripe_post(client, {"id": "evt_1", "type": "checkout.session.completed",
                                 "data": {"object": {}}}).get_json()["outcome"] == "duplicate"


def _activate(settings, email="a@example.com", discord="https://discord.com/api/webhooks/1/abc"):
    conn = db.connect(settings.db_path)
    conn.execute("UPDATE users SET subscription_status='active', discord_webhook=? WHERE email=?",
                 (discord, email))
    conn.commit()


def test_settings_reject_non_discord_webhook(client, settings):
    _signup(client)
    token = _csrf(client, "/settings")
    r = client.post("/settings", data={"discord_webhook": "http://169.254.169.254/x",
                                       "csrf": token}, follow_redirects=True)
    assert "isn&#39;t a Discord webhook" in r.get_data(as_text=True)
    assert not _user(settings)["discord_webhook"]


def test_tradingview_ingest_fans_out_to_active_subscribers(client, settings, no_network):
    _signup(client)
    _activate(settings)
    assert client.post("/ingest/tradingview", data="UPTREND").status_code == 401
    r = client.post("/ingest/tradingview?instrument=mes_futures",
                    data=json.dumps({"action": "buy", "price": 5800.25}),
                    headers={"X-Webhook-Secret": "tv-secret"})
    body = r.get_json()
    assert body["ok"] and body["deliveries"] == 1
    assert "ENTRY SIGNAL: BUY" in no_network[0][1]
    assert client.post("/ingest/tradingview?key=tv-secret", data="garbage").status_code == 400


def test_api_requires_active_token(client, settings):
    _signup(client)
    client.post("/ingest/tradingview?key=tv-secret", data="DOWNTREND")
    token = _user(settings)["api_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/signals", headers=hdr).status_code == 401
    _activate(settings)
    rows = client.get("/api/signals", headers=hdr).get_json()
    assert rows[0]["direction"] == "DOWNTREND"


def test_admin_only_for_admins_and_can_comp(client, settings):
    _signup(client)
    assert client.get("/admin").status_code == 404
    client.post("/logout", data={"csrf": _csrf(client, "/billing")})
    _signup(client, "boss@example.com")
    assert client.get("/admin").status_code == 200
    uid = _user(settings)["id"]
    client.post("/admin", data={"action": "toggle_comp", "user_id": uid,
                                "csrf": _csrf(client, "/admin")})
    assert db.has_access(_user(settings))


class _FakeRunner:
    def __init__(self, sigs):
        self._last_trend = Trend.NEUTRAL
        self._sigs = list(sigs)

    def evaluate(self):
        s = self._sigs.pop(0)
        if s.trend != self._last_trend:
            self._last_trend = s.trend
        return s


def test_engine_publishes_flips_and_entries_once(settings):
    from bot.strategy import Signal
    conn = db.connect(settings.db_path)
    up = {"5m": "UPTREND", "1h": "UPTREND"}
    runner = _FakeRunner([Signal("none", Trend.UP, 100.0, up), Signal("buy", Trend.UP, 101.0, up),
                          Signal("none", Trend.UP, 102.0, up)])
    runners = {"btc_usd_spot": runner}
    first = engine.tick(conn, settings, runners)
    assert [(s["kind"], s["direction"]) for s in first] == [("trend", "UPTREND")]
    assert [(s["kind"], s["direction"]) for s in engine.tick(conn, settings, runners)] == [
        ("entry", "BUY")]
    assert engine.tick(conn, settings, runners) == []
    assert db.latest_trends(conn)["btc_usd_spot"]["direction"] == "UPTREND"
