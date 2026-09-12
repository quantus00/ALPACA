"""A tiny web 'cockpit' for ICC backtests.

Run:  icc-web            (needs `pip install -e '.[web]'` and COINBASE_KEY_FILE)
Open: http://<droplet-ip>:8787   (or forward the port in VS Code)

Pick an asset, timeframes, history, and risk settings in the browser; it fetches
candles from Coinbase, runs the same backtest engine, and shows stats, an equity
curve, and the trade list. Read-only market data — it never places orders.
"""
from __future__ import annotations

import logging
import os

from .backtest import run_backtest
from .data import fetch_candles, timeframe_seconds
from .sim import stats
from .strategy import ICCParams

log = logging.getLogger("icc_bot.web")

_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>ICC Backtest Cockpit</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;font:15px/1.4 system-ui,sans-serif;background:#0d1117;color:#e6edf3}
 header{padding:14px 18px;background:#161b22;border-bottom:1px solid #30363d;font-weight:600}
 main{padding:18px;max-width:1000px;margin:0 auto}
 form{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;
      background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}
 label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:#9da7b3}
 input,select{background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:8px}
 .run{grid-column:1/-1}
 button{background:#238636;color:#fff;border:0;border-radius:6px;padding:10px 16px;font-weight:600;cursor:pointer}
 button:disabled{opacity:.5}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:16px 0}
 .stat{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px}
 .stat b{display:block;font-size:22px;margin-top:2px}
 .muted{color:#9da7b3;font-size:12px}
 table{width:100%;border-collapse:collapse;margin-top:12px;font-size:13px}
 th,td{padding:6px 8px;border-bottom:1px solid #21262d;text-align:right}
 th:first-child,td:first-child{text-align:left}
 .win{color:#3fb950}.loss{color:#f85149}
 svg{background:#161b22;border:1px solid #30363d;border-radius:10px;width:100%;height:160px}
 #err{color:#f85149;margin-top:10px;white-space:pre-wrap}
</style></head><body>
<header>ICC Backtest Cockpit</header>
<main>
 <form id=f>
  <label>Asset (product id)<input name=symbol value="BTC-USD"></label>
  <label>Entry timeframe<select name=ltf>
    <option>5m</option><option selected>15m</option><option>30m</option><option>1h</option></select></label>
  <label>Structure timeframe<select name=htf>
    <option>1h</option><option>2h</option><option>6h</option><option>1d</option></select></label>
  <label>History (days)<input name=days type=number value=30 min=2 max=365></label>
  <label>Equity ($)<input name=equity type=number value=10000></label>
  <label>Risk per trade (%)<input name=risk_pct type=number step=0.1 value=1></label>
  <label>Target R:R<input name=target_rr type=number step=0.5 value=3></label>
  <label>HTF pivot length<input name=htf_lookback type=number value=5></label>
  <label>LTF pivot length<input name=ltf_lookback type=number value=3></label>
  <div class=run><button id=go type=submit>Run backtest</button>
     <span class=muted id=note></span></div>
 </form>
 <div id=err></div>
 <div id=out hidden>
   <div class=grid id=stats></div>
   <svg id=eq viewBox="0 0 600 160" preserveAspectRatio=none></svg>
   <table id=trades><thead><tr><th>#</th><th>dir</th><th>entry</th><th>stop</th>
     <th>target</th><th>exit</th><th>outcome</th><th>R</th><th>pnl</th></tr></thead><tbody></tbody></table>
 </div>
</main>
<script>
const f=document.getElementById('f'),go=document.getElementById('go'),note=document.getElementById('note');
f.onsubmit=async e=>{
 e.preventDefault();go.disabled=true;note.textContent='running...';document.getElementById('err').textContent='';
 const d=Object.fromEntries(new FormData(f).entries());
 try{
  const r=await fetch('/api/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
  const j=await r.json();
  if(!r.ok){throw new Error(j.error||'error')}
  render(j);
 }catch(err){document.getElementById('err').textContent=String(err.message||err)}
 go.disabled=false;note.textContent='';
};
function render(j){
 document.getElementById('out').hidden=false;
 const s=j.stats,g=document.getElementById('stats');
 const cells=[['Trades',s.trades],['Win rate',s.win_rate_pct+'%'],['Avg R',s.avg_r],
  ['Profit factor',s.profit_factor??'—'],['Return',s.return_pct+'%'],
  ['Max drawdown',s.max_drawdown_pct+'%'],['End equity','$'+s.end_equity]];
 g.innerHTML=cells.map(c=>`<div class=stat><span class=muted>${c[0]}</span><b>${c[1]}</b></div>`).join('');
 // equity curve
 const eq=j.equity_curve,mn=Math.min(...eq),mx=Math.max(...eq),rng=(mx-mn)||1;
 const pts=eq.map((v,i)=>`${(i/(eq.length-1||1))*600},${160-((v-mn)/rng)*150-5}`).join(' ');
 document.getElementById('eq').innerHTML=`<polyline fill=none stroke=#3fb950 stroke-width=2 points="${pts}"/>`;
 const tb=document.querySelector('#trades tbody');
 tb.innerHTML=j.trades.map((t,i)=>`<tr><td>${i+1}</td><td>${t.direction}</td><td>${t.entry}</td>
  <td>${t.stop}</td><td>${t.target}</td><td>${t.exit}</td><td>${t.outcome}</td>
  <td class=${t.pnl>=0?'win':'loss'}>${t.r_multiple}</td>
  <td class=${t.pnl>=0?'win':'loss'}>${t.pnl.toFixed(2)}</td></tr>`).join('');
}
</script></body></html>"""


def create_app():
    from flask import Flask, jsonify, request

    app = Flask(__name__)

    @app.get("/")
    def index():
        return _PAGE

    @app.post("/api/backtest")
    def api_backtest():
        d = request.get_json(force=True)
        try:
            symbol = str(d.get("symbol", "BTC-USD")).strip()
            ltf = str(d.get("ltf", "15m"))
            htf = str(d.get("htf", "1h"))
            days = float(d.get("days", 30))
            params = ICCParams(htf_lookback=int(d.get("htf_lookback", 5)),
                               ltf_lookback=int(d.get("ltf_lookback", 3)),
                               target_rr=float(d.get("target_rr", 3.0)))
            bars = fetch_candles(symbol, ltf, days)
            if len(bars) < 30:
                return jsonify({"error": f"only {len(bars)} candles returned for "
                                         f"{symbol} {ltf}; try a different asset/timeframe."}), 400
            sim = run_backtest(symbol, bars, timeframe_seconds(htf), params,
                               float(d.get("equity", 10000)), float(d.get("risk_pct", 1.0)))
            eq = [sim.start_equity]
            for t in sim.trades:
                eq.append(round(eq[-1] + t.pnl, 2))
            trades = [{"direction": t.direction.value, "entry": t.entry, "stop": t.stop,
                       "target": round(t.target, 4), "exit": t.exit, "outcome": t.outcome,
                       "r_multiple": round(t.r_multiple, 2), "pnl": round(t.pnl, 2)}
                      for t in sim.trades]
            return jsonify({"stats": stats(sim), "equity_curve": eq, "trades": trades,
                            "bars": len(bars)})
        except Exception as exc:  # surface a readable message to the UI
            log.exception("backtest failed")
            return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 400

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    host = os.environ.get("ICC_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("ICC_WEB_PORT", "8787"))
    log.info("ICC backtest cockpit on http://%s:%s", host, port)
    create_app().run(host=host, port=port)


if __name__ == "__main__":
    main()
