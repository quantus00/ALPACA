"""Flask web app: landing page, accounts, billing, dashboard, API, admin."""
from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import time
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import billing, db
from .delivery import DISCORD_RE, TELEGRAM_CHAT_RE, deliver
from .settings import INSTRUMENT_LABELS, Settings

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TV_ACTIONS = {"buy": ("entry", "BUY"), "sell": ("entry", "SELL")}
TV_TRENDS = {"UPTREND", "DOWNTREND"}


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings()
    settings.validate()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=settings.secure_cookies,
        MAX_CONTENT_LENGTH=256 * 1024,
    )
    app.extensions["saas_settings"] = settings

    # ---- per-request plumbing ---------------------------------------------
    def get_db():
        if "db" not in g:
            g.db = db.connect(settings.db_path)
        return g.db

    @app.teardown_appcontext
    def _close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def current_user():
        if "user" not in g:
            uid = session.get("uid")
            g.user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() \
                if uid else None
        return g.user

    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @app.before_request
    def _check_csrf():
        exempt = {"stripe_webhook", "ingest_tradingview", "api_signals", "health"}
        if request.method == "POST" and request.endpoint not in exempt:
            sent = request.form.get("csrf", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Invalid form token — reload the page and try again.")

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.context_processor
    def _inject():
        return {"brand": settings.brand, "user": current_user(), "csrf_token": csrf_token,
                "has_access": db.has_access(current_user()), "settings": settings,
                "labels": INSTRUMENT_LABELS}

    @app.template_filter("ts")
    def _ts(value):
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(value)) if value else "—"

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user():
                return redirect(url_for("login", next=request.path))
            return fn(*a, **kw)
        return wrapper

    def admin_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if not u or not u["is_admin"]:
                abort(404)
            return fn(*a, **kw)
        return wrapper

    # ---- public pages --------------------------------------------------------
    @app.get("/health")
    def health():
        get_db().execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/")
    def landing():
        return render_template("landing.html", instruments=settings.instruments)

    @app.get("/terms")
    def terms():
        return render_template("terms.html")

    # ---- accounts ------------------------------------------------------------
    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if not EMAIL_RE.match(email):
                flash("Enter a valid email address.", "error")
            elif len(password) < 10:
                flash("Use a password of at least 10 characters.", "error")
            elif not request.form.get("accept_terms"):
                flash("You must accept the terms and risk disclosure.", "error")
            else:
                conn = get_db()
                try:
                    cur = conn.execute(
                        "INSERT INTO users (email, password_hash, created_at, is_admin, api_token)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (email, generate_password_hash(password), int(time.time()),
                         int(email in settings.admin_emails), secrets.token_urlsafe(32)))
                    conn.commit()
                except Exception:  # noqa: BLE001 - unique email
                    flash("An account with that email already exists.", "error")
                else:
                    session.clear()
                    session["uid"] = cur.lastrowid
                    return redirect(url_for("billing_page"))
        return render_template("auth.html", mode="signup")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            row = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                session.clear()
                session["uid"] = row["id"]
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//")
                                else url_for("dashboard"))
            flash("Wrong email or password.", "error")
        return render_template("auth.html", mode="login")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("landing"))

    # ---- billing -------------------------------------------------------------
    @app.get("/billing")
    @login_required
    def billing_page():
        return render_template("billing.html", checkout=request.args.get("checkout"))

    @app.post("/billing/checkout")
    @login_required
    def billing_checkout():
        if not settings.billing_enabled:
            flash("Billing is not configured yet.", "error")
            return redirect(url_for("billing_page"))
        try:
            return redirect(billing.create_checkout_session(settings, current_user()), 303)
        except billing.BillingError as exc:
            flash(str(exc), "error")
            return redirect(url_for("billing_page"))

    @app.post("/billing/portal")
    @login_required
    def billing_portal():
        try:
            return redirect(billing.create_portal_session(settings, current_user()), 303)
        except billing.BillingError as exc:
            flash(str(exc), "error")
            return redirect(url_for("billing_page"))

    @app.post("/stripe/webhook")
    def stripe_webhook():
        payload = request.get_data()
        if not billing.verify_signature(payload, request.headers.get("Stripe-Signature", ""),
                                        settings.stripe_webhook_secret):
            return {"ok": False, "error": "bad signature"}, 400
        outcome = billing.handle_event(get_db(), json.loads(payload))
        return {"ok": True, "outcome": outcome}

    # ---- subscriber area -----------------------------------------------------
    @app.get("/dashboard")
    @login_required
    def dashboard():
        if not db.has_access(current_user()):
            return redirect(url_for("billing_page"))
        conn = get_db()
        return render_template("dashboard.html", trends=db.latest_trends(conn),
                               signals=db.recent_signals(conn, 50),
                               instruments=settings.instruments)

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def alert_settings():
        u = current_user()
        if request.method == "POST":
            if request.form.get("action") == "rotate_token":
                get_db().execute("UPDATE users SET api_token=? WHERE id=?",
                                 (secrets.token_urlsafe(32), u["id"]))
                get_db().commit()
                flash("API token rotated.", "ok")
                return redirect(url_for("alert_settings"))
            discord = request.form.get("discord_webhook", "").strip()
            chat = request.form.get("telegram_chat_id", "").strip()
            wanted = [i for i in request.form.getlist("instruments") if i in settings.instruments]
            if discord and not DISCORD_RE.match(discord):
                flash("That isn't a Discord webhook URL (https://discord.com/api/webhooks/…).",
                      "error")
            elif chat and not TELEGRAM_CHAT_RE.match(chat):
                flash("Telegram chat id should be a number, e.g. 123456789.", "error")
            else:
                get_db().execute(
                    "UPDATE users SET discord_webhook=?, telegram_chat_id=?, instruments=?"
                    " WHERE id=?", (discord, chat, ",".join(wanted), u["id"]))
                get_db().commit()
                flash("Alert settings saved.", "ok")
                return redirect(url_for("alert_settings"))
        chosen = [i for i in (u["instruments"] or "").split(",") if i]
        return render_template("settings.html", chosen=chosen, instruments=settings.instruments)

    @app.post("/settings/test")
    @login_required
    def test_alert():
        u = current_user()
        if not db.has_access(u):
            return redirect(url_for("billing_page"))
        n = deliver({"instrument": settings.instruments[0], "kind": "trend",
                     "direction": "TEST ALERT", "price": 0, "detail": "Your alerts are working."},
                    [u], settings.brand, settings.telegram_bot_token)
        flash(f"Test alert sent to {n} channel(s)." if n else
              "No alert channels configured yet.", "ok" if n else "error")
        return redirect(url_for("alert_settings"))

    # ---- API ------------------------------------------------------------------
    @app.get("/api/signals")
    def api_signals():
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        u = get_db().execute("SELECT * FROM users WHERE api_token=?", (token,)).fetchone() \
            if token else None
        if not db.has_access(u):
            return {"error": "invalid token or inactive subscription"}, 401
        limit = max(1, min(request.args.get("limit", 50, type=int), 500))
        return jsonify(db.recent_signals(get_db(), limit))

    @app.post("/ingest/tradingview")
    def ingest_tradingview():
        """Accepts the Pine Script alert payload (or a bare UPTREND/DOWNTREND body)."""
        supplied = request.headers.get("X-Webhook-Secret") or request.args.get("key", "")
        if not settings.ingest_secret or not hmac.compare_digest(supplied, settings.ingest_secret):
            return {"ok": False, "error": "unauthorized"}, 401
        raw = request.get_data(as_text=True).strip()
        instrument = request.args.get("instrument", settings.instruments[0])
        if instrument not in INSTRUMENT_LABELS:
            return {"ok": False, "error": "unknown instrument"}, 400
        price = 0.0
        if raw in TV_TRENDS:
            kind, direction = "trend", raw
        else:
            try:
                payload = json.loads(raw)
                kind, direction = TV_ACTIONS[str(payload.get("action", "")).lower()]
                price = float(payload.get("price", 0) or 0)
            except (ValueError, KeyError, TypeError, AttributeError):
                return {"ok": False, "error": "bad payload"}, 400
        conn = get_db()
        sig = db.insert_signal(conn, instrument, kind, direction, price, "", "tradingview")
        queued = deliver(sig, db.subscribers_for(conn, instrument), settings.brand,
                         settings.telegram_bot_token)
        return {"ok": True, "signal_id": sig["id"], "deliveries": queued}

    # ---- admin ------------------------------------------------------------------
    @app.route("/admin", methods=["GET", "POST"])
    @admin_required
    def admin():
        conn = get_db()
        if request.method == "POST":
            uid = request.form.get("user_id", type=int)
            if request.form.get("action") == "toggle_comp" and uid:
                conn.execute("UPDATE users SET comped = 1 - comped WHERE id=?", (uid,))
                conn.commit()
            return redirect(url_for("admin"))
        users = conn.execute("SELECT * FROM users ORDER BY id DESC").fetchall()
        paying = sum(1 for u in users if u["subscription_status"] in db.ACTIVE_STATUSES)
        return render_template("admin.html", users=users, paying=paying,
                               signals=db.recent_signals(conn, 20))

    return app
