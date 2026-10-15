"""Trader Cockpit — one dashboard for the ICC engine across brokers.

A parallel app to `icc_bot.cockpit` (which is left untouched). Ported from the
Drive "Trading Cockpit", with its broker registry and bot/backtest presets
pointed at ICC.

Run:  icc-trader                       (serves on ICC_TRADER_PORT, default 8788)
Open: http://<ip>:8788/?token=YOURTOKEN
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("icc_bot.trader")

try:
    from fastapi import Body, FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:  # pragma: no cover - only without the [web] extra
    FastAPI = None  # type: ignore

from . import config
from .backtests import runner as backtests
from .bots import runner as bots
from .brokers import registry
from .brokers.base import Order

HERE = os.path.dirname(os.path.abspath(__file__))


def create_app():
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("FastAPI not installed — run: pip install -e '.[web]'")

    app = FastAPI(title="Trader Cockpit", docs_url=None, redoc_url=None)

    def check(token: str) -> None:
        if config.ACCESS_TOKEN and token != config.ACCESS_TOKEN:
            raise HTTPException(status_code=401, detail="bad or missing token")

    if not config.ACCESS_TOKEN:
        log.warning("COCKPIT_TOKEN not set — Trader cockpit is OPEN. Set it for LAN use.")

    # -------- accounts / positions / quote (read-only) --------
    @app.get("/api/accounts")
    def accounts(broker: str = "all", mode: str = "paper", token: str = ""):
        check(token)
        names = registry.names() if broker == "all" else [broker]
        out = []
        for n in names:
            if n not in config.BROKERS:
                continue
            try:
                out.append({"broker": n, "accounts": registry.get_broker(n, mode).get_accounts()})
            except Exception as e:  # noqa - per-broker errors shouldn't crash the page
                out.append({"broker": n, "error": str(e)})
        return out

    @app.get("/api/positions")
    def positions(broker: str, mode: str = "paper", account_id: str = None, token: str = ""):
        check(token)
        try:
            return registry.get_broker(broker, mode).get_positions(account_id)
        except Exception as e:  # noqa
            return JSONResponse({"error": str(e)}, status_code=200)

    @app.get("/api/quote")
    def quote(broker: str, symbol: str, mode: str = "paper", token: str = ""):
        check(token)
        try:
            return registry.get_broker(broker, mode).get_quote(symbol)
        except Exception as e:  # noqa
            return {"error": str(e)}

    # -------- orders (the guarded path) --------
    @app.post("/api/order")
    def order(payload: dict = Body(...), token: str = ""):
        check(token)
        o = Order(broker=payload["broker"], mode=payload.get("mode", "paper"),
                  account_id=payload.get("account_id", ""), symbol=payload["symbol"],
                  side=payload["side"], qty=payload.get("qty"),
                  notional=payload.get("notional"), type=payload.get("type", "market"),
                  price=payload.get("price"), instrument=payload.get("instrument", "stock"),
                  fractional=payload.get("fractional", False))
        dry_run = payload.get("dry_run", True)

        # ---- hard safety gates (live only) ----
        if not dry_run and o.mode == "live":
            if o.account_id in config.OFF_LIMITS and payload.get("override") != o.account_id:
                raise HTTPException(400, f"account {o.account_id} is OFF-LIMITS; refusing.")
            if payload.get("confirm") != "CONFIRM":
                raise HTTPException(400, "live order requires confirm='CONFIRM'.")
            if os.environ.get("ICC_I_UNDERSTAND_LIVE_RISK") != "yes":
                raise HTTPException(403, "live blocked: set ICC_I_UNDERSTAND_LIVE_RISK=yes "
                                         "in the server environment first.")
        try:
            return registry.get_broker(o.broker, o.mode).place_order(o, dry_run=dry_run)
        except HTTPException:
            raise
        except Exception as e:  # noqa
            raise HTTPException(400, str(e))

    # -------- bots --------
    @app.get("/api/bots")
    def list_bots(token: str = ""):
        check(token)
        return bots.list_bots()

    @app.post("/api/bots/{name}/{action}")
    def bot_action(name: str, action: str, token: str = ""):
        check(token)
        if action == "start":
            return bots.start(name)
        if action == "stop":
            return bots.stop(name)
        raise HTTPException(400, "action must be start|stop")

    @app.get("/api/bots/{name}/log")
    def bot_log(name: str, token: str = ""):
        check(token)
        return {"name": name, "log": bots.log(name)}

    # -------- backtests --------
    @app.get("/api/backtests")
    def list_backtests(token: str = ""):
        check(token)
        return backtests.list_backtests()

    @app.post("/api/backtests/{name}/run")
    def run_backtest(name: str, payload: dict = Body(default={}), token: str = ""):
        check(token)
        return backtests.run(name, payload.get("args"))

    # -------- files (view / edit) --------
    @app.get("/api/files")
    def list_files(token: str = ""):
        check(token)
        root = config.REPO_ROOT
        return sorted(f for f in os.listdir(root) if f.endswith(".py"))

    @app.get("/api/file")
    def read_file(name: str, token: str = ""):
        check(token)
        path = os.path.join(config.REPO_ROOT, os.path.basename(name))
        if not os.path.exists(path):
            raise HTTPException(404, "not found")
        with open(path) as f:
            return {"name": os.path.basename(name), "content": f.read()}

    @app.post("/api/file")
    def write_file(payload: dict = Body(...), token: str = ""):
        check(token)
        name = os.path.basename(payload["name"])
        if not name.endswith(".py"):
            raise HTTPException(400, "only .py files")
        with open(os.path.join(config.REPO_ROOT, name), "w") as f:
            f.write(payload["content"])
        return {"saved": name, "bytes": len(payload["content"])}

    # -------- google drive import/export (not wired in this app) --------
    @app.get("/api/drive/list")
    def drive_list(token: str = ""):
        check(token)
        return {"error": "Drive sync is not configured in the Trader app yet."}

    @app.get("/api/health")
    def health():
        return {"ok": True, "auth": bool(config.ACCESS_TOKEN),
                "live_armed": os.environ.get("ICC_I_UNDERSTAND_LIVE_RISK") == "yes",
                "brokers": config.BROKERS}

    # -------- UI --------
    @app.get("/")
    def index():
        return FileResponse(os.path.join(HERE, "static", "index.html"))

    @app.get("/manifest.json")
    def manifest():
        return FileResponse(os.path.join(HERE, "static", "manifest.json"))

    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import uvicorn
    host = os.environ.get("ICC_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("ICC_TRADER_PORT", "8788"))
    log.info("Trader cockpit on http://%s:%s", host, port)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
