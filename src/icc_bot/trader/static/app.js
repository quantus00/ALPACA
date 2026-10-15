const TOKEN = new URLSearchParams(location.search).get("token") || "";
const q = s => `${s}${s.includes("?") ? "&" : "?"}token=${encodeURIComponent(TOKEN)}`;
const mode = () => document.body.classList.contains("live") ? "live" : "paper";

async function api(path, opts) {
  const r = await fetch(q(path), opts);
  if (!r.ok) { let t = await r.text(); throw new Error(t); }
  return r.json();
}

// ---- tabs ----
document.querySelectorAll(".tabbar button").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tabbar button").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  document.getElementById("tab-" + b.dataset.tab).classList.add("active");
});

// ---- live/paper toggle ----
const liveToggle = document.getElementById("liveToggle");
liveToggle.onchange = () => {
  document.body.classList.toggle("live", liveToggle.checked);
  document.getElementById("modeLabel").textContent = liveToggle.checked ? "LIVE" : "PAPER";
  document.querySelector(".send").classList.toggle("live", liveToggle.checked);
  if (typeof disarmSend === "function") disarmSend();
};

// ---- accounts ----
async function loadAccounts() {
  const el = document.getElementById("accounts"); el.innerHTML = "Loading…";
  try {
    const data = await api(`/api/accounts?broker=all&mode=${mode()}`);
    el.innerHTML = "";
    data.forEach(b => {
      if (b.error) { el.innerHTML += `<div class="acct"><b>${b.broker}</b> <span class="err">${b.error}</span></div>`; return; }
      (b.accounts || []).forEach(a => {
        el.innerHTML += `<div class="acct">
          <div><b>${b.broker}</b> · ${a.label || ""}
          <span class="badge ${a.mode}">${(a.mode||"").toUpperCase()}</span></div>
          <div class="id">${a.account_id || ""}</div>
          ${a.equity != null ? `<div>equity $${a.equity}</div>` : ""}
          ${a.error ? `<div class="err">${a.error}</div>` : ""}
          <button onclick="fillAccount('${b.broker}','${a.account_id}')">Use in Trade</button>
        </div>`;
      });
    });
  } catch (e) { el.innerHTML = `<span class="err">${e.message}</span>`; }
}
function fillAccount(broker, id) {
  document.getElementById("t_broker").value = broker;
  document.getElementById("t_account").value = id;
  document.querySelector('[data-tab="trade"]').click();
}

// ---- trade ----
function pickAsset() {
  const v = document.getElementById("t_asset").value;
  if (!v) return;
  const [sym, brk, instr] = v.split("|");
  t_symbol.value = sym;
  t_broker.value = brk;
  if (instr) t_instr.value = instr;
  t_fractional.checked = false;
}
function orderPayload(dry) {
  return {
    broker: t_broker.value, mode: mode(), account_id: t_account.value,
    symbol: t_symbol.value, side: t_side.value, instrument: t_instr.value,
    qty: parseFloat(t_qty.value) || null,
    notional: t_notional.value ? parseFloat(t_notional.value) : null,
    type: t_type.value, price: t_price.value ? parseFloat(t_price.value) : null,
    fractional: t_fractional.checked, dry_run: dry
  };
}
async function preview() {
  try {
    const r = await api("/api/order", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(orderPayload(true))});
    t_out.textContent = "PREVIEW (nothing sent):\n" + (r.preview || JSON.stringify(r, null, 2));
  } catch (e) { t_out.innerHTML = `<span class="err">${e.message}</span>`; }
}
// Mobile-safe confirm: a LIVE order needs a deliberate SECOND tap (auto-disarms
// after 8s). PAPER sends on the first tap.
let sendArmed = false, armTimer = null;
function disarmSend() {
  sendArmed = false; clearTimeout(armTimer);
  const btn = document.querySelector(".send");
  if (btn && btn.dataset.label) btn.textContent = btn.dataset.label;
}
function armSend(summary) {
  sendArmed = true;
  const btn = document.querySelector(".send");
  if (!btn.dataset.label) btn.dataset.label = btn.textContent;
  btn.textContent = "⚠️ TAP AGAIN → send LIVE";
  t_out.textContent = "LIVE order ARMED:\n  " + summary +
    "\n\nTap the red button AGAIN to send it, or wait 8s to cancel.";
  speak("Live order armed. Tap the red button again to send.");
  clearTimeout(armTimer);
  armTimer = setTimeout(() => { disarmSend();
    t_out.textContent = "cancelled (waited too long — tap Send again to re-arm)."; }, 8000);
}
async function send() {
  const p = orderPayload(false);
  const summary = `${p.side} ${p.qty ?? ("$"+p.notional)} ${p.symbol} `
    + `(${p.instrument}) ${p.type} — ${p.mode.toUpperCase()}`;
  if (p.mode === "live") {
    if (!sendArmed) { armSend(summary); return; }
    p.confirm = "CONFIRM";
    disarmSend();
  }
  try {
    t_out.textContent = "sending…";
    const r = await api("/api/order", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(p)});
    t_out.textContent = (r.sent ? "SENT:\n" : "NOT SENT:\n") + JSON.stringify(r, null, 2);
    speak(r.sent ? "Order sent." : "Order not sent.");
  } catch (e) { t_out.innerHTML = `<span class="err">${e.message}</span>`; speak("Order failed. " + e.message); }
}

// ---- bots ----
let BOTS = [];
async function loadBots() {
  const el = document.getElementById("bots"); el.innerHTML = "Loading…";
  BOTS = await api("/api/bots"); el.innerHTML = "";
  BOTS.forEach(b => {
    el.innerHTML += `<div class="acct"><div><b>${b.name}</b>
      <span class="badge ${b.running?'live':'paper'}">${b.running?'RUNNING':'stopped'}</span></div>
      <div class="id">${b.cmd}</div>
      <div class="row">
        <button onclick="botAct('${encodeURIComponent(b.name)}','start')">Start</button>
        <button class="send" onclick="botAct('${encodeURIComponent(b.name)}','stop')">Stop</button>
        <button onclick="botLog('${encodeURIComponent(b.name)}')">Log</button>
      </div></div>`;
  });
}
async function botAct(n, a) { await api(`/api/bots/${n}/${a}`, {method:"POST"}); loadBots(); }
async function botLog(n) { const r = await api(`/api/bots/${n}/log`); botlog.textContent = r.log || "(no output yet)"; }

// ---- backtests ----
let BACKTESTS = [];
async function loadBacktests() {
  const el = document.getElementById("backtests"); el.innerHTML = "Loading…";
  BACKTESTS = await api("/api/backtests"); el.innerHTML = "";
  BACKTESTS.forEach(b => {
    el.innerHTML += `<div class="acct"><div><b>${b.name}</b></div>
      <div class="id">${b.cmd}</div>
      <button onclick="runBt('${encodeURIComponent(b.name)}')">Run</button></div>`;
  });
}
async function runBt(n) {
  bt_out.textContent = "running…";
  const r = await api(`/api/backtests/${n}/run`, {method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"});
  bt_out.textContent = r.output;
  speak(`Backtest ${decodeURIComponent(n)} complete.`);
}

// ---- files (view/edit) ----
async function loadFiles() {
  const files = await api("/api/files");
  f_list.innerHTML = files.map(f => `<option>${f}</option>`).join("");
  openFile();
}
async function openFile() {
  if (!f_list.value) return;
  const r = await api(`/api/file?name=${encodeURIComponent(f_list.value)}`);
  f_content.value = r.content;
}
async function saveFile() {
  const r = await api("/api/file", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({name:f_list.value, content:f_content.value})});
  alert("saved " + r.saved + " (" + r.bytes + " bytes)");
}

// ---- VOICE (browser Web Speech API) ----
function speak(text) {
  const on = document.getElementById("speakBack");
  if (!on || !on.checked || !text) return;
  try { window.speechSynthesis.cancel();
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(text)); } catch (e) {}
}
const TICKERS = {bitcoin:"BTC-USD", btc:"BTC-USD", ether:"ETH-USD", ethereum:"ETH-USD",
  eth:"ETH-USD", gold:"GOL", perp:"BTC-PERP"};
function extractSymbol(s) {
  for (const k in TICKERS) if (s.includes(k)) return TICKERS[k];
  return null;
}
function startVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { voice_status.textContent = "Speech recognition needs Chrome or Edge."; return; }
  const r = new SR(); r.lang = "en-US"; r.interimResults = false; r.maxAlternatives = 1;
  voice_status.textContent = "Listening…"; speak("Listening");
  r.onerror = e => { voice_status.textContent = "Voice error: " + e.error; };
  r.onresult = e => { const t = e.results[0][0].transcript;
    voice_status.textContent = "Heard: " + t; route(t); };
  r.start();
}
function matchFromList(s, list) {
  // match a spoken phrase to a loaded bot/backtest by keyword overlap
  let best = null, bestScore = 0;
  (list || []).forEach(item => {
    const words = item.name.toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/);
    const score = words.filter(w => w.length > 2 && s.includes(w)).length;
    if (score > bestScore) { bestScore = score; best = item.name; }
  });
  return bestScore > 0 ? best : null;
}
async function route(t) {
  const s = t.toLowerCase();
  if (s.includes("bot")) {
    const start = /\b(start|turn on|run|launch|begin)\b/.test(s);
    const stop = /\b(stop|turn off|kill|halt|end)\b/.test(s);
    if (!BOTS.length) { try { BOTS = await api("/api/bots"); } catch(e){} }
    const name = matchFromList(s, BOTS);
    if (name && (start || stop)) {
      document.querySelector('[data-tab="bots"]').click();
      speak(`${stop ? "Stopping" : "Starting"} ${name}.`);
      await botAct(encodeURIComponent(name), stop ? "stop" : "start");
      return;
    }
  }
  if (s.includes("backtest") || (s.includes("test") && !/\b(buy|sell)\b/.test(s))) {
    if (!BACKTESTS.length) { try { BACKTESTS = await api("/api/backtests"); } catch(e){} }
    const name = matchFromList(s, BACKTESTS);
    if (name) {
      document.querySelector('[data-tab="backtests"]').click();
      await runBt(encodeURIComponent(name));
      return;
    }
    speak("Which backtest? Say Bitcoin or Ethereum.");
    return;
  }
  parseVoice(t);
}
function parseVoice(t) {
  const s = t.toLowerCase();
  if (/\bsell\b/.test(s)) t_side.value = "sell"; else if (/\bbuy\b/.test(s)) t_side.value = "buy";
  ["coinbase","webull","mock"].forEach(b => { if (s.includes(b)) t_broker.value = b; });
  if (s.includes("live")) { liveToggle.checked = true; liveToggle.onchange(); }
  else if (s.includes("paper") || s.includes("demo")) { liveToggle.checked = false; liveToggle.onchange(); }
  if (s.includes("limit")) { t_type.value = "limit";
    const m = s.match(/limit\s+(?:at\s+)?(\d+(?:\.\d+)?)/); if (m) t_price.value = m[1]; }
  else if (s.includes("market")) t_type.value = "market";
  const dm = s.match(/(\d+(?:\.\d+)?)\s*dollars?/);
  if (dm) { t_notional.value = dm[1]; t_fractional.checked = true; }
  else { const qm = s.match(/\b(\d+(?:\.\d+)?)\b/); if (qm) t_qty.value = qm[1]; }
  const sym = extractSymbol(s); if (sym) t_symbol.value = sym;
  document.querySelector('[data-tab="trade"]').click();
  const size = t_fractional.checked ? ("$" + t_notional.value) : t_qty.value;
  const summary = `${t_side.value} ${size} ${t_symbol.value || "(no symbol heard)"} `
    + `${t_type.value} on ${t_broker.value}, ${mode()}`;
  voice_status.textContent = "Parsed → " + summary + " — tap Confirm to send.";
  preview();
  speak(t_symbol.value ? ("Ready to " + summary + ". Tap confirm to send.")
    : "I didn't catch the symbol. Please say it again or type it.");
}

// initial load
loadAccounts();
