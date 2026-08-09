"""Flask webhook that receives TradingView alerts from the Pine Script and
routes them to the configured broker.

TradingView alert body (from the Pine ``f_payload``) looks like:
    {"symbol":"BTCUSD","action":"buy","trend":"UPTREND","price":65000,"time":...}
Plain "UPTREND" / "DOWNTREND" bodies are treated as notifications only.
"""
from __future__ import annotations

import json
import logging

from flask import Flask, request

from .brokers import get_broker
from .config import Config
from .notifier import Notifier

log = logging.getLogger(__name__)


def create_app(cfg: Config, notifier: Notifier | None = None) -> Flask:
    app = Flask(__name__)
    notifier = notifier or Notifier()
    broker = get_broker(cfg)

    @app.get("/health")
    def health():
        return {"status": "ok", "config": cfg.describe()}

    @app.post("/webhook")
    def webhook():
        raw = request.get_data(as_text=True).strip()

        # Optional shared-secret check.
        if cfg.webhook_secret:
            supplied = request.headers.get("X-Webhook-Secret", "")
            if supplied != cfg.webhook_secret:
                return {"ok": False, "error": "unauthorized"}, 401

        # Plain trend notification.
        if raw in ("UPTREND", "DOWNTREND"):
            notifier.notify_trend(raw, cfg.symbol(), 0.0)
            return {"ok": True, "handled": "trend", "trend": raw}

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"bad payload: {raw[:120]}"}, 400

        action = str(payload.get("action", "")).lower()
        price = float(payload.get("price", 0) or 0)
        if action not in ("buy", "sell"):
            return {"ok": False, "error": f"unknown action {action!r}"}, 400

        result = broker.place_order(action, cfg.contract_size)
        if result.ok:
            notifier.notify_entry(action, result.symbol, price, cfg.contract_size)
        else:
            log.error("Order failed: %s", result.error)
        return {"ok": result.ok, "order_id": result.order_id, "error": result.error}

    return app


def run(cfg: Config) -> None:
    app = create_app(cfg)
    log.info("Webhook listening on %s:%s  (%s)", cfg.webhook_host, cfg.webhook_port,
             cfg.describe())
    app.run(host=cfg.webhook_host, port=cfg.webhook_port)
