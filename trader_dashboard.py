#!/usr/bin/env python3
"""
trader_dashboard.py — Web UI for Algo Trader
Run: python trader_dashboard.py
Open: http://72.61.173.32:5099
"""

import json
import os
import re
import subprocess
import signal
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, render_template_string, request

BASE_DIR      = Path(__file__).resolve().parent
TC_FILE       = BASE_DIR / "nifty_config.json"
LOG_FILE      = BASE_DIR / "nifty_trader.log"
RESULTS_DIR   = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)
PYTHON        = str(BASE_DIR / "venv" / "bin" / "python")
TRADER_SCRIPT = str(BASE_DIR / "nifty_ema_trader.py")

app = Flask(__name__)

# ── HTML ───────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Algo Trader</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',sans-serif;font-size:14px}

/* ── Header ── */
.hdr{background:#161b22;border-bottom:1px solid #30363d;padding:14px 20px;display:flex;align-items:center;gap:12px}
.hdr h1{font-size:16px;font-weight:600}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:12px}
#clock{color:#8b949e;font-size:13px}
.dot{width:9px;height:9px;border-radius:50%;background:#f85149;display:inline-block;flex-shrink:0}
.dot.on{background:#3fb950;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}

/* ── Tabs ── */
.tabs{display:flex;background:#161b22;border-bottom:1px solid #30363d;padding:0 20px}
.tab{padding:12px 18px;cursor:pointer;color:#8b949e;font-size:13px;font-weight:500;border-bottom:2px solid transparent;transition:.15s}
.tab:hover{color:#e6edf3}
.tab.active{color:#e6edf3;border-bottom-color:#1f6feb}
.tab-body{display:none;padding:20px}
.tab-body.active{display:block}

/* ── Cards / Grid ── */
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:18px}
.card h2{font-size:12px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:14px}
.full{grid-column:1/-1}

/* ── Stat rows ── */
.stat{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid #21262d}
.stat:last-child{border-bottom:none}
.stat-label{color:#8b949e}
.stat-value{font-weight:600}
.green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.blue{color:#58a6ff}

/* ── Buttons ── */
.btn{padding:9px 16px;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;transition:opacity .15s}
.btn:hover{opacity:.8}
.btn-green{background:#238636;color:#fff}
.btn-blue{background:#1f6feb;color:#fff}
.btn-red{background:#da3633;color:#fff}
.btn-gray{background:#21262d;color:#e6edf3;border:1px solid #30363d}
.btn-amber{background:#9e6a03;color:#fff}
.btn-row{display:flex;gap:8px;flex-wrap:wrap}

/* ── Strategy card ── */
.strat-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:18px}
.strat-card.ema{border-color:#1f6feb}
.strat-card.rsi{border-color:#d29922}
.strat-title{font-size:15px;font-weight:600;margin-bottom:6px;display:flex;align-items:center;gap:8px}
.strat-mode{font-size:12px;color:#8b949e;margin-bottom:14px}

/* ── Log box ── */
.log-box{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px 12px;height:320px;overflow-y:auto;font-family:monospace;font-size:11.5px;line-height:1.65}
.lb{color:#8b949e}
.lb-buy{color:#3fb950}
.lb-sell{color:#f85149}
.lb-err{color:#d29922}
.lb-scan{color:#388bfd}

/* ── Table ── */
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;color:#8b949e;border-bottom:1px solid #30363d;font-weight:500}
td{padding:8px 10px;border-bottom:1px solid #21262d}
tr:last-child td{border-bottom:none}
.tag{padding:2px 7px;border-radius:4px;font-size:11px;font-weight:600}
.tag-w{background:#1a4731;color:#3fb950}
.tag-l{background:#3d1f1f;color:#f85149}

/* ── Form inputs ── */
.field{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #21262d}
.field:last-of-type{border-bottom:none}
.field label{color:#8b949e}
input[type=number],select{background:#0d1117;border:1px solid #30363d;color:#e6edf3;padding:5px 9px;border-radius:4px;font-size:13px;width:90px}
select{width:auto}
textarea{width:100%;height:70px;background:#0d1117;border:1px solid #d29922;color:#e6edf3;padding:9px;border-radius:6px;font-family:monospace;font-size:12px;resize:vertical}

/* ── Flash msg ── */
#flash{font-size:13px;color:#8b949e;min-height:20px;padding:6px 0}
</style>
</head>
<body>

<div class="hdr">
  <span class="dot" id="hdr-dot"></span>
  <h1>Algo Trader</h1>
  <span id="hdr-badge" style="font-size:12px;color:#8b949e">—</span>
  <div class="hdr-right">
    <a href="/backtest" target="_blank" style="padding:6px 12px;background:#21262d;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:12px;font-weight:600;text-decoration:none;white-space:nowrap">&#128200; Backtest Lab</a>
    <span id="clock"></span>
  </div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('control')">Control</div>
  <div class="tab" onclick="switchTab('pnl')">P&amp;L</div>
  <div class="tab" onclick="switchTab('log')">Log</div>
  <div class="tab" onclick="switchTab('config')">Config</div>
</div>

<!-- ═══════════════════ TAB: CONTROL ═══════════════════ -->
<div class="tab-body active" id="tab-control">

  <!-- Token card -->
  <div class="card" style="margin-bottom:16px;border-color:#d29922">
    <h2 style="color:#d29922">Dhan Token — Rozana Subah Update Karo</h2>
    <div style="display:flex;gap:10px;align-items:flex-start;margin-top:4px">
      <textarea id="token-input" placeholder="JWT token yahan paste karo..."></textarea>
      <div style="display:flex;flex-direction:column;gap:8px;flex-shrink:0">
        <button class="btn btn-amber" onclick="saveToken()">Save Token</button>
        <button class="btn btn-gray"  onclick="checkToken()">Check</button>
      </div>
    </div>
    <div id="token-msg" style="font-size:12px;margin-top:6px;color:#8b949e"></div>
    <div id="token-status" style="font-size:12px;margin-top:2px;color:#8b949e"></div>
  </div>

  <!-- Strategy cards -->
  <div class="grid" style="margin-bottom:12px">

    <div class="strat-card ema">
      <div class="strat-title">
        <span class="dot" id="ema-dot"></span>
        <span class="blue">EMA 9/20</span>
        <span style="font-size:11px;color:#8b949e">1-min</span>
      </div>
      <div class="strat-mode" id="ema-mode">Stopped</div>
      <div class="btn-row">
        <button class="btn btn-green" onclick="startTrader('ema','paper')">▶ Paper</button>
        <button class="btn btn-blue"  onclick="startTrader('ema','live')">💰 Live</button>
        <button class="btn btn-red"   onclick="stopTrader('ema')">⏹ Stop</button>
      </div>
    </div>

    <div class="strat-card rsi">
      <div class="strat-title">
        <span class="dot" id="rsi-dot"></span>
        <span class="yellow">RSI (14)</span>
        <span style="font-size:11px;color:#8b949e">5-min</span>
      </div>
      <div class="strat-mode" id="rsi-mode">Stopped</div>
      <div class="btn-row">
        <button class="btn btn-green" onclick="startTrader('rsi','paper')">▶ Paper</button>
        <button class="btn btn-blue"  onclick="startTrader('rsi','live')">💰 Live</button>
        <button class="btn btn-red"   onclick="stopTrader('rsi')">⏹ Stop</button>
      </div>
    </div>

  </div>

  <div class="btn-row">
    <button class="btn btn-gray" onclick="saveSummary()">💾 Save Today's Summary</button>
  </div>
  <div id="flash"></div>

</div>

<!-- ═══════════════════ TAB: P&L ═══════════════════ -->
<div class="tab-body" id="tab-pnl">

  <div class="grid" style="margin-bottom:16px">

    <!-- EMA stats -->
    <div class="card" style="border-color:#1f6feb">
      <h2 class="blue">EMA P&amp;L — Today</h2>
      <div class="stat"><span class="stat-label">Trades</span><span class="stat-value" id="e-trades">—</span></div>
      <div class="stat"><span class="stat-label">Wins</span><span class="stat-value green" id="e-wins">—</span></div>
      <div class="stat"><span class="stat-label">Losses</span><span class="stat-value red" id="e-losses">—</span></div>
      <div class="stat"><span class="stat-label">Win Rate</span><span class="stat-value yellow" id="e-wr">—</span></div>
      <div class="stat"><span class="stat-label">Net P&amp;L</span><span class="stat-value" id="e-pnl">—</span></div>
    </div>

    <!-- RSI stats -->
    <div class="card" style="border-color:#d29922">
      <h2 class="yellow">RSI P&amp;L — Today</h2>
      <div class="stat"><span class="stat-label">Trades</span><span class="stat-value" id="r-trades">—</span></div>
      <div class="stat"><span class="stat-label">Wins</span><span class="stat-value green" id="r-wins">—</span></div>
      <div class="stat"><span class="stat-label">Losses</span><span class="stat-value red" id="r-losses">—</span></div>
      <div class="stat"><span class="stat-label">Win Rate</span><span class="stat-value yellow" id="r-wr">—</span></div>
      <div class="stat"><span class="stat-label">Net P&amp;L</span><span class="stat-value" id="r-pnl">—</span></div>
    </div>

  </div>

  <!-- EMA trades table -->
  <div class="card" style="margin-bottom:16px;border-color:#1f6feb">
    <h2 class="blue">EMA Trades</h2>
    <div id="ema-trades">—</div>
  </div>

  <!-- RSI trades table -->
  <div class="card" style="border-color:#d29922">
    <h2 class="yellow">RSI Trades</h2>
    <div id="rsi-trades">—</div>
  </div>

</div>

<!-- ═══════════════════ TAB: LOG ═══════════════════ -->
<div class="tab-body" id="tab-log">

  <div class="grid">
    <div class="card" style="border-color:#1f6feb">
      <h2 class="blue">EMA Log <span style="font-weight:400;font-size:11px;color:#8b949e">(auto 5s)</span></h2>
      <div class="log-box" id="ema-log">Loading...</div>
    </div>
    <div class="card" style="border-color:#d29922">
      <h2 class="yellow">RSI Log <span style="font-weight:400;font-size:11px;color:#8b949e">(auto 5s)</span></h2>
      <div class="log-box" id="rsi-log">Loading...</div>
    </div>
  </div>

</div>

<!-- ═══════════════════ TAB: CONFIG ═══════════════════ -->
<div class="tab-body" id="tab-config">

  <div class="grid">

    <!-- EMA config -->
    <div class="card" style="border-color:#1f6feb">
      <h2 class="blue">EMA Config (hot-reload)</h2>
      <div class="field"><label>Fast EMA</label><input type="number" id="c-fast" value="9" min="1"></div>
      <div class="field"><label>Slow EMA</label><input type="number" id="c-slow" value="20" min="1"></div>
      <div class="field"><label>Qty</label><input type="number" id="c-qty" value="1" min="1"></div>
      <div class="field"><label>Max Trades / Symbol</label><input type="number" id="c-max" value="2" min="1"></div>
      <div class="field"><label>Active</label>
        <select id="c-active">
          <option value="true">Yes</option>
          <option value="false">Pause</option>
        </select>
      </div>
      <button class="btn btn-gray" style="margin-top:14px;width:100%" onclick="saveEmaConfig()">💾 Save EMA Config</button>
    </div>

    <!-- RSI config -->
    <div class="card" style="border-color:#d29922">
      <h2 class="yellow">RSI Config (hot-reload)</h2>
      <div class="field"><label>RSI Period</label><input type="number" id="r-period" value="14" min="1"></div>
      <div class="field"><label>Oversold</label><input type="number" id="r-os" value="30" min="1"></div>
      <div class="field"><label>Overbought</label><input type="number" id="r-ob" value="70" min="1"></div>
      <div class="field"><label>Qty</label><input type="number" id="r-qty" value="1" min="1"></div>
      <div class="field"><label>Max Trades / Symbol</label><input type="number" id="r-max" value="2" min="1"></div>
      <div class="field"><label>Active</label>
        <select id="r-active">
          <option value="true">Yes</option>
          <option value="false">Pause</option>
        </select>
      </div>
      <button class="btn btn-gray" style="margin-top:14px;width:100%" onclick="saveRsiConfig()">💾 Save RSI Config</button>
    </div>

  </div>
  <div id="cfg-msg" style="font-size:13px;color:#8b949e;padding:10px 0"></div>

</div>

<script>
// ── Tab switch ─────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t,i)=>{
    const names = ['control','pnl','log','config'];
    t.classList.toggle('active', names[i]===name);
  });
  document.querySelectorAll('.tab-body').forEach(b=>{
    b.classList.toggle('active', b.id==='tab-'+name);
  });
  if (name==='log') { loadLog(); }
  if (name==='pnl') { loadPnl(); }
  if (name==='config') { loadConfigs(); }
}

// ── Clock ──────────────────────────────────────────────
setInterval(()=>{
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'})+' IST';
},1000);

// ── Flash ──────────────────────────────────────────────
function flash(msg, ok=true) {
  const el = document.getElementById('flash') || document.getElementById('cfg-msg');
  el.textContent = msg;
  el.style.color = ok ? '#3fb950' : '#f85149';
  setTimeout(()=>{ el.textContent=''; el.style.color='#8b949e'; }, 4000);
}

// ── Status ─────────────────────────────────────────────
function setDot(id, on, mode) {
  const dot  = document.getElementById(id+'-dot');
  const lbl  = document.getElementById(id+'-mode');
  dot.classList.toggle('on', on);
  if (on) {
    lbl.textContent  = mode==='live' ? '💰 LIVE' : '📝 PAPER';
    lbl.style.color  = mode==='live' ? '#1f6feb' : '#3fb950';
  } else {
    lbl.textContent = 'Stopped';
    lbl.style.color = '#8b949e';
  }
}

async function checkStatus() {
  const d = await fetch('/api/status').then(r=>r.json());
  const anyOn = d.ema.running || d.rsi.running;
  const hdrDot = document.getElementById('hdr-dot');
  const hdrBadge = document.getElementById('hdr-badge');
  hdrDot.classList.toggle('on', anyOn);
  if (anyOn) {
    const isLive = (d.ema.running&&d.ema.mode==='live')||(d.rsi.running&&d.rsi.mode==='live');
    hdrBadge.textContent = isLive ? '💰 LIVE' : '📝 PAPER';
    hdrBadge.style.color = isLive ? '#1f6feb' : '#3fb950';
  } else {
    hdrBadge.textContent = 'All stopped';
    hdrBadge.style.color = '#8b949e';
  }
  setDot('ema', d.ema.running, d.ema.mode);
  setDot('rsi', d.rsi.running, d.rsi.mode);
}

// ── Trader start / stop ────────────────────────────────
async function startTrader(s, mode) {
  const d = await fetch(`/api/start?s=${s}&mode=${mode}`,{method:'POST'}).then(r=>r.json());
  flash(d.msg);
  setTimeout(checkStatus, 1200);
}
async function stopTrader(s) {
  const d = await fetch(`/api/stop?s=${s}`,{method:'POST'}).then(r=>r.json());
  flash(d.msg);
  setTimeout(checkStatus, 1200);
}
async function saveSummary() {
  const d = await fetch('/api/save-summary',{method:'POST'}).then(r=>r.json());
  flash(d.msg);
}

// ── Token ──────────────────────────────────────────────
async function saveToken() {
  const tok = document.getElementById('token-input').value.trim();
  if (tok.length < 20) { document.getElementById('token-msg').textContent='⚠️ Token too short'; return; }
  const d = await fetch('/api/token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:tok})}).then(r=>r.json());
  document.getElementById('token-msg').textContent = d.msg;
  document.getElementById('token-msg').style.color = d.ok ? '#3fb950' : '#f85149';
  document.getElementById('token-input').value = '';
  checkToken();
}
async function checkToken() {
  const d = await fetch('/api/token').then(r=>r.json());
  const el = document.getElementById('token-status');
  el.textContent = d.has_token ? `Saved token: ...${d.preview}  (${d.saved_at})` : 'No token saved';
  el.style.color = d.has_token ? '#8b949e' : '#f85149';
}

// ── Log ────────────────────────────────────────────────
function colorLines(lines) {
  return lines.map(l=>{
    let c='lb';
    if(l.includes('PAPER BUY')||l.includes('LIVE BUY')||l.includes('signal=BUY')) c='lb-buy';
    else if(l.includes('PAPER SELL')||l.includes('LIVE SELL')||l.includes('signal=SELL')) c='lb-sell';
    else if(l.includes('ERROR')||l.includes('LOSS')) c='lb-err';
    else if(l.includes('Scanning')||l.includes('[RSI] Scanning')) c='lb-scan';
    return `<div class="${c}">${l}</div>`;
  }).join('');
}
async function loadLog() {
  const [re,rr] = await Promise.all([fetch('/api/log?s=ema'),fetch('/api/log?s=rsi')]);
  const [de,dr] = await Promise.all([re.json(),rr.json()]);
  const eb = document.getElementById('ema-log');
  const rb = document.getElementById('rsi-log');
  eb.innerHTML = colorLines(de.lines); eb.scrollTop=eb.scrollHeight;
  rb.innerHTML = colorLines(dr.lines); rb.scrollTop=rb.scrollHeight;
}

// ── P&L ────────────────────────────────────────────────
function fillPnl(pfx, d) {
  document.getElementById(pfx+'-trades').textContent = d.trades;
  document.getElementById(pfx+'-wins').textContent   = d.wins;
  document.getElementById(pfx+'-losses').textContent = d.losses;
  document.getElementById(pfx+'-wr').textContent     = d.win_rate+'%';
  const el = document.getElementById(pfx+'-pnl');
  el.textContent = (d.total_pnl>=0?'+':'')+d.total_pnl;
  el.className   = 'stat-value '+(d.total_pnl>=0?'green':'red');
}
function buildTable(details) {
  if(!details||!details.length) return '<p style="color:#8b949e;padding:8px 0">No completed trades today.</p>';
  let h='<table><thead><tr><th>Symbol</th><th>Entry</th><th>Price</th><th>Exit</th><th>Price</th><th>P&L</th><th></th></tr></thead><tbody>';
  details.forEach(t=>{
    const tag=t.pnl>=0?'<span class="tag tag-w">WIN</span>':'<span class="tag tag-l">LOSS</span>';
    h+=`<tr><td><b>${t.sym}</b></td><td>${t.entry}</td><td>${t.entry_price.toFixed(2)}</td><td>${t.exit}</td><td>${t.exit_price.toFixed(2)}</td><td class="${t.pnl>=0?'green':'red'}"><b>${t.pnl>=0?'+':''}${t.pnl.toFixed(0)}</b></td><td>${tag}</td></tr>`;
  });
  return h+'</tbody></table>';
}
async function loadPnl() {
  const [re,rr] = await Promise.all([fetch('/api/pnl?s=ema'),fetch('/api/pnl?s=rsi')]);
  const [de,dr] = await Promise.all([re.json(),rr.json()]);
  fillPnl('e',de); fillPnl('r',dr);
  document.getElementById('ema-trades').innerHTML = buildTable(de.details);
  document.getElementById('rsi-trades').innerHTML = buildTable(dr.details);
}

// ── Config ─────────────────────────────────────────────
async function loadConfigs() {
  const [re,rr] = await Promise.all([fetch('/api/config?s=ema'),fetch('/api/config?s=rsi')]);
  const [de,dr] = await Promise.all([re.json(),rr.json()]);
  document.getElementById('c-fast').value = de.fast_ema||9;
  document.getElementById('c-slow').value = de.slow_ema||20;
  document.getElementById('c-qty').value  = de.qty||1;
  document.getElementById('c-max').value  = de.max_trades_per_symbol||2;
  document.getElementById('c-active').value = de.active===false?'false':'true';
  document.getElementById('r-period').value = dr.rsi_period||14;
  document.getElementById('r-os').value     = dr.oversold||30;
  document.getElementById('r-ob').value     = dr.overbought||70;
  document.getElementById('r-qty').value    = dr.qty||1;
  document.getElementById('r-max').value    = dr.max_trades_per_symbol||2;
  document.getElementById('r-active').value = dr.active===false?'false':'true';
}
async function saveEmaConfig() {
  const cfg={
    active: document.getElementById('c-active').value==='true',
    fast_ema: +document.getElementById('c-fast').value,
    slow_ema: +document.getElementById('c-slow').value,
    qty: +document.getElementById('c-qty').value,
    max_trades_per_symbol: +document.getElementById('c-max').value,
  };
  await fetch('/api/config?s=ema',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
  document.getElementById('cfg-msg').textContent='✅ EMA config saved (hot-reload)';
  setTimeout(()=>document.getElementById('cfg-msg').textContent='',3000);
}
async function saveRsiConfig() {
  const cfg={
    active: document.getElementById('r-active').value==='true',
    rsi_period: +document.getElementById('r-period').value,
    oversold: +document.getElementById('r-os').value,
    overbought: +document.getElementById('r-ob').value,
    qty: +document.getElementById('r-qty').value,
    max_trades_per_symbol: +document.getElementById('r-max').value,
  };
  await fetch('/api/config?s=rsi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
  document.getElementById('cfg-msg').textContent='✅ RSI config saved (hot-reload)';
  setTimeout(()=>document.getElementById('cfg-msg').textContent='',3000);
}

// ── Init ───────────────────────────────────────────────
checkStatus(); checkToken(); loadPnl();
setInterval(()=>{ checkStatus(); loadPnl(); }, 5000);
setInterval(()=>{ if(document.getElementById('tab-log').classList.contains('active')) loadLog(); }, 5000);
</script>
</body>
</html>"""

# ── API Routes ─────────────────────────────────────────────────────────────────

RSI_SCRIPT  = str(BASE_DIR / "rsi_trader.py")
RSI_LOG     = BASE_DIR / "rsi_trader.log"
RSI_CFG     = BASE_DIR / "rsi_config.json"
CONFIG_FILE = BASE_DIR / "data" / "config.json"

STRATEGIES = {
    "ema": {"script": TRADER_SCRIPT, "log": LOG_FILE, "cfg": TC_FILE,  "grep": "nifty_ema_trader"},
    "rsi": {"script": RSI_SCRIPT,    "log": RSI_LOG,  "cfg": RSI_CFG,  "grep": "rsi_trader"},
}

def get_pid(strategy="ema"):
    grep = STRATEGIES[strategy]["grep"]
    try:
        out = subprocess.check_output(['pgrep', '-f', grep], text=True).strip()
        return int(out.split('\n')[0]) if out else None
    except Exception:
        return None

def get_mode(strategy="ema"):
    grep = STRATEGIES[strategy]["grep"]
    try:
        out = subprocess.check_output(['ps', 'aux'], text=True)
        for line in out.splitlines():
            if grep in line:
                return 'live' if '--live' in line else 'paper'
    except Exception:
        pass
    return 'paper'

def parse_pnl(log_path, today, qty=1):
    try:
        lines = [l for l in Path(log_path).read_text().splitlines() if l.startswith(today)]
    except Exception:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0, "details": []}

    signals = {}
    for line in lines:
        m = re.search(r'\[RSI\]\s+(\w+)\s+close=([\d.]+)\s+RSI=[\d.]+\s+signal=(BUY|SELL)', line)
        if not m:
            m = re.search(r'  (\w+)\s+close=([\d.]+)\s+signal=(BUY|SELL)', line)
        if m:
            sym, price, sig = m.group(1), float(m.group(2)), m.group(3)
            signals.setdefault(sym, []).append((sig, price))

    details, total_pnl, wins, losses = [], 0, 0, 0
    for sym, entries in sorted(signals.items()):
        for i in range(len(entries) - 1):
            a, b = entries[i], entries[i+1]
            if   a[0]=='BUY'  and b[0]=='SELL': p = (b[1]-a[1])*qty
            elif a[0]=='SELL' and b[0]=='BUY':  p = (a[1]-b[1])*qty
            else: continue
            total_pnl += p
            wins   += 1 if p > 0 else 0
            losses += 0 if p > 0 else 1
            details.append({"sym": sym, "entry": a[0], "entry_price": a[1],
                            "exit": b[0], "exit_price": b[1], "pnl": round(p, 2)})
    n = len(details)
    return {"trades": n, "wins": wins, "losses": losses,
            "win_rate": round(wins/n*100, 1) if n else 0,
            "total_pnl": round(total_pnl, 2), "details": details}

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/backtest')
def backtest():
    from flask import send_file
    return send_file(BASE_DIR / 'backtest_dashboard.html')

@app.route('/api/status')
def api_status():
    ep = get_pid('ema')
    rp = get_pid('rsi')
    return jsonify({
        "ema": {"running": ep is not None, "pid": ep, "mode": get_mode('ema') if ep else None},
        "rsi": {"running": rp is not None, "pid": rp, "mode": get_mode('rsi') if rp else None},
    })

@app.route('/api/log')
def api_log():
    s  = request.args.get('s', 'ema')
    lf = STRATEGIES.get(s, STRATEGIES['ema'])['log']
    try:
        lines = Path(lf).read_text().splitlines()[-80:]
        return jsonify({"lines": lines})
    except Exception:
        return jsonify({"lines": ["Log not found"]})

@app.route('/api/config', methods=['GET'])
def api_get_config():
    s  = request.args.get('s', 'ema')
    cf = STRATEGIES.get(s, STRATEGIES['ema'])['cfg']
    try:
        return jsonify(json.loads(Path(cf).read_text()))
    except Exception:
        if s == 'rsi':
            return jsonify({"rsi_period": 14, "oversold": 30, "overbought": 70, "qty": 1, "max_trades_per_symbol": 2, "active": True})
        return jsonify({"fast_ema": 9, "slow_ema": 20, "qty": 1, "max_trades_per_symbol": 2, "active": True})

@app.route('/api/config', methods=['POST'])
def api_set_config():
    s  = request.args.get('s', 'ema')
    cf = Path(STRATEGIES.get(s, STRATEGIES['ema'])['cfg'])
    data = request.get_json()
    existing = {}
    if cf.exists():
        existing = json.loads(cf.read_text())
    existing.update(data)
    cf.write_text(json.dumps(existing, indent=2))
    return jsonify({"ok": True})

@app.route('/api/start', methods=['POST'])
def api_start():
    s    = request.args.get('s', 'ema')
    mode = request.args.get('mode', 'paper')
    st   = STRATEGIES.get(s, STRATEGIES['ema'])
    pid  = get_pid(s)
    if pid:
        return jsonify({"msg": f"{s.upper()} already running (PID {pid})"})
    flag = '--live' if mode == 'live' else '--paper'
    lf   = open(st['log'], 'a')
    subprocess.Popen([PYTHON, st['script'], flag],
                     stdout=lf, stderr=lf,
                     cwd=str(BASE_DIR),
                     start_new_session=True)
    return jsonify({"msg": f"✅ {s.upper()} started — {mode.upper()} mode"})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    s   = request.args.get('s', 'ema')
    pid = get_pid(s)
    if not pid:
        return jsonify({"msg": f"{s.upper()} not running"})
    try:
        os.kill(pid, signal.SIGTERM)
        return jsonify({"msg": f"⏹ {s.upper()} stopped"})
    except Exception as e:
        return jsonify({"msg": f"Error: {e}"})

@app.route('/api/pnl')
def api_pnl():
    s     = request.args.get('s', 'ema')
    today = datetime.now().strftime("%Y-%m-%d")
    lf    = STRATEGIES.get(s, STRATEGIES['ema'])['log']
    return jsonify(parse_pnl(lf, today))

@app.route('/api/token', methods=['GET'])
def api_get_token():
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        tok = cfg.get('jwt_token', '')
        if not tok:
            return jsonify({"has_token": False})
        return jsonify({"has_token": True, "preview": tok[-12:], "saved_at": cfg.get('token_saved_at', '?')})
    except Exception:
        return jsonify({"has_token": False})

@app.route('/api/token', methods=['POST'])
def api_set_token():
    token = (request.get_json().get('token') or '').strip()
    if len(token) < 20:
        return jsonify({"ok": False, "msg": "⚠️ Invalid token"})
    try:
        cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        cfg['jwt_token']     = token
        cfg['token_saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        return jsonify({"ok": True, "msg": "✅ Token saved!"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route('/api/save-summary', methods=['POST'])
def api_save_summary():
    try:
        subprocess.run([PYTHON, str(BASE_DIR / 'save_daily_summary.py')], cwd=str(BASE_DIR))
        return jsonify({"msg": "✅ Summary saved to results/"})
    except Exception as e:
        return jsonify({"msg": f"Error: {e}"})

if __name__ == '__main__':
    print("\n🤖 Algo Trader Dashboard")
    print("   Open: http://72.61.173.32:5099\n")
    app.run(host='0.0.0.0', port=5099, debug=False)
