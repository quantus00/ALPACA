"""ICC Cockpit — a browser control surface for the ICC trading engine.

One FastAPI app, wired straight to the same engine the CLI uses, with four panels:

  • Backtest   — replay history (Coinbase or deep ccxt data) and see stats,
                 an equity curve, and every trade. No keys needed for ccxt.
  • Bot        — start/stop the ICC bot as a child process in a chosen mode
                 (dry_run / paper / live) with the settings you pick, and tail
                 its log live.
  • Positions  — current account balance and open positions from your broker.
  • Order      — a *guarded* manual order panel. Dry-run by default; a live
                 order refuses unless you both flip live and type CONFIRM, and
                 the whole app refuses live unless ICC_I_UNDERSTAND_LIVE_RISK=yes.

Safety model (unchanged from the CLI):
  - The cockpit never trades on its own. It only shows data and relays the
    buttons you press.
  - `COCKPIT_TOKEN` (env) gates every /api call when set — share the URL with
    ?token=... or send an `X-Cockpit-Token` header. Leaving it unset is fine on
    a private droplet but logs a warning.
  - Live orders/bot need BOTH the env opt-in (`ICC_I_UNDERSTAND_LIVE_RISK=yes`)
    and an explicit per-action confirmation.

Run:  pip install -e '.[web]'   then   scripts/run_web.sh   (or `icc-cockpit`)
Open: http://<droplet-ip>:8787  (forward the port in VS Code if remote)
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from collections import deque
from typing import Deque, Optional

# Imported at module level so FastAPI can resolve the string annotations that
# `from __future__ import annotations` produces (it looks them up in module
# globals, not the create_app closure). Guarded so the non-web parts of this
# module still import when the [web] extra isn't installed.
try:
    from fastapi import FastAPI, Header, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
except ImportError:  # pragma: no cover - only when [web] extra is absent
    FastAPI = None  # type: ignore

log = logging.getLogger("icc_bot.cockpit")

# ---------------------------------------------------------------------------
# Bot process supervisor — runs `python -m icc_bot` as a child, tails its log.
# ---------------------------------------------------------------------------


class BotSupervisor:
    """Owns at most one child ICC-bot process and a ring buffer of its output."""

    def __init__(self, max_lines: int = 500) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lines: Deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._started_env: dict = {}

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        with self._lock:
            code = None if self.running else (self._proc.returncode if self._proc else None)
            return {
                "running": self.running,
                "pid": self._proc.pid if self._proc else None,
                "returncode": code,
                "mode": self._started_env.get("ICC_MODE"),
                "symbols": self._started_env.get("ICC_SYMBOLS"),
                "venue": self._started_env.get("ICC_VENUE"),
            }

    def log_text(self) -> str:
        with self._lock:
            return "".join(self._lines)

    def start(self, env_overrides: dict) -> dict:
        if self.running:
            return {"ok": False, "error": "bot already running; stop it first"}

        mode = str(env_overrides.get("ICC_MODE", "dry_run")).lower()
        if mode == "live" and os.environ.get("ICC_I_UNDERSTAND_LIVE_RISK") != "yes":
            return {"ok": False, "error": "live bot blocked: set "
                    "ICC_I_UNDERSTAND_LIVE_RISK=yes in the server environment first"}

        env = os.environ.copy()
        # only keep string values; drop blanks so config defaults apply
        clean = {k: str(v) for k, v in env_overrides.items() if str(v).strip() != ""}
        env.update(clean)

        with self._lock:
            self._lines.clear()
            self._started_env = clean
            self._lines.append(f"$ ICC bot starting ({mode}) {clean}\n")
            self._proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "icc_bot"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env, bufsize=1,
            )
        threading.Thread(target=self._pump, args=(self._proc,), daemon=True).start()
        return {"ok": True, "pid": self._proc.pid, "mode": mode}

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return {"ok": True, "note": "not running"}
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        with self._lock:
            self._lines.append("$ ICC bot stopped\n")
        return {"ok": True}

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            with self._lock:
                self._lines.append(line)
        with self._lock:
            self._lines.append(f"$ bot exited (code {proc.returncode})\n")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _run_backtest_payload(d: dict) -> dict:
    """Shared backtest logic (also used by tests)."""
    from .backtest import run_backtest
    from .data import fetch_candles, fetch_ccxt, timeframe_seconds
    from .sim import stats
    from .strategy import ICCParams

    symbol = str(d.get("symbol", "BTC-USD")).strip()
    ltf = str(d.get("ltf", "15m"))
    htf = str(d.get("htf", "1h"))
    days = float(d.get("days", 30))
    source = str(d.get("source", "coinbase")).lower()
    params = ICCParams(htf_lookback=int(d.get("htf_lookback", 5)),
                       ltf_lookback=int(d.get("ltf_lookback", 3)),
                       target_rr=float(d.get("target_rr", 3.0)))

    if source == "coinbase":
        bars = fetch_candles(symbol, ltf, days)
    else:  # ccxt exchange id, e.g. "binanceus" / "kraken" / "coinbase"
        bars = fetch_ccxt(symbol, ltf, days, exchange=source)

    if len(bars) < 30:
        raise ValueError(f"only {len(bars)} candles for {symbol} {ltf} via {source}; "
                         "try more days, a different timeframe, or another source.")

    sim = run_backtest(symbol, bars, timeframe_seconds(htf), params,
                       float(d.get("equity", 10000)), float(d.get("risk_pct", 1.0)))
    eq = [sim.start_equity]
    for t in sim.trades:
        eq.append(round(eq[-1] + t.pnl, 2))
    trades = [{"direction": t.direction.value, "entry": t.entry, "stop": t.stop,
               "target": round(t.target, 4), "exit": t.exit, "outcome": t.outcome,
               "r_multiple": round(t.r_multiple, 2), "pnl": round(t.pnl, 2)}
              for t in sim.trades]
    return {"stats": stats(sim), "equity_curve": eq, "trades": trades, "bars": len(bars)}


def create_app():
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("FastAPI not installed — run: pip install -e '.[web]'")

    app = FastAPI(title="ICC Cockpit", docs_url=None, redoc_url=None)
    bot = BotSupervisor()
    app.state.bot = bot

    token = os.environ.get("COCKPIT_TOKEN", "").strip()
    if not token:
        log.warning("COCKPIT_TOKEN is not set — the cockpit API is unauthenticated. "
                    "Set it (and keep the port private) before exposing this.")

    def _check(tok_header: Optional[str], tok_query: Optional[str]) -> None:
        if not token:
            return
        supplied = (tok_header or tok_query or "").strip()
        if supplied != token:
            raise HTTPException(status_code=401, detail="bad or missing cockpit token")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _PAGE

    @app.get("/manifest.json")
    def manifest():
        return JSONResponse(_MANIFEST)

    @app.get("/api/health")
    def health():
        return {"ok": True, "auth": bool(token),
                "live_armed": os.environ.get("ICC_I_UNDERSTAND_LIVE_RISK") == "yes"}

    @app.post("/api/backtest")
    async def api_backtest(request: Request,
                           x_cockpit_token: Optional[str] = Header(None),
                           token: Optional[str] = Query(None)):
        _check(x_cockpit_token, token)
        d = await request.json()
        try:
            return _run_backtest_payload(d)
        except Exception as exc:  # readable message to the UI
            log.exception("backtest failed")
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)

    # --- account / positions (read-only) -----------------------------------
    @app.get("/api/account")
    def api_account(x_cockpit_token: Optional[str] = Header(None),
                    token: Optional[str] = Query(None)):
        _check(x_cockpit_token, token)
        try:
            from .config import load_config
            from .runner import build_broker
            broker = build_broker(load_config())
            acct = broker.get_account()
            positions = broker.get_positions()
            return {"broker": broker.name, "equity": acct.equity, "cash": acct.cash,
                    "buying_power": acct.buying_power, "positions": positions}
        except Exception as exc:
            log.exception("account fetch failed")
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)

    # --- bot control --------------------------------------------------------
    @app.get("/api/bot/status")
    def bot_status(x_cockpit_token: Optional[str] = Header(None),
                   token: Optional[str] = Query(None)):
        _check(x_cockpit_token, token)
        return bot.status()

    @app.get("/api/bot/log", response_class=PlainTextResponse)
    def bot_log(x_cockpit_token: Optional[str] = Header(None),
                token: Optional[str] = Query(None)):
        _check(x_cockpit_token, token)
        return bot.log_text()

    @app.post("/api/bot/start")
    async def bot_start(request: Request,
                        x_cockpit_token: Optional[str] = Header(None),
                        token: Optional[str] = Query(None)):
        _check(x_cockpit_token, token)
        d = await request.json()
        overrides = {
            "ICC_BROKER": d.get("broker", "coinbase"),
            "ICC_VENUE": d.get("venue", "spot"),
            "ICC_MODE": d.get("mode", "dry_run"),
            "ICC_SYMBOLS": d.get("symbols", "BTC-USD"),
            "ICC_HTF": d.get("htf", "1h"),
            "ICC_LTF": d.get("ltf", "15m"),
            "ICC_TARGET_RR": d.get("target_rr", ""),
            "ICC_RISK_PCT": d.get("risk_pct", ""),
        }
        return bot.start(overrides)

    @app.post("/api/bot/stop")
    def bot_stop(x_cockpit_token: Optional[str] = Header(None),
                 token: Optional[str] = Query(None)):
        _check(x_cockpit_token, token)
        return bot.stop()

    # --- guarded manual order ----------------------------------------------
    @app.post("/api/order")
    async def api_order(request: Request,
                        x_cockpit_token: Optional[str] = Header(None),
                        token: Optional[str] = Query(None)):
        _check(x_cockpit_token, token)
        d = await request.json()
        from .config import load_config
        from .models import Order, Side
        from .runner import build_broker

        live = bool(d.get("live"))
        confirm = str(d.get("confirm", ""))
        if live:
            if os.environ.get("ICC_I_UNDERSTAND_LIVE_RISK") != "yes":
                return JSONResponse({"error": "live orders blocked: set "
                    "ICC_I_UNDERSTAND_LIVE_RISK=yes on the server"}, status_code=403)
            if confirm != "CONFIRM":
                return JSONResponse({"error": "type CONFIRM to place a live order"},
                                    status_code=400)

        try:
            side = Side.BUY if str(d.get("side", "buy")).lower() == "buy" else Side.SELL
            order = Order(
                symbol=str(d["symbol"]).strip(),
                side=side,
                qty=float(d["qty"]),
                type=str(d.get("type", "market")),
                limit_price=float(d["limit_price"]) if d.get("limit_price") else None,
                stop_price=float(d["stop_price"]) if d.get("stop_price") else None,
                take_profit=float(d["take_profit"]) if d.get("take_profit") else None,
                meta={"source": "cockpit"},
            )
        except (KeyError, ValueError, TypeError) as exc:
            return JSONResponse({"error": f"bad order fields: {exc}"}, status_code=400)

        # Non-live orders go through a bare DryRunBroker that records but never
        # sends — no broker connection or credentials required. Only an explicit
        # live order builds the real venue adapter.
        from .brokers.base import DryRunBroker
        from .models import Mode
        try:
            if live:
                cfg = load_config()
                cfg.mode = Mode.LIVE
                broker = build_broker(cfg)
                mode = cfg.mode.value
            else:
                broker = DryRunBroker()
                mode = Mode.DRY_RUN.value
            use_bracket = order.stop_price is not None or order.take_profit is not None
            result = broker.place_bracket(order) if use_bracket else broker.place_order(order)
            return {"ok": True, "mode": mode, "broker": broker.name,
                    "bracket": use_bracket, "result": result}
        except Exception as exc:
            log.exception("order failed")
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import uvicorn
    host = os.environ.get("ICC_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("ICC_WEB_PORT", "8787"))
    log.info("ICC cockpit on http://%s:%s", host, port)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


_MANIFEST = {
    "name": "ICC Cockpit",
    "short_name": "ICC",
    "start_url": ".",
    "display": "standalone",
    "background_color": "#0d1117",
    "theme_color": "#0d1117",
    "icons": [],
}


_PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<link rel=manifest href="manifest.json">
<title>ICC Cockpit</title>
<style>
 :root{color-scheme:dark}
 *{box-sizing:border-box}
 body{margin:0;font:15px/1.4 system-ui,sans-serif;background:#0d1117;color:#e6edf3}
 header{padding:14px 18px;background:#161b22;border-bottom:1px solid #30363d;
   font-weight:600;display:flex;justify-content:space-between;align-items:center;gap:12px}
 header .live{font-size:12px;font-weight:400;color:#9da7b3}
 nav{display:flex;gap:4px;padding:10px 18px 0;background:#161b22;flex-wrap:wrap}
 nav button{background:transparent;color:#9da7b3;border:0;border-bottom:2px solid transparent;
   padding:8px 14px;cursor:pointer;font-size:14px}
 nav button.on{color:#e6edf3;border-bottom-color:#238636}
 main{padding:18px;max-width:1000px;margin:0 auto}
 section{display:none}section.on{display:block}
 form{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;
      background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}
 label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:#9da7b3}
 input,select{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:8px}
 .full{grid-column:1/-1}
 button.act{background:#238636;color:#fff;border:0;border-radius:6px;padding:10px 16px;font-weight:600;cursor:pointer}
 button.act.stop{background:#8b2c2c}
 button.act:disabled{opacity:.5}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0}
 .stat{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px}
 .stat b{display:block;font-size:22px;margin-top:2px}
 .muted{color:#9da7b3;font-size:12px}
 table{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px}
 th,td{padding:6px 8px;border-bottom:1px solid #21262d;text-align:right}
 th:first-child,td:first-child{text-align:left}
 .win{color:#3fb950}.loss{color:#f85149}
 svg{background:#161b22;border:1px solid #30363d;border-radius:10px;width:100%;height:160px}
 pre{background:#010409;border:1px solid #30363d;border-radius:10px;padding:12px;
   font-size:12px;max-height:340px;overflow:auto;white-space:pre-wrap}
 .err{color:#f85149;margin-top:10px;white-space:pre-wrap}
 .pill{font-size:12px;padding:2px 8px;border-radius:20px;border:1px solid #30363d}
 .pill.run{color:#3fb950;border-color:#238636}
 .warn{background:#3a2a00;border:1px solid #7a5c00;border-radius:8px;padding:8px 12px;
   font-size:13px;margin-bottom:12px;color:#f5d67b}
</style></head><body>
<header><span>⚡ ICC Cockpit</span><span class=live id=live></span></header>
<nav>
 <button class=on data-tab=backtest>Backtest</button>
 <button data-tab=bot>Bot</button>
 <button data-tab=positions>Positions</button>
 <button data-tab=order>Order</button>
</nav>
<main>
 <!-- BACKTEST ------------------------------------------------------------->
 <section id=backtest class=on>
  <form id=bt>
   <label>Data source<select name=source>
     <option value=coinbase selected>Coinbase (needs key)</option>
     <option value=binanceus>ccxt: binanceus</option>
     <option value=kraken>ccxt: kraken</option>
     <option value=coinbase>ccxt: coinbase</option></select></label>
   <label>Asset<input name=symbol value="BTC-USD"></label>
   <label>Entry timeframe<select name=ltf>
     <option>5m</option><option selected>15m</option><option>30m</option><option>1h</option></select></label>
   <label>Structure timeframe<select name=htf>
     <option>1h</option><option>2h</option><option>6h</option><option>1d</option></select></label>
   <label>History (days)<input name=days type=number value=30 min=2 max=365></label>
   <label>Equity ($)<input name=equity type=number value=10000></label>
   <label>Risk/trade (%)<input name=risk_pct type=number step=0.1 value=1></label>
   <label>Target R:R<input name=target_rr type=number step=0.5 value=3></label>
   <label>HTF pivot len<input name=htf_lookback type=number value=5></label>
   <label>LTF pivot len<input name=ltf_lookback type=number value=3></label>
   <div class=full><button class=act id=btgo type=submit>Run backtest</button>
      <span class=muted id=btnote></span></div>
  </form>
  <div class=err id=bterr></div>
  <div id=btout hidden>
    <div class=grid id=btstats></div>
    <svg id=eq viewBox="0 0 600 160" preserveAspectRatio=none></svg>
    <table id=bttrades><thead><tr><th>#</th><th>dir</th><th>entry</th><th>stop</th>
      <th>target</th><th>exit</th><th>outcome</th><th>R</th><th>pnl</th></tr></thead><tbody></tbody></table>
  </div>
 </section>

 <!-- BOT ------------------------------------------------------------------->
 <section id=bot>
  <div class=warn>The bot places orders only in <b>live</b> mode, and live needs
    <code>ICC_I_UNDERSTAND_LIVE_RISK=yes</code> in the server environment. Start in
    <b>dry_run</b> or <b>paper</b> first.</div>
  <form id=bf>
   <label>Broker<select name=broker><option>coinbase</option><option>webull</option></select></label>
   <label>Venue<select name=venue><option>spot</option><option>futures</option><option>perp</option></select></label>
   <label>Mode<select name=mode><option value=dry_run selected>dry_run</option>
     <option value=paper>paper</option><option value=live>live</option></select></label>
   <label>Symbols<input name=symbols value="BTC-USD"></label>
   <label>Structure TF<input name=htf value="1h"></label>
   <label>Entry TF<input name=ltf value="15m"></label>
   <label>Target R:R<input name=target_rr type=number step=0.5 placeholder=3></label>
   <label>Risk/trade (%)<input name=risk_pct type=number step=0.1 placeholder=1></label>
   <div class=full>
     <button class=act id=bstart type=submit>Start bot</button>
     <button class=act stop id=bstop type=button>Stop bot</button>
     <span class=pill id=bstate>—</span>
   </div>
  </form>
  <div class=err id=boterr></div>
  <pre id=botlog>(no output yet)</pre>
 </section>

 <!-- POSITIONS ------------------------------------------------------------->
 <section id=positions>
  <button class=act id=posrefresh type=button>Refresh</button>
  <div class=err id=poserr></div>
  <div class=grid id=posstats></div>
  <table id=postbl><thead><tr><th>product</th><th>side</th><th>size</th><th>entry</th>
    <th>uPnL</th></tr></thead><tbody></tbody></table>
 </section>

 <!-- ORDER ----------------------------------------------------------------->
 <section id=order>
  <div class=warn>Guarded manual order. Leave <b>Live</b> off to dry-run (nothing is
    sent). Live also needs the server opt-in and typing <code>CONFIRM</code>.</div>
  <form id=of>
   <label>Symbol<input name=symbol value="BTC-USD"></label>
   <label>Side<select name=side><option>buy</option><option>sell</option></select></label>
   <label>Qty (units)<input name=qty type=number step=any value=0.001></label>
   <label>Type<select name=type><option>market</option><option>limit</option></select></label>
   <label>Limit price<input name=limit_price type=number step=any placeholder=optional></label>
   <label>Stop price<input name=stop_price type=number step=any placeholder=optional></label>
   <label>Take profit<input name=take_profit type=number step=any placeholder=optional></label>
   <label>Live?<select name=live><option value="">no (dry-run)</option><option value=1>YES — real order</option></select></label>
   <label>Confirm<input name=confirm placeholder="type CONFIRM for live"></label>
   <div class=full><button class=act id=ogo type=submit>Place order</button>
     <span class=muted id=onote></span></div>
  </form>
  <div class=err id=oerr></div>
  <pre id=oout hidden></pre>
 </section>
</main>
<script>
// token from ?token= in the URL, forwarded on every call
const TOKEN=new URLSearchParams(location.search).get('token')||'';
const H={'Content-Type':'application/json'};
if(TOKEN)H['X-Cockpit-Token']=TOKEN;
const $=s=>document.querySelector(s);
async function api(path,opts={}){
 const r=await fetch(path,{headers:H,...opts});
 const j=await r.json().catch(()=>({}));
 if(!r.ok)throw new Error(j.error||j.detail||('HTTP '+r.status));
 return j;
}
// tabs
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on',x===b));
 document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id===b.dataset.tab));
 if(b.dataset.tab==='positions')loadPositions();
 if(b.dataset.tab==='bot')pollBot();
});
fetch('/api/health').then(r=>r.json()).then(h=>{
 $('#live').textContent=(h.auth?'🔒 token on':'🔓 no token')+' · '+(h.live_armed?'LIVE ARMED':'live disarmed');
}).catch(()=>{});

// --- backtest ---
$('#bt').onsubmit=async e=>{
 e.preventDefault();$('#btgo').disabled=true;$('#btnote').textContent='running…';$('#bterr').textContent='';
 try{
  const d=Object.fromEntries(new FormData($('#bt')).entries());
  const j=await api('/api/backtest',{method:'POST',body:JSON.stringify(d)});
  renderBT(j);
 }catch(err){$('#bterr').textContent=String(err.message||err)}
 $('#btgo').disabled=false;$('#btnote').textContent='';
};
function renderBT(j){
 $('#btout').hidden=false;const s=j.stats;
 const cells=[['Trades',s.trades],['Win rate',s.win_rate_pct+'%'],['Avg R',s.avg_r],
  ['Profit factor',s.profit_factor??'—'],['Return',s.return_pct+'%'],
  ['Max drawdown',s.max_drawdown_pct+'%'],['End equity','$'+s.end_equity],['Bars',j.bars]];
 $('#btstats').innerHTML=cells.map(c=>`<div class=stat><span class=muted>${c[0]}</span><b>${c[1]}</b></div>`).join('');
 const eq=j.equity_curve,mn=Math.min(...eq),mx=Math.max(...eq),rng=(mx-mn)||1;
 const pts=eq.map((v,i)=>`${(i/(eq.length-1||1))*600},${160-((v-mn)/rng)*150-5}`).join(' ');
 $('#eq').innerHTML=`<polyline fill=none stroke=#3fb950 stroke-width=2 points="${pts}"/>`;
 $('#bttrades tbody').innerHTML=j.trades.map((t,i)=>`<tr><td>${i+1}</td><td>${t.direction}</td>
  <td>${t.entry}</td><td>${t.stop}</td><td>${t.target}</td><td>${t.exit}</td><td>${t.outcome}</td>
  <td class=${t.pnl>=0?'win':'loss'}>${t.r_multiple}</td>
  <td class=${t.pnl>=0?'win':'loss'}>${t.pnl.toFixed(2)}</td></tr>`).join('');
}

// --- bot ---
let botTimer=null;
$('#bf').onsubmit=async e=>{
 e.preventDefault();$('#boterr').textContent='';
 try{
  const d=Object.fromEntries(new FormData($('#bf')).entries());
  const j=await api('/api/bot/start',{method:'POST',body:JSON.stringify(d)});
  if(!j.ok)throw new Error(j.error||'could not start');
  pollBot();
 }catch(err){$('#boterr').textContent=String(err.message||err)}
};
$('#bstop').onclick=async()=>{
 $('#boterr').textContent='';
 try{await api('/api/bot/stop',{method:'POST'});pollBot();}
 catch(err){$('#boterr').textContent=String(err.message||err)}
};
async function pollBot(){
 try{
  const s=await api('/api/bot/status');
  const st=$('#bstate');
  st.textContent=s.running?`running pid ${s.pid} · ${s.mode}`:'stopped';
  st.classList.toggle('run',s.running);
  const log=await (await fetch('/api/bot/log',{headers:H})).text();
  const el=$('#botlog');const atBottom=el.scrollTop+el.clientHeight>=el.scrollHeight-20;
  el.textContent=log||'(no output yet)';if(atBottom)el.scrollTop=el.scrollHeight;
  clearTimeout(botTimer);
  if(document.querySelector('#bot').classList.contains('on'))botTimer=setTimeout(pollBot,2000);
 }catch(err){$('#boterr').textContent=String(err.message||err)}
}

// --- positions ---
$('#posrefresh').onclick=loadPositions;
async function loadPositions(){
 $('#poserr').textContent='';
 try{
  const j=await api('/api/account');
  $('#posstats').innerHTML=[['Broker',j.broker],['Equity','$'+fmt(j.equity)],
    ['Cash','$'+fmt(j.cash)],['Buying power','$'+fmt(j.buying_power)]]
    .map(c=>`<div class=stat><span class=muted>${c[0]}</span><b>${c[1]}</b></div>`).join('');
  const rows=(j.positions||[]).map(p=>`<tr><td>${p.product_id||p.symbol||'?'}</td>
    <td>${p.side||''}</td><td>${p.size??p.qty??''}</td><td>${p.entry_price??p.avg_entry??''}</td>
    <td>${p.unrealized_pnl??p.upnl??''}</td></tr>`).join('');
  $('#postbl tbody').innerHTML=rows||'<tr><td colspan=5 class=muted>no open positions</td></tr>';
 }catch(err){$('#poserr').textContent=String(err.message||err);$('#postbl tbody').innerHTML=''}
}
function fmt(n){return (typeof n==='number')?n.toLocaleString(undefined,{maximumFractionDigits:2}):n}

// --- order ---
$('#of').onsubmit=async e=>{
 e.preventDefault();$('#ogo').disabled=true;$('#onote').textContent='sending…';$('#oerr').textContent='';$('#oout').hidden=true;
 try{
  const d=Object.fromEntries(new FormData($('#of')).entries());
  d.live=!!d.live;
  const j=await api('/api/order',{method:'POST',body:JSON.stringify(d)});
  $('#oout').hidden=false;$('#oout').textContent=JSON.stringify(j,null,2);
 }catch(err){$('#oerr').textContent=String(err.message||err)}
 $('#ogo').disabled=false;$('#onote').textContent='';
};
</script></body></html>"""


if __name__ == "__main__":
    main()
