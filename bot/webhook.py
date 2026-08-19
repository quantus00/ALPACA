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


# The dashboard page served at "/". The button opens both URLs as two separate
# windows from a single user click (distinct window names => two windows, not
# one), staggered so they open one after the other rather than at the same time.
DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trend Bot — Trader App</title>
<style>
  body{{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#0d1117;color:#e6edf3;
       margin:0;display:flex;min-height:100vh;align-items:center;justify-content:center}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:14px;padding:32px 36px;
       max-width:520px;width:90%;box-shadow:0 10px 40px rgba(0,0,0,.5)}}
  h1{{margin:0 0 4px;font-size:20px}}
  .sub{{color:#8b949e;font-size:13px;margin-bottom:22px}}
  .btn{{display:block;width:100%;box-sizing:border-box;text-align:center;cursor:pointer;
       border:0;border-radius:10px;padding:16px;font-size:16px;font-weight:600;
       background:#238636;color:#fff;margin-bottom:14px}}
  .btn:hover{{background:#2ea043}}
  .links{{display:flex;gap:10px}}
  .links a{{flex:1;text-align:center;color:#58a6ff;text-decoration:none;font-size:13px;
       border:1px solid #30363d;border-radius:8px;padding:10px}}
  .cfg{{margin-top:18px;font-size:12px;color:#8b949e;word-break:break-word}}
  .note{{margin-top:10px;font-size:11px;color:#6e7681}}
</style></head><body>
  <div class="card">
    <h1>📈 Trend Bot — {symbol}</h1>
    <div class="sub">Multi-timeframe trend / structure bot</div>
    <button class="btn" onclick="openBoth()">Open trading windows ↗↗</button>
    <div class="links">
      <a href="{w1}" target="win1">{t1} only</a>
      <a href="{w2}" target="win2">{t2} only</a>
    </div>
    <div class="cfg">{desc}</div>
    <div class="note">Opens the two windows one after the other. Allow pop-ups for this site so both can open.</div>
  </div>
<script>
// Delay (ms) between opening the first and the second window, so they do NOT
// open at the same instant.
var OPEN_STAGGER_MS = 700;
function openBoth() {{
  var sw = screen.availWidth || 1280, sh = screen.availHeight || 800;
  var w = Math.floor(sw / 2) - 20, h = sh - 80;
  // Two distinct window names => two separate OS windows, placed side by side.
  // Open the first now, then the second after a short delay (not simultaneously).
  window.open("{w1}", "win1", "width="+w+",height="+h+",left=0,top=0,noopener");
  setTimeout(function() {{
    window.open("{w2}", "win2", "width="+w+",height="+h+",left="+(w+20)+",top=0,noopener");
  }}, OPEN_STAGGER_MS);
}}
</script>
</body></html>"""


def create_app(cfg: Config, notifier: Notifier | None = None) -> Flask:
    app = Flask(__name__)
    notifier = notifier or Notifier()
    broker = get_broker(cfg)

    @app.get("/health")
    def health():
        return {"status": "ok", "config": cfg.describe()}

    @app.get("/")
    def dashboard():
        # Minimal "trader app" page. The link opens TWO separate browser windows
        # (window1_url and window2_url) via distinct window names + geometry, so
        # they land side by side instead of as tabs in one window.
        return DASHBOARD_HTML.format(
            w1=cfg.window1_url, w2=cfg.window2_url,
            t1=cfg.window1_title, t2=cfg.window2_title,
            desc=cfg.describe(), symbol=cfg.symbol(),
        )

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
