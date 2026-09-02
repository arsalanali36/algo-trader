/* level_slots.js — 🎯 Level Spread Slots page (registry 03.02).
   Pure UI over /api/level-slots* (thin CRUD routes). State machine + firing are server-side
   (_ops/level_slots.py + level_slots_live.py); this file only edits config, arms/disarms and
   draws the slot's chart (LightweightCharts vendor, same lib as /trade-chart). */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const LBL = { idle: 'IDLE', armed: 'ARMED · waiting', in_zone: 'IN ZONE · watching candle',
    pattern: 'PATTERN ✓ · waiting break', firing: 'FIRING…', entered: 'ENTERED', exited: 'EXITED',
    failed: 'FAILED', expired: 'EXPIRED', cancelled: 'CANCELLED' };
  const STEP_IDX = { idle: -1, armed: 0, in_zone: 1, pattern: 2, firing: 3, entered: 4, exited: 4 };
  const D = { underlyings: {}, slots: [], prices: {}, mode: 'paper', fixed: [] };
  let ix = null, cur = null, F = null, Fid = null;     // F = working copy of the selected slot's config
  let chart = null, series = null, lines = [], chartKey = null, pollT = null;
  let paneSig = null, drawMode = false, userLines = [], fsRO = null;
  const chartHost = document.createElement('div'); chartHost.id = 'chart';
  const sigOf = (s) => s ? JSON.stringify([s.status, s.last_msg, (s.events || []).length, s.entry, s.pattern, s.result, s.level, s.zone, s.exit]) : '';
  const DAYS_KEY = 'ls_chart_days';
  const chartDays = () => { try { return Number(localStorage.getItem(DAYS_KEY)) || 5; } catch (e) { return 5; } };

  function toast(msg, kind) {
    const t = $('toast'); t.textContent = msg; t.className = 'toast ' + (kind || '');
    t.style.display = 'block'; clearTimeout(t._t); t._t = setTimeout(() => t.style.display = 'none', 3200);
  }
  async function api(path, opt) {
    const r = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opt || {}));
    if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); return { ok: false, msg: 'login' }; }
    return r.json();
  }
  const fmt = (v, d) => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: d == null ? 1 : d });
  const slotsOf = (sym) => D.slots.filter(s => s.sym === sym);
  const slot = (id) => D.slots.find(s => s.id === id);
  const isBTC = (sym) => sym === 'BTC';
  const cur$ = (sym) => isBTC(sym) ? '$' : '₹';

  // ───────────── load / poll ─────────────
  async function refresh(force) {
    const d = await api('/api/level-slots');
    if (!d.ok) { toast(d.msg || 'load fail', 'bad'); return; }
    Object.assign(D, d);
    $('modeBadge').textContent = String(D.mode || 'paper').toUpperCase();
    const syms = Object.keys(D.underlyings);
    if (!ix || !syms.includes(ix)) ix = syms[0] || null;
    if (!cur || !slot(cur) || slot(cur).sym !== ix) { const first = slotsOf(ix)[0]; cur = first ? first.id : null; }
    if (Fid !== cur || force) { F = cur ? deepCopy(slot(cur)) : null; Fid = cur; }
    render(force);
  }
  const deepCopy = (o) => JSON.parse(JSON.stringify(o));

  // ───────────── underlying boxes ─────────────
  function renderTabs() {
    const syms = Object.keys(D.underlyings);
    const src = (s) => isBTC(s) ? 'Delta · crypto' : (['NIFTY', 'BANKNIFTY'].includes(s) ? 'NSE · index' : 'NSE · stock');
    $('itabs').innerHTML = syms.map(s => `<button class="itab ${s === ix ? 'on' : ''}" data-sym="${s}">
        <span class="nm">${s}${D.fixed.includes(s) ? '' : `<span class="x" data-rm="${s}" title="remove">✕</span>`}</span>
        <span class="px">${isBTC(s) ? '$' : ''}${fmt(D.prices[s], isBTC(s) ? 0 : 1)}</span>
        <span class="foot"><span class="src">${src(s)}</span><span class="dots">${slotsOf(s).map(x => `<i class="dot ${x.status}"></i>`).join('')}</span></span>
      </button>`).join('') +
      `<div class="addwrap"><button class="add" id="addBtn">＋ Add underlying</button>
        <div class="pick" id="pick"><input id="pq" placeholder="search F&amp;O symbol… (RELIANCE, SBIN)"><div id="plist"></div></div></div>`;
    $('itabs').querySelectorAll('.itab').forEach(b => b.onclick = (e) => {
      if (e.target.dataset.rm) { removeUnderlying(e.target.dataset.rm); e.stopPropagation(); return; }
      ix = b.dataset.sym; const f = slotsOf(ix)[0]; cur = f ? f.id : null; F = cur ? deepCopy(slot(cur)) : null; Fid = cur; render();
    });
    $('addBtn').onclick = (e) => { e.stopPropagation(); $('pick').classList.toggle('on'); if ($('pick').classList.contains('on')) { renderPick(); $('pq').focus(); } };
    $('pq').oninput = renderPick;
    $('ixnote').textContent = isBTC(ix) ? '4 slots · Delta Exchange · daily expiry 12:00 UTC (17:30 IST) · 24/7 · lot 0.001 BTC · INDEX slots only (paper)'
      : `4 slots · NSE · nearest expiry (auto) · 09:15–15:30 · 3:15 EOD squareoff`;
  }
  let pickT = null;
  function renderPick() {
    clearTimeout(pickT);
    pickT = setTimeout(async () => {
      const q = ($('pq').value || '').toUpperCase();
      const d = await api('/api/level-slots/search?q=' + encodeURIComponent(q));
      const have = Object.keys(D.underlyings);
      const idx = [['NIFTY'], ['BANKNIFTY'], ['BTC']].filter(x => x[0].includes(q));
      $('plist').innerHTML = `<div class="grpname">Index / Crypto</div><ul>${idx.map(x => `<li class="${have.includes(x[0]) ? 'dis' : ''}" data-add="${x[0]}"><span>${x[0]}</span><span class="m">${have.includes(x[0]) ? 'open' : ''}</span></li>`).join('')}</ul>`
        + `<div class="grpname">F&amp;O stocks (${(d.symbols || []).length})</div><ul>${(d.symbols || []).map(x => `<li class="${have.includes(x) ? 'dis' : ''}" data-add="${x}"><span>${x}</span><span class="m">${have.includes(x) ? 'open' : ''}</span></li>`).join('') || '<li class="dis">no match</li>'}</ul>`;
      $('plist').querySelectorAll('li[data-add]').forEach(li => li.onclick = () => addUnderlying(li.dataset.add));
    }, 150);
  }
  document.addEventListener('click', (e) => { if (!e.target.closest('.addwrap')) { const p = $('pick'); if (p) p.classList.remove('on'); } });
  async function addUnderlying(sym) {
    const d = await api('/api/level-slots/underlying', { method: 'POST', body: JSON.stringify({ sym }) });
    if (!d.ok) { toast(d.msg, 'bad'); return; }
    toast(sym + ' added — 4 slots (idle)', 'ok'); ix = sym; cur = null; await refresh(true);
  }
  async function removeUnderlying(sym) {
    if (!confirm(sym + ' hatayen? (armed slot ho to refuse hoga)')) return;
    const d = await api('/api/level-slots/underlying/' + sym, { method: 'DELETE' });
    if (!d.ok) { toast(d.msg, 'bad'); return; }
    if (ix === sym) ix = null; await refresh(true);
  }

  // ───────────── slot tabs + pane ─────────────
  function render(force) {
    renderTabs();
    // poll re-render must not steal focus / wipe an input mid-typing
    const ae = document.activeElement;
    const editing = !force && ae && ['INPUT', 'SELECT'].includes(ae.tagName) && $('pane').contains(ae);
    if (editing) { renderLog(); return; }
    const csig = sigOf(slot(cur));
    if (!force && paneSig === csig && $('chart') && chartHost.parentNode) { renderStabs(); renderLog(); patchStatus(); loadChart(false); return; }
    paneSig = csig;
    renderStabs();
    renderPane();
    renderLog();
  }
  function patchStatus() { const s = slot(cur); const st = document.querySelector('.ft .st'); if (s && st) { st.textContent = LBL[s.status] || s.status; st.className = 'st ' + s.status; } }
  function renderStabs() {
    const sl = slotsOf(ix);
    $('stabs').innerHTML = sl.map((s, k) => `<div class="stab ${s.id === cur ? 'on' : ''} ${k === 2 ? 'grp-start' : ''}" data-id="${s.id}">
        <span class="k"><i class="dot ${s.status}"></i> ${s.kind === 'idx' ? 'INDEX' : 'PREMIUM'} ${s.slot[1]}</span>
        <span class="n">${slotName(s)}</span>
        <span class="s">${s.status !== 'idle' ? (LBL[s.status] || s.status) : (s.level ? 'idle · lvl ' + fmt(s.level) : 'not set')}</span></div>`).join('');
    $('stabs').querySelectorAll('.stab').forEach(el => el.onclick = () => { cur = el.dataset.id; F = deepCopy(slot(cur)); Fid = cur; render(true); });
  }
  function slotName(s) {
    const bear = s.from_dir !== 'above';
    const base = bear ? 'Resistance → Bear Call' : 'Support → Bull Put';
    if (s.kind === 'prem') { const c = s.contract; return (c ? `${c.strike} ${c.opt} · ` : '') + (bear ? 'premium ↑ → Sell CE' : 'premium ↓ → Sell PE'); }
    return base;
  }
  const R = (l, c) => `<div class="row"><label>${l}</label>${c}</div>`;
  // valid_till: server keeps 'HH:MM' (today) or 'YYYY-MM-DD HH:MM'; the picker needs 'YYYY-MM-DDTHH:MM'
  const todayIST = () => { const d = new Date(Date.now() + (330 + new Date().getTimezoneOffset()) * 60000); return d.toISOString().slice(0, 10); };
  const vtLocal = (v) => { v = String(v || '14:30'); return v.length > 5 ? v.replace(' ', 'T').slice(0, 16) : todayIST() + 'T' + v; };
  const vtServer = (v) => String(v || '').replace('T', ' ').slice(0, 16);
  const on = (cond) => cond ? 'on' : '';
  function renderPane() {
    const s = slot(cur);
    if (!s || !F) { $('pane').innerHTML = '<div class="chartmsg">koi slot nahi — ＋ Add underlying</div>'; return; }
    const isIdx = s.kind === 'idx', bear = F.from_dir !== 'above', C = cur$(s.sym), sym = s.sym;
    const ex = F.exit || {}, en = ex.enabled || {};
    const zoneUnit = F.zone_unit || 'pt';
    const lo = zoneLo(F), hi = zoneHi(F);
    const n = STEP_IDX[s.status] == null ? -1 : STEP_IDX[s.status];
    const steps = ['Waiting', 'In zone', 'Pattern', bear ? 'Low break' : 'High break', 'Entered'].map((t, k) => {
      let c = ''; if (n >= 0 && k < n) c = 'done'; if (n >= 0 && k === n) c = (s.status === 'entered' || s.status === 'exited') ? 'done' : 'now';
      if (s.status === 'failed' && k === 4) c = 'bad';
      return `<div class="step ${c}">${t}</div>`; }).join('');
    const e = s.entry || {};
    const legSell = e.legs ? e.legs.find(l => l.side === 'SELL') : null, legBuy = e.legs ? e.legs.find(l => l.side === 'BUY') : null;
    const editable = !['armed', 'in_zone', 'pattern', 'firing'].includes(s.status);

    $('pane').innerHTML = `
    <div class="chartbox"><div class="ch"><b id="chTitle">${isIdx ? sym + ' spot' : (F.contract ? F.contract.trad_sym || (F.contract.strike + ' ' + F.contract.opt) : 'contract?')}</b>
       <span class="tf" id="chTf">${['1m', '3m', '5m', '15m'].map(t => `<button class="${on(t === (F.tf || '5m'))}" data-tf="${t}">${t}</button>`).join('')}</span>
       <span class="lg"><i style="border-color:#58a6ff"></i>level</span><span class="lg"><i style="border-color:#58a6ff;border-top-style:dashed"></i>zone ±</span>
       <span class="lg"><i style="border-color:var(--red);border-top-style:dashed"></i>SL</span><span class="lg"><i style="border-color:var(--green);border-top-style:dashed"></i>Target</span>
       <span class="lg" style="color:var(--yellow)">▼ pattern</span><span class="lg" style="color:var(--green)">▲ entry</span>
       <span style="margin-left:auto" id="chPx"></span>
       <span class="tf" id="chDays">${[1, 5, 10, 30].map(d => `<button class="${on(d === chartDays())}" data-days="${d}">${d}d</button>`).join('')}</span>
       <button class="cbtn ${on(drawMode)}" id="drawBtn" title="click chart → horizontal line">＋ Line</button>
       <button class="cbtn" id="rstBtn" title="reset view (Alt+R)">⟲</button>
       <button class="cbtn" id="fsBtn" title="fullscreen (Esc)">⛶</button></div>
      <div id="chartslot"></div><div class="dlines" id="dlines" style="display:none"></div></div>
    <div class="two">
      <div class="box"><h3>Entry <span>${sym} · ${isIdx ? 'spot level' : 'premium level'}</span></h3>
        <div class="sec"><div class="rows">
          ${isIdx ? '' : R('Contract', `<div class="zone" style="grid-template-columns:90px 1fr"><div class="grp" id="optGrp"><button class="${on((F.contract || {}).opt !== 'PE')}" data-opt="CE">CE</button><button class="${on((F.contract || {}).opt === 'PE')}" data-opt="PE">PE</button></div><select id="strikeSel"><option value="">strike…</option></select></div>`)}
          ${R(isIdx ? 'Key level (spot)' : 'Key level (' + C + ')', `<input id="fLevel" value="${F.level ?? ''}" placeholder="${isIdx ? 'e.g. 24700' : 'premium'}">`)}
          ${R('Price comes from', `<div class="grp" id="fromGrp"><button class="${on(bear)}" data-from="below">▲ below</button><button class="${on(!bear)}" data-from="above">▼ above</button></div>`)}
          ${R('Level zone ±', `<div class="zone"><input id="fZone" value="${F.zone ?? ''}" placeholder="0"><div class="grp" id="zuGrp"><button class="${on(zoneUnit === 'pt')}" data-zu="pt">${isIdx ? 'pt' : C}</button><button class="${on(zoneUnit === 'pct')}" data-zu="pct">%</button></div></div>`)}
          ${R('Pattern', `<div class="chk">${['engulf', 'hammer', 'inside'].map(p => `<label><input type="checkbox" data-pat="${p}" ${(F.patterns || []).includes(p) ? 'checked' : ''}> ${p}</label>`).join('')}</div>`)}
          ${R('Break confirm', `<div class="grp" id="ecGrp"><button class="${on((F.entry_confirm || 'close') === 'close')}" data-ec="close">candle close</button><button class="${on(F.entry_confirm === 'wick')}" data-ec="wick">wick</button></div>`)}
          ${isIdx ? R('Sell leg', `<select id="fSell"><option value="atm" ${F.sell_leg !== 'level' ? 'selected' : ''}>ATM (at fire-time spot)</option><option value="level" ${F.sell_leg === 'level' ? 'selected' : ''}>Strike nearest to level</option></select>`) : ''}
          ${R('Hedge delta', `<input id="fHd" value="${F.hedge_delta ?? 0.25}">`)}
          ${R(isBTC(sym) ? 'Lots (0.001 BTC)' : 'Lots', `<input id="fLots" value="${F.lots ?? 1}">`)}
          ${R('Candle TF', `<select id="fTf">${['1m', '3m', '5m', '15m'].map(t => `<option ${t === (F.tf || '5m') ? 'selected' : ''}>${t}</option>`).join('')}</select>`)}
          ${R('Valid till', `<input id="fVt" type="datetime-local" value="${vtLocal(F.valid_till)}" step="60">`)}
        </div></div>
        <div class="sec hint" id="hint">${hintText(F, s)}</div>
      </div>
      <div class="box"><h3>Exit <span>${isIdx ? 'index triggers' : 'premium triggers'} · Trade Manager</span></h3>
        <div class="sec">
          <div class="rule ${en.rs ? '' : 'off'}"><div class="tog ${on(en.rs)}" data-en="rs"></div><div class="lab"><b>${C} Combined premium</b><small>poore basket ka MTM (pehle chhuye → poori group off)</small></div><input data-ex="rs_sl" placeholder="${C} SL" value="${ex.rs_sl ?? ''}"><input data-ex="rs_tg" placeholder="${C} Target" value="${ex.rs_tg ?? ''}"></div>
          <div class="rule ${en.ip ? '' : 'off'}"><div class="tog ${on(en.ip)}" data-en="ip"></div><div class="lab"><b>Index points</b><small>entry ke spot se ± (server pe freeze)</small></div><input data-ex="ip_sl" placeholder="pt SL" value="${ex.ip_sl ?? ''}"><input data-ex="ip_tg" placeholder="pt Target" value="${ex.ip_tg ?? ''}"></div>
          <div class="rule ${en.il ? '' : 'off'}"><div class="tog ${on(en.il)}" data-en="il"></div><div class="lab"><b>Index price</b><small>apna level — cross pe exit</small></div><input data-ex="il_sl" placeholder="SL level" value="${ex.il_sl ?? ''}"><input data-ex="il_tg" placeholder="Target level" value="${ex.il_tg ?? ''}"></div>
          <div class="perpt">jo pehle chhuye wahi poori group square off — shorts pehle, wings baad${isBTC(sym) ? ' · Delta paper (own engine, wick mode)' : ''}</div>
        </div>
        <div class="sec" id="projSec"><div style="display:flex;align-items:center;gap:8px;margin-bottom:6px"><span style="font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)">Projection · abhi ke strike/Δ/margin se</span><button class="cbtn" id="projBtn">🔄 calc</button></div><div id="proj" class="perpt">— (calc dabao)</div></div>
        <div class="sec"><div style="font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:8px">Exit confirmation (index triggers)</div>
          <div class="tfrow">TF = slot TF (${F.tf || '5m'})</div>
          <div class="radios" id="cmGrp">
            ${[['wick', 'Wick chhuye = turant', 'broker jaisa · ~3s loop'], ['close', 'Candle close pe', 'touch se kuch nahi — close bahar hona chahiye'], ['wait', 'Close + N min', 'fake breakout filter']].map(([v, a, b]) =>
              `<div class="radio ${on((ex.confirm_mode || 'close') === v)}" data-cm="${v}"><span class="r"></span><div><b>${a}${v === 'wait' ? ` <input data-ex="confirm_min" value="${ex.confirm_min ?? 2}"> min` : ''}</b><small>${b}</small></div></div>`).join('')}
          </div>
        </div>
      </div>
    </div>
    <div class="sec" style="padding:0 12px 12px;border:0">
      <div class="steps">${steps}</div>
      <div class="legs">
        <div class="leg"><span class="k">SELL</span><span class="s">${legSell ? (legSell.trad_sym || legSell.symbol) + ' @' + legSell.price : (isIdx ? (bear ? 'CE' : 'PE') + ' · ' + (F.sell_leg === 'level' ? 'strike near level' : 'ATM at fire') : (F.contract ? (F.contract.trad_sym || F.contract.strike + ' ' + F.contract.opt) : '—'))}</span><span class="m">${legSell ? 'filled' : 'resolved at entry'}</span></div>
        <div class="leg"><span class="k">BUY (hedge)</span><span class="b">${legBuy ? (legBuy.trad_sym || legBuy.symbol) + ' @' + (legBuy.price ?? legBuy.entry_fill) : `≈ ${F.hedge_delta ?? 0.25}Δ wing`}</span><span class="m">${legBuy ? 'filled' : 'IV back-solved from sold leg, delta walk'}</span></div>
        <div class="leg"><span class="k">After entry</span><span>${e.group_id ? e.group_id : '—'}</span><span class="m">${e.rule_key ? 'Trade Manager rule armed' : (isBTC(sym) ? 'own paper exit engine' : 'exit rules → Trade Manager group')}${e.credit != null ? ' · net ' + e.credit : ''}${s.result ? ' · ' + s.result : ''}</span></div>
      </div>
    </div>
    <div class="ft"><span class="st ${s.status}">${LBL[s.status] || s.status}</span><span style="font-size:11px;color:var(--muted)">${s.last_msg || ''}</span><span class="sp"></span>
      <button class="btn" id="saveBtn" ${editable ? '' : 'disabled title="pehle disarm karo"'}>💾 Save</button>
      ${['armed', 'in_zone', 'pattern'].includes(s.status) ? `<button class="btn dan" id="disarmBtn">DISARM</button>` : `<button class="btn pri" id="armBtn" ${s.status === 'firing' ? 'disabled' : ''}>ARM</button>`}
    </div>`;

    // ── bind
    const bind = (id, k, num) => { const el = $(id); if (el) el.oninput = () => { F[k] = num ? (el.value === '' ? null : Number(el.value)) : el.value; $('hint').innerHTML = hintText(F, s); }; };
    bind('fLevel', 'level', true); bind('fZone', 'zone', true); ['fLevel', 'fZone'].forEach(id => { const el = $(id); if (el) el.addEventListener('input', redrawSoon); }); bind('fHd', 'hedge_delta', true); bind('fLots', 'lots', true); bind('fVt', 'valid_till');
    if ($('fTf')) $('fTf').onchange = () => { F.tf = $('fTf').value; loadChart(true); };
    if ($('fSell')) $('fSell').onchange = () => F.sell_leg = $('fSell').value;
    grp('fromGrp', 'from', (v) => { F.from_dir = v; render(); });
    grp('zuGrp', 'zu', (v) => { F.zone_unit = v; $('hint').innerHTML = hintText(F, s); redrawSoon(); });
    grp('ecGrp', 'ec', (v) => F.entry_confirm = v);
    grp('optGrp', 'opt', (v) => { F.contract = Object.assign({}, F.contract || {}, { opt: v, sec_id: null, strike: null }); loadStrikes(); });
    document.querySelectorAll('[data-pat]').forEach(c => c.onchange = () => { F.patterns = [...document.querySelectorAll('[data-pat]:checked')].map(x => x.dataset.pat); });
    document.querySelectorAll('[data-en]').forEach(t => t.onclick = () => { F.exit = F.exit || {}; F.exit.enabled = F.exit.enabled || {}; F.exit.enabled[t.dataset.en] = !F.exit.enabled[t.dataset.en]; t.classList.toggle('on'); t.parentNode.classList.toggle('off', !t.classList.contains('on')); redrawSoon(); });
    document.querySelectorAll('[data-ex]').forEach(i => i.oninput = () => { F.exit = F.exit || {}; F.exit[i.dataset.ex] = i.value === '' ? null : Number(i.value); redrawSoon(); });
    document.querySelectorAll('#cmGrp .radio').forEach(r => r.onclick = (ev) => { if (ev.target.tagName === 'INPUT') return; F.exit = F.exit || {}; F.exit.confirm_mode = r.dataset.cm; document.querySelectorAll('#cmGrp .radio').forEach(x => x.classList.remove('on')); r.classList.add('on'); });
    document.querySelectorAll('#chTf button').forEach(b => b.onclick = () => { F.tf = b.dataset.tf; if ($('fTf')) $('fTf').value = b.dataset.tf; document.querySelectorAll('#chTf button').forEach(x => x.classList.remove('on')); b.classList.add('on'); loadChart(true); });
    if ($('saveBtn')) $('saveBtn').onclick = save;
    if ($('armBtn')) $('armBtn').onclick = arm;
    if ($('disarmBtn')) $('disarmBtn').onclick = disarm;
    if (!isIdx) loadStrikes(true);
    $('chartslot').appendChild(chartHost);
    document.querySelectorAll('#chDays button').forEach(b => b.onclick = () => { try { localStorage.setItem(DAYS_KEY, b.dataset.days); } catch (e) {} document.querySelectorAll('#chDays button').forEach(x => x.classList.remove('on')); b.classList.add('on'); loadChart(true); });
    $('drawBtn').onclick = () => { drawMode = !drawMode; $('drawBtn').classList.toggle('on', drawMode); toast(drawMode ? 'chart pe click karo → line' : 'line mode off'); };
    $('fsBtn').onclick = toggleFs;
    $('projBtn').onclick = () => loadProjection(true);
    if (window._projCache && window._projCache.id === cur) renderProjection(window._projCache.d);
    $('rstBtn').onclick = () => resetView();
    loadUserLines();
    loadChart(chartKey !== cur + '|' + (F.tf || '5m'));
  }
  let redrawT = null, projT = null;
  function redrawSoon() { clearTimeout(redrawT); redrawT = setTimeout(() => loadChart(false), 250); clearTimeout(projT); projT = setTimeout(() => { if (window._projCache && window._projCache.id === cur) loadProjection(false); }, 900); }
  const money = (v) => (v == null || isNaN(v)) ? '—' : (v < 0 ? '−' : '+') + '₹' + Math.abs(Math.round(v)).toLocaleString('en-IN');
  async function loadProjection(user) {
    const el = $('proj'); if (!el || !F) return;
    if (!F.level) { el.textContent = 'pehle Key level do'; return; }
    if (user) el.textContent = 'resolving legs…';
    const body = { level: F.level, zone: F.zone, zone_unit: F.zone_unit, from_dir: F.from_dir, sell_leg: F.sell_leg, hedge_delta: F.hedge_delta, lots: F.lots, exit: F.exit, contract: F.contract };
    const d = await api('/api/level-slots/' + encodeURIComponent(cur) + '/preview', { method: 'POST', body: JSON.stringify(body) });
    window._projCache = { id: cur, d };
    renderProjection(d);
  }
  function renderProjection(d) {
    const el = $('proj'); if (!el) return;
    if (!d || !d.ok) { el.innerHTML = '<span style="color:var(--red)">' + ((d && d.msg) || 'fail') + '</span>'; return; }
    const C = d.cur || '₹';
    const legs = (d.legs || []).map(l => `<div><span style="color:${l.side === 'SELL' ? 'var(--red)' : 'var(--green)'}">${l.side}</span> ${l.sym} · strike ${fmt(l.strike, 0)} · LTP ${C}${fmt(l.ltp, 2)} · Δ ${l.delta == null ? '—' : l.delta}</div>`).join('');
    const rows = (d.projection || []).map(p => `<div><span style="color:${p.side === 'sl' ? 'var(--red)' : 'var(--green)'}">${p.side === 'sl' ? 'SL' : 'Target'}</span> <span style="color:var(--muted)">${p.src === 'rs' ? C + ' combined' : p.src === 'ip' ? 'index pt' : 'index price'}${p.pts != null ? ' · ' + fmt(p.pts, 1) + ' pt' : ''}${p.level != null ? ' @ ' + fmt(p.level, 1) : ''}</span> → <b style="color:${p.rs < 0 ? 'var(--red)' : 'var(--green)'}">${C === '₹' ? money(p.rs) : (p.rs < 0 ? '−' : '+') + '₹' + Math.abs(Math.round(p.rs)).toLocaleString('en-IN')}</b></div>`).join('') || '<div style="color:var(--muted)">koi exit line enabled/typed nahi</div>';
    el.innerHTML = `${legs}
      <div style="margin-top:4px">net |Δ| <b>${d.net_delta == null ? '—' : d.net_delta}</b>/unit · <b>${d.rs_per_pt == null ? '—' : '₹' + fmt(d.rs_per_pt, 1)}</b> per index pt · qty ${d.qty}${d.lot_size ? ' (lot ' + d.lot_size + ')' : ''} · credit ${C}${fmt(d.credit_unit, 2)}/unit = <b>${money(d.credit_rs)}</b>${d.atm_iv ? ' · IV ' + (d.atm_iv * 100).toFixed(1) + '%' : ''}</div>
      <div>margin (entry) <b style="color:var(--text)">₹${fmt(d.margin_hedged, 0)}</b> hedged${d.margin_naked ? ' <span style="color:var(--muted)">(naked hota ₹' + fmt(d.margin_naked, 0) + ')</span>' : ''} · max loss (wing cap) <b>₹${fmt(d.max_loss_rs, 0)}</b></div>
      <div style="margin-top:4px;border-top:1px solid var(--border2);padding-top:4px">${rows}</div>
      <div style="color:var(--muted);margin-top:3px">${d.note || ''}</div>`;
  }
  function grp(id, key, fn) { const g = $(id); if (!g) return; g.querySelectorAll('button').forEach(b => b.onclick = () => { g.querySelectorAll('button').forEach(x => x.classList.remove('on')); b.classList.add('on'); fn(b.dataset[key]); }); }
  const zoneAbs = (f) => f.zone_unit === 'pct' ? (Number(f.level) || 0) * (Number(f.zone) || 0) / 100 : (Number(f.zone) || 0);
  const zoneLo = (f) => (Number(f.level) || 0) - zoneAbs(f), zoneHi = (f) => (Number(f.level) || 0) + zoneAbs(f);
  function hintText(f, s) {
    if (!f.level) return 'Key level set karo, phir Save → ARM.';
    const bear = f.from_dir !== 'above', C = cur$(s.sym);
    const what = s.kind === 'idx' ? `Spot band <b>${fmt(zoneLo(f))} – ${fmt(zoneHi(f))}</b>` : `Premium band <b>${C}${fmt(zoneLo(f), 2)} – ${C}${fmt(zoneHi(f), 2)}</b>`;
    return `${what} me closed candle → pattern (${(f.patterns || []).join('/') || '—'}) → <b>agli candle us candle ka ${bear ? 'LOW' : 'HIGH'} ${(f.entry_confirm || 'close') === 'wick' ? 'wick se' : 'close pe'} tode</b> → BUY ${bear ? 'CE' : 'PE'} wing (≈${f.hedge_delta ?? 0.25}Δ) PEHLE, phir SELL ${s.kind === 'idx' ? 'ATM ' + (bear ? 'CE' : 'PE') : 'chosen contract'} × ${f.lots || 1} lot.`;
  }
  async function loadStrikes(keep) {
    const sel = $('strikeSel'); if (!sel) return;
    const opt = (F.contract || {}).opt || 'CE';
    sel.innerHTML = '<option value="">loading…</option>';
    const d = await api(`/api/level-slots/contracts?sym=${ix}&opt=${opt}`);
    if (!d.ok) { sel.innerHTML = `<option value="">${d.msg || 'no data'}</option>`; return; }
    const curSec = keep ? String((F.contract || {}).sec_id || '') : '';
    sel.innerHTML = '<option value="">strike…</option>' + d.rows.map(r => `<option value="${r.sec_id}" ${r.sec_id === curSec ? 'selected' : ''}>${r.strike} ${opt}${r.offset === 0 ? ' (ATM)' : ''} · lot ${r.lot} · ${r.expiry}</option>`).join('');
    sel.onchange = () => { const r = d.rows.find(x => x.sec_id === sel.value); F.contract = r ? { opt, strike: r.strike, sec_id: r.sec_id, trad_sym: r.trad_sym, symbol: ix } : null; };
  }

  // ───────────── actions ─────────────
  async function save() {
    if (!F) return;
    const body = { level: F.level, zone: F.zone, zone_unit: F.zone_unit, from_dir: F.from_dir, patterns: F.patterns, entry_confirm: F.entry_confirm,
      sell_leg: F.sell_leg, hedge_delta: F.hedge_delta, lots: F.lots, tf: F.tf, valid_till: F.valid_till, contract: F.contract, exit: F.exit };
    const d = await api('/api/level-slots/' + cur, { method: 'POST', body: JSON.stringify(body) });
    if (!d.ok) { toast(d.msg, 'bad'); return; }
    toast('saved', 'ok'); await refresh(true);
  }
  async function arm() {
    const s = slot(cur);
    if (JSON.stringify(cfgOf(F)) !== JSON.stringify(cfgOf(s))) { await save(); if (!slot(cur).level) return; }
    const d = await api('/api/level-slots/' + cur + '/arm', { method: 'POST' });
    toast(d.msg, d.ok ? 'ok' : 'bad'); await refresh(true);
  }
  const cfgOf = (s) => s ? ({ level: s.level, zone: s.zone, zone_unit: s.zone_unit, from_dir: s.from_dir, patterns: s.patterns, entry_confirm: s.entry_confirm, sell_leg: s.sell_leg, hedge_delta: s.hedge_delta, lots: s.lots, tf: s.tf, valid_till: s.valid_till, contract: s.contract, exit: s.exit }) : null;
  async function disarm() {
    const d = await api('/api/level-slots/' + cur + '/disarm', { method: 'POST' });
    toast(d.msg, d.ok ? 'ok' : 'bad'); await refresh(true);
  }

  // ───────────── chart ─────────────
  function ensureChart() {
    const el = chartHost; if (!el.parentNode || typeof LightweightCharts === 'undefined') return false;
    if (chart && series) return true;
    el.innerHTML = '';
    chart = LightweightCharts.createChart(el, { width: el.clientWidth, height: el.clientHeight || 280,
      layout: { background: { color: '#0d1117' }, textColor: '#8b949e' }, grid: { vertLines: { color: '#161b22' }, horzLines: { color: '#161b22' } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#30363d' }, rightPriceScale: { borderColor: '#30363d' } });
    chart._el = el;
    series = chart.addCandlestickSeries({ upColor: '#3fb950', downColor: '#f85149', borderVisible: false, wickUpColor: '#3fb950', wickDownColor: '#f85149' });
    window.addEventListener('resize', () => { try { chart.applyOptions({ width: el.clientWidth, height: el.clientHeight || 280 }); } catch (e) {} });
    chart.subscribeClick((param) => {
      if (!drawMode || !param || !param.point) return;
      const price = series.coordinateToPrice(param.point.y); if (price == null) return;
      userLines.push(Math.round(price * 100) / 100); saveUserLines(); drawUserLines(); renderDlines();
    });
    return true;
  }
  // ── user-drawn horizontal lines (per slot, localStorage) ──
  let ulObjs = [];
  const ulKey = () => 'ls_lines:' + cur;
  function loadUserLines() { try { userLines = JSON.parse(localStorage.getItem(ulKey()) || '[]'); } catch (e) { userLines = []; } renderDlines(); }
  function saveUserLines() { try { localStorage.setItem(ulKey(), JSON.stringify(userLines)); } catch (e) {} }
  function drawUserLines() {
    if (!series) return;
    ulObjs.forEach(l => { try { series.removePriceLine(l); } catch (e) {} }); ulObjs = [];
    userLines.forEach(p => ulObjs.push(series.createPriceLine({ price: p, color: '#e3b341', lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: 'L' })));
  }
  function renderDlines() {
    const el = $('dlines'); if (!el) return;
    el.style.display = userLines.length ? 'flex' : 'none';
    el.innerHTML = userLines.map((p, i) => `<span class="dline"><b>${fmt(p, 2)}</b><span data-lv="${i}" title="is line ko Key level banao">→ level</span><span data-rm="${i}" title="hatao">✕</span></span>`).join('');
    el.querySelectorAll('[data-lv]').forEach(x => x.onclick = () => { const p = userLines[+x.dataset.lv]; F.level = p; const inp = $('fLevel'); if (inp) { inp.value = p; inp.dispatchEvent(new Event('input')); } loadChart(false); toast('Key level = ' + p + ' (Save karo)', 'ok'); });
    el.querySelectorAll('[data-rm]').forEach(x => x.onclick = () => { userLines.splice(+x.dataset.rm, 1); saveUserLines(); drawUserLines(); renderDlines(); });
  }
  // ── fullscreen ──
  function toggleFs() {
    const box = document.querySelector('.chartbox'); if (!box) return;
    const onFs = box.classList.toggle('fs');
    document.body.style.overflow = onFs ? 'hidden' : '';
    const fit = () => { try { chart && chart.applyOptions({ width: chartHost.clientWidth, height: chartHost.clientHeight || 280 }); } catch (e) {} };
    if (fsRO) { fsRO.disconnect(); fsRO = null; }
    if (onFs && window.ResizeObserver) { fsRO = new ResizeObserver(fit); fsRO.observe(chartHost); }
    setTimeout(fit, 50);
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { const box = document.querySelector('.chartbox.fs'); if (box) toggleFs(); } });
  async function loadChart(fit) {
    const s = slot(cur); if (!s || !F) return;
    const tf = F.tf || s.tf || '5m';
    const key = cur + '|' + tf + '|' + chartDays();
    const d = await api(`/api/level-slots/${encodeURIComponent(cur)}/chart?tf=${tf}&days=${chartDays()}`);
    const el = chartHost; if (!el.parentNode) return;
    if (!d.ok || !d.bars || !d.bars.length) { chart = null; series = null; el.innerHTML = `<div class="chartmsg">${d.msg || 'candles nahi (market band / contract set nahi)'}</div>`; return; }
    if (!ensureChart()) return;
    if ($('chPx')) $('chPx').textContent = d.price != null ? 'last ' + fmt(d.price, 2) : '';
    series.setData(d.bars.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })));
    lines.forEach(l => { try { series.removePriceLine(l); } catch (e) {} }); lines = [];
    const add = (price, color, title, dashed) => { if (price == null || isNaN(price)) return; lines.push(series.createPriceLine({ price: Number(price), color, lineWidth: dashed ? 1 : 2, lineStyle: dashed ? 2 : 0, axisLabelVisible: true, title })); };
    if (F.level) { add(F.level, '#58a6ff', 'LVL'); if (zoneAbs(F) > 0) { add(zoneLo(F), '#58a6ff', 'zone', true); add(zoneHi(F), '#58a6ff', 'zone', true); } }
    const ss = d.slot || s, e = ss.entry || {}, ex = ss.exit || {}, en = ex.enabled || {};
    if (ss.pattern && ss.pattern.break_level) add(ss.pattern.break_level, '#d29922', 'break', true);
    // SL / Target lines — after entry anchored on the REAL entry spot (server-frozen); before
    // entry PROJECTED from the key level (entry lagbhag level ke paas hi hoti hai) so the user
    // sees where the exits will sit while setting the slot. Uses the UNSAVED form values (F).
    if (ss.kind === 'idx') {
      const exF = F.exit || {}, enF = exF.enabled || {};
      const entered = !!e.spot, anchor = entered ? e.spot : Number(F.level);
      const dir = entered ? (e.dir || (ss.from_dir === 'above' ? 1 : -1)) : (F.from_dir === 'above' ? 1 : -1);
      const sfx = entered ? '' : ' (proj)';
      if (anchor) {
        if (enF.ip) { if (exF.ip_sl) add(anchor - dir * exF.ip_sl, '#f85149', 'SL' + sfx, true); if (exF.ip_tg) add(anchor + dir * exF.ip_tg, '#3fb950', 'TG' + sfx, true); }
        if (enF.il) { if (exF.il_sl) add(exF.il_sl, '#f85149', 'SL', true); if (exF.il_tg) add(exF.il_tg, '#3fb950', 'TG', true); }
      }
    }
    const mk = [];
    if (ss.pattern && ss.pattern.ts) mk.push({ time: ss.pattern.ts, position: ss.from_dir === 'above' ? 'belowBar' : 'aboveBar', color: '#d29922', shape: ss.from_dir === 'above' ? 'arrowUp' : 'arrowDown', text: ss.pattern.name });
    if (e.ts && d.bars.length) { const tfs = { '1m': 60, '3m': 180, '5m': 300, '15m': 900 }[tf] || 300; const t = Math.floor((e.ts + 19800) / tfs) * tfs; const bar = d.bars.find(b => b.time === t) || d.bars[d.bars.length - 1]; mk.push({ time: bar.time, position: ss.from_dir === 'above' ? 'belowBar' : 'aboveBar', color: '#3fb950', shape: ss.from_dir === 'above' ? 'arrowUp' : 'arrowDown', text: 'ENTRY' }); }
    try { series.setMarkers(mk.sort((a, b) => a.time - b.time)); } catch (err) {}
    drawUserLines();
    // fitContent ONLY on slot/tf/days change — a poll refresh must never reset the user's zoom/scale
    if (fit || chartKey !== key) { resetView(); chartKey = key; }
  }
  // Reset BOTH axes — a manually-dragged price scale turns autoScale OFF and stays that way
  // across setData(), which is exactly the "BTC ke baad RELIANCE squeezed" symptom.
  function resetView() {
    if (!chart) return;
    try { chart.priceScale('right').applyOptions({ autoScale: true }); } catch (e) {}
    try { chart.timeScale().resetTimeScale(); } catch (e) {}
    try { chart.timeScale().fitContent(); } catch (e) {}
  }
  document.addEventListener('keydown', (e) => { if (e.altKey && (e.key === 'r' || e.key === 'R')) { e.preventDefault(); resetView(); } });

  function renderLog() {
    const s = slot(cur);
    $('log').textContent = s && s.events && s.events.length ? s.events.slice().reverse().join('\n') : '—';
  }

  // ───────────── boot ─────────────
  window.LS = { refresh, D: () => D, lines: () => lines.map(l => { try { return l.options().title + '@' + l.options().price; } catch (e) { return '?'; } }) };
  refresh(true);
  pollT = setInterval(() => { if (!document.hidden) { const keepF = F; refresh(false).then(() => { /* keep unsaved edits */ if (keepF && Fid === cur) F = keepF; }); } }, 10000);
})();
