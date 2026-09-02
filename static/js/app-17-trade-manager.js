/* app-17-trade-manager.js — Trade Manager panel (Open Positions ke andar).
 *
 * Ek open position GROUP pe exit triggers set karne ka panel: ₹ combined MTM,
 * combined premium points, index points, aur absolute index level — har ek apne
 * toggle ke saath, plus exit confirmation (wick / candle close / close + N min).
 *
 * DELIBERATELY LIGHT. Panel khulte hi ek bhi bhaari call nahi jaati:
 *   • legs pehle se Open Positions table me hain (koi fetch nahi)
 *   • armed rule ek chhoti GET se aati hai
 *   • Δ (₹ per index point) LAZY hai — panel uske bina poora chalta hai, wo sirf
 *     "₹4,000 = kitne index point" wala hint deta hai. Trigger comparison server
 *     pe index-PRICE space me hoti hai, isliye Δ late aaye ya na aaye, SL/target
 *     galat jagah nahi lag sakte.
 * Payoff panel ke `/api/position-payoff` + `/api/position-legs-series` (BS curve
 * + per-leg Dhan candles) — jo use slow karte hain — yahan se KABHI nahi chhute.
 */

window._tmState = window._tmState || {};

function _tmBtn(items, grpId) {
  const ids = (items || []).map(t => t.id).filter(Boolean);
  if (!ids.length) return '';
  const sym = String((items[0] && (items[0].symbol || items[0].sym)) || '').split('-')[0];
  const q = _tmQty(items);
  return `<button onclick="tmToggle('${grpId}','${ids.join(',')}','${sym}',${q}, event)"
    title="Trade Manager — ₹ / index point / index level pe exit trigger"
    style="margin-right:10px;padding:3px 8px;font-size:11px;background:#a371f720;border:1px solid #a371f780;border-radius:4px;color:#c8a2fc;cursor:pointer;font-weight:600">🎛 Manage</button>`;
}

function _tmQty(items) {
  // "Combined premium points" ke liye structure ka base qty = sabse bada leg.
  return Math.max.apply(null, (items || []).map(t => Math.abs(+t.qty || 0)).concat([1]));
}

async function tmToggle(grpId, idsCsv, sym, qty, ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  const host = document.getElementById('tm-panel-' + grpId);
  if (!host) return;
  if (host.style.display !== 'none') { host.style.display = 'none'; return; }
  host.style.display = 'block';
  const S = window._tmState[grpId] = window._tmState[grpId] || {
    ids: idsCsv, sym: sym, qty: qty || 1, qs: 'ids=' + idsCsv,
    rs_tg: '', rs_sl: '', ip_tg: '', ip_sl: '', il_tg: '', il_sl: '',
    en: { rs: true, pp: false, ip: true, il: true },
    tf: '5m', mode: 'close', wait: 2, rpt: null, spot: null, loaded: false
  };
  S.ids = idsCsv; S.qs = 'ids=' + idsCsv; S.sym = sym; S.qty = qty || S.qty || 1;
  tmRender(grpId);
  if (!S.loaded) {
    S.loaded = true;
    // armed rule (chhoti call) — taaki dobara kholne pe saved values dikhein
    try {
      const j = await (await fetch('/api/position-exit-rule?' + S.qs)).json();
      const r = j && j.rule;
      if (r) {
        S.rs_tg = r.target_rs ? Math.round(r.target_rs) : '';
        S.rs_sl = r.sl_rs ? Math.round(r.sl_rs) : '';
        S.ip_tg = r.idx_pt_tg || ''; S.ip_sl = r.idx_pt_sl || '';
        S.il_tg = r.idx_px_tg || ''; S.il_sl = r.idx_px_sl || '';
        if (r.enabled) S.en = Object.assign(S.en, r.enabled);
        S.tf = r.tf || S.tf; S.mode = r.confirm_mode || S.mode;
        S.wait = r.confirm_min || S.wait; S.armed = true;
        S.entry_spot = r.entry_spot || null;
        tmRender(grpId);
      }
    } catch (e) { /* panel rule ke bina bhi poora chalta hai */ }
    tmLoadDelta(grpId);
  }
}

// Δ — LAZY, panel ke render ko kabhi block nahi karta.
// net_delta pe qty aur side dono baked hain, to |net_delta| hi seedha
// "₹ per index point" hai (Δ × lot × lots wala hisaab wahin ho chuka).
async function tmLoadDelta(grpId) {
  const S = window._tmState[grpId]; if (!S) return;
  try {
    const g = await (await fetch('/api/position-greeks?' + S.qs)).json();
    if (g && g.ok) {
      S.rpt = Math.abs(+g.net_delta || 0) || null;
      S.spot = g.spot || null;
      S.entry_spot = S.entry_spot || g.spot_entry || null;
      tmRender(grpId);
    }
  } catch (e) { /* Δ optional — bridge line bas '—' rahegi */ }
}

// Keys typed into text boxes. For these we must NOT re-render the panel on every
// keystroke — innerHTML replace destroys the focused <input>, so the user could
// type exactly ONE digit ("25625" me sirf "2" jaata tha). Update state + refresh
// only the derived bits (₹→points row, Δ bridge) in place.
const _TM_TEXT_KEYS = new Set(['rs_sl', 'rs_tg', 'ip_sl', 'ip_tg', 'il_sl', 'il_tg', 'wait']);
function tmSet(grpId, k, v) {
  const S = window._tmState[grpId]; if (!S) return;
  S[k] = v;
  if (_TM_TEXT_KEYS.has(k)) { _tmRefreshDerived(grpId); return; }
  tmRender(grpId);
}
function _tmRefreshDerived(grpId) {
  const S = window._tmState[grpId]; if (!S) return;
  const q = S.qty || 1;
  const set = (id, val) => { const el = document.getElementById(id); if (el && el.value !== val) el.value = val; };
  set(`tm-in-${grpId}-pp_sl`, S.rs_sl ? (Math.abs(+S.rs_sl) / q).toFixed(1) : '');
  set(`tm-in-${grpId}-pp_tg`, S.rs_tg ? (Math.abs(+S.rs_tg) / q).toFixed(1) : '');
  const b = document.getElementById(`tm-bridge-${grpId}`);
  if (b) b.innerHTML = _tmBridgeHtml(S);
}
function _tmBridgeHtml(S) {
  let bridge = `<span style="color:#6e7681">Δ aa raha hai… (iske bina bhi trigger set ho jayenge)</span>`;
  if (S.rpt) {
    const es = S.entry_spot || S.spot;
    const mk = (rs, dirUp) => {
      if (!rs || !es) return '—';
      const pt = Math.abs(+rs) / S.rpt;
      const lv = dirUp ? es + pt : es - pt;
      return `${pt.toFixed(0)} pt → ${Math.round(lv).toLocaleString('en-IN')}`;
    };
    bridge = `<b style="color:#79b8ff">₹${Math.round(S.rpt).toLocaleString('en-IN')}</b>
      <span style="color:#6e7681">per index point</span>
      &nbsp;·&nbsp; 🎯 <span style="color:#3fb950">${mk(S.rs_tg, true)}</span>
      &nbsp;·&nbsp; 🛡 <span style="color:#f85149">${mk(S.rs_sl, false)}</span>`;
  }
  return bridge;
}
function tmTog(grpId, k) {
  const S = window._tmState[grpId]; if (!S) return;
  S.en[k] = !S.en[k]; tmRender(grpId);
}

function _tmRow(grpId, key, name, note, pre, slVal, tgVal, slK, tgK, derived, rightBorder) {
  const S = window._tmState[grpId];
  const on = !!S.en[key];
  const dis = derived ? ' readonly' : '';
  const bg = derived ? '#0b0f15' : '#0d1117';
  // 2-column layout: aadhi width me hone se SL/Target boxes label ke paas aa
  // jaate hain (full-width rows me wo screen ke door kone me chale jaate the).
  // max-width zaroori hai: bina iske label cell (1fr) poori chaudai kha jaata hai
  // aur wide screen pe SL/Target boxes text se ~500px door chale jaate hain (yahi
  // asli shikayat thi). Cap se dono column left-packed rehte hain aur inputs
  // aapas me aligned bhi.
  const bd = rightBorder ? 'border-right:1px solid #21262d;' : '';
  return `<div style="display:grid;grid-template-columns:30px minmax(0,1fr) 88px 88px;gap:7px;align-items:center;max-width:430px;padding:8px 10px;border-bottom:1px solid #21262d;${bd}opacity:${on ? 1 : .42}">
    <button onclick="tmTog('${grpId}','${key}')" title="on/off"
      style="width:32px;height:18px;border:0;border-radius:9px;background:${on ? '#1f6feb55' : '#30363d'};position:relative;cursor:pointer;padding:0">
      <span style="position:absolute;top:2px;left:${on ? 16 : 2}px;width:14px;height:14px;border-radius:50%;background:${on ? '#58a6ff' : '#8b949e'};transition:left .15s"></span></button>
    <div><div style="font-size:12px;font-weight:600;color:#e6edf3">${name}</div>
         <div style="font-size:10px;color:#6e7681;margin-top:1px">${note}</div></div>
    <div style="display:flex;align-items:center;border:1px solid #30363d;border-radius:5px;background:${bg}">
      <span style="padding:0 5px;color:#6e7681;font-size:10px;font-family:ui-monospace,monospace">${pre}</span>
      <input id="tm-in-${grpId}-${slK}" value="${slVal}" ${dis} placeholder="SL" oninput="tmSet('${grpId}','${slK}',this.value)"
        style="background:transparent;border:0;color:${derived ? '#8b949e' : '#e6edf3'};font-family:ui-monospace,monospace;font-size:12px;font-weight:600;padding:5px 6px 5px 0;width:100%;outline:none"></div>
    <div style="display:flex;align-items:center;border:1px solid #30363d;border-radius:5px;background:${bg}">
      <span style="padding:0 5px;color:#6e7681;font-size:10px;font-family:ui-monospace,monospace">${pre}</span>
      <input id="tm-in-${grpId}-${tgK}" value="${tgVal}" ${dis} placeholder="Target" oninput="tmSet('${grpId}','${tgK}',this.value)"
        style="background:transparent;border:0;color:${derived ? '#8b949e' : '#e6edf3'};font-family:ui-monospace,monospace;font-size:12px;font-weight:600;padding:5px 6px 5px 0;width:100%;outline:none"></div>
  </div>`;
}

function tmRender(grpId) {
  const host = document.getElementById('tm-panel-' + grpId);
  const S = window._tmState[grpId];
  if (!host || !S) return;

  const q = S.qty || 1;
  const ppSl = S.rs_sl ? (Math.abs(+S.rs_sl) / q).toFixed(1) : '';
  const ppTg = S.rs_tg ? (Math.abs(+S.rs_tg) / q).toFixed(1) : '';

  // Δ bridge — sirf hint. Δ na mile to '—', panel phir bhi poora kaam karta hai.
  // (HTML _tmBridgeHtml me — tmSet ise bina re-render in-place refresh karta hai.)
  const bridge = _tmBridgeHtml(S);

  const seg = (v, cur, k) => `<button onclick="tmSet('${grpId}','${k}','${v}')"
    style="background:${cur === v ? '#1f6feb' : 'transparent'};border:0;color:${cur === v ? '#fff' : '#8b949e'};font-family:ui-monospace,monospace;font-size:11px;font-weight:600;padding:4px 8px;cursor:pointer">${v}</button>`;
  const mopt = (v, label, desc) => `<label onclick="tmSet('${grpId}','mode','${v}')"
    style="display:flex;gap:8px;align-items:flex-start;padding:7px 9px;border:1px solid ${S.mode === v ? '#1f6feb' : '#30363d'};border-radius:6px;cursor:pointer;background:${S.mode === v ? '#1f6feb12' : '#0d1117'}">
    <span style="width:12px;height:12px;border-radius:50%;border:2px solid ${S.mode === v ? '#58a6ff' : '#484f58'};flex:none;margin-top:2px;position:relative">${S.mode === v ? '<span style="position:absolute;inset:2px;border-radius:50%;background:#58a6ff"></span>' : ''}</span>
    <span><span style="font-size:11.5px;font-weight:600;color:#e6edf3">${label}</span>
    <span style="display:block;font-size:10px;color:#6e7681;margin-top:1px;line-height:1.4">${desc}</span></span></label>`;

  host.innerHTML = `
  <div style="border:1px solid #30363d;border-radius:8px;background:#0d1117;margin:8px 12px 12px">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:9px 11px;border-bottom:1px solid #30363d">
      <span style="font-size:12.5px;font-weight:700;color:#c8a2fc">🎛 Trade Manager</span>
      <span style="font-size:10.5px;color:#8b949e;border:1px solid #30363d;border-radius:4px;padding:2px 7px">${S.sym || '—'}</span>
      <span style="font-size:10.5px;color:#8b949e;border:1px solid #30363d;border-radius:4px;padding:2px 7px">qty ${q}</span>
      ${S.armed ? '<span style="font-size:10.5px;color:#79b8ff;border:1px solid #1f6feb55;background:#1f6feb14;border-radius:4px;padding:2px 7px">● rule armed</span>' : ''}
      <span style="flex:1"></span>
      <span id="tm-note-${grpId}" style="font-size:11px;color:#8b949e"></span>
    </div>

    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,430px));justify-content:start">
      ${_tmRow(grpId, 'rs', '₹ Combined premium', 'poore basket ka MTM', '₹', S.rs_sl, S.rs_tg, 'rs_sl', 'rs_tg', false, true)}
      ${_tmRow(grpId, 'pp', 'Combined premium — points', '₹ ÷ qty (' + q + '), auto', 'pt', ppSl, ppTg, 'pp_sl', 'pp_tg', true, false)}
      ${_tmRow(grpId, 'ip', 'Index points', 'entry ke index level se ±', 'pt', S.ip_sl, S.ip_tg, 'ip_sl', 'ip_tg', false, true)}
      ${_tmRow(grpId, 'il', 'Index price', 'apna level — cross pe exit', '₹', S.il_sl, S.il_tg, 'il_sl', 'il_tg', false, false)}
    </div>

    <div id="tm-bridge-${grpId}" style="padding:8px 11px;border-bottom:1px solid #21262d;font-family:ui-monospace,monospace;font-size:11px">${bridge}</div>

    <div style="display:flex;gap:12px;flex-wrap:wrap;padding:10px 11px">
      <div style="min-width:200px;flex:1">
        <div style="font-size:9.5px;letter-spacing:.06em;text-transform:uppercase;color:#6e7681;font-weight:600;margin-bottom:6px">Exit confirmation</div>
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:7px">
          <span style="font-size:11px;color:#8b949e">TF</span>
          <div style="display:inline-flex;border:1px solid #30363d;border-radius:5px;overflow:hidden;background:#0d1117">
            ${seg('1m', S.tf, 'tf')}${seg('3m', S.tf, 'tf')}${seg('5m', S.tf, 'tf')}${seg('15m', S.tf, 'tf')}</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:5px">
          ${mopt('wick', 'Wick chhuye = turant', 'broker jaisa · ~3s loop, tick-level nahi')}
          ${mopt('close', 'Candle close pe', 'touch se kuch nahi — close bahar hona chahiye')}
          ${mopt('wait', 'Close + <input value="' + S.wait + '" onclick="event.stopPropagation()" oninput="tmSet(\'' + grpId + '\',\'wait\',this.value)" style="width:34px;text-align:center;background:#0d1117;border:1px solid #30363d;border-radius:4px;color:#e6edf3;font-size:11px;font-family:ui-monospace,monospace;padding:0 3px"> min', 'fake breakout filter')}
        </div>
      </div>
      <div style="min-width:190px;display:flex;flex-direction:column;gap:7px;justify-content:flex-end">
        <div style="font-size:10.5px;color:#6e7681;line-height:1.55">
          Jitne source ON hain, <b style="color:#8b949e">jo pehle chhuye</b> wahi poori group square off karega
          (shorts pehle, wings baad me).<br>
          <span style="color:#d29922">Confirmation abhi index triggers pe hai — ₹ wala turant firta hai.</span>
        </div>
        <div style="display:flex;gap:7px">
          <button onclick="tmClear('${grpId}')" style="background:transparent;border:1px solid #30363d;border-radius:6px;color:#8b949e;font-size:11.5px;font-weight:600;padding:6px 10px;cursor:pointer">Rule hatao</button>
          <button onclick="tmApply('${grpId}')" id="tm-apply-${grpId}" style="flex:1;background:#1f6feb;border:1px solid #1f6feb;border-radius:6px;color:#fff;font-size:11.5px;font-weight:600;padding:6px 10px;cursor:pointer">✓ Rule laga do</button>
        </div>
      </div>
    </div>
  </div>`;
}

async function tmApply(grpId) {
  const S = window._tmState[grpId]; if (!S) return;
  const btn = document.getElementById('tm-apply-' + grpId);
  const note = document.getElementById('tm-note-' + grpId);
  const num = v => { const f = parseFloat(v); return isNaN(f) ? null : f; };
  const body = {
    qs: S.qs,
    target_rs: S.en.rs ? (num(S.rs_tg) || 0) : 0,
    sl_rs: S.en.rs ? -Math.abs(num(S.rs_sl) || 0) : 0,
    idx_pt_tg: S.en.ip ? num(S.ip_tg) : null,
    idx_pt_sl: S.en.ip ? num(S.ip_sl) : null,
    idx_px_tg: S.en.il ? num(S.il_tg) : null,
    idx_px_sl: S.en.il ? num(S.il_sl) : null,
    enabled: S.en, tf: S.tf, confirm_mode: S.mode, confirm_min: num(S.wait) || 2
  };
  if (btn) { btn.disabled = true; btn.textContent = '⏳ lagate hain…'; }
  let j = null;
  try {
    const r = await fetch('/api/position-exit-rule', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    j = await r.json();
  } catch (e) { j = { ok: false, msg: String(e) }; }
  if (btn) { btn.disabled = false; btn.textContent = '✓ Rule laga do'; }
  if (note) {
    if (j && j.ok) {
      S.armed = true;
      const lv = (j.levels || []).map(x =>
        `${x.side === 'target' ? '🎯' : '🛡'} ${Math.round(x.level).toLocaleString('en-IN')}`).join(' · ');
      note.style.color = '#3fb950';
      note.textContent = '✓ Rule armed' + (lv ? ' — ' + lv : '');
    } else {
      note.style.color = '#f85149';
      note.textContent = (j && j.msg) || 'apply fail';
    }
  }
}

async function tmClear(grpId) {
  const S = window._tmState[grpId]; if (!S) return;
  const note = document.getElementById('tm-note-' + grpId);
  try {
    await fetch('/api/position-exit-rule?' + S.qs, { method: 'DELETE' });
    S.armed = false;
    if (note) { note.style.color = '#8b949e'; note.textContent = 'Rule hata diya.'; }
  } catch (e) {
    if (note) { note.style.color = '#f85149'; note.textContent = 'clear fail: ' + e; }
  }
}
