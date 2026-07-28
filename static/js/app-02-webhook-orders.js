// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // ── WEBHOOK TAB (multi-strategy) ──
    let WH_TOKEN = '', WH_PUBLIC_BASE = '', _whTimer = null, _whLtpTimer = null;
    let WH_STRATS = {}, WH_GLOBAL = {}, WH_SEL = null, WH_MODE = 'paper', WH_OPTMODE = 'SELL';
    function whEnter() {
      renderWebhookTab(); whStatusPoll(); whLtpPoll();
      _whTimer = setInterval(whStatusPoll, 4000);   // status/log — local, no Dhan
      _whLtpTimer = setInterval(whLtpPoll, 15000);     // LTP — light on Dhan (rate limit)
    }
    function whLeave() {
      if (_whTimer) { clearInterval(_whTimer); _whTimer = null; }
      if (_whLtpTimer) { clearInterval(_whLtpTimer); _whLtpTimer = null; }
    }

    function _whDefaults() {
      return {
        active: true, broker: 'dhan', mode: 'paper', instrument: 'options', strike_offset: 0, qty: 1,
        opt_action: 'SELL', long_opt_type: 'PE', short_opt_type: 'CE', sl_type: 'aggressive',
        max_trades_per_day: 2, no_entry_after: '15:15', squareoff_at: '15:15'
      };
    }

    async function renderWebhookTab() {
      try { const r = await fetch('/api/config'); GLOBAL_CONFIG = await r.json(); } catch (e) { }
      const whs = (GLOBAL_CONFIG && GLOBAL_CONFIG.webhooks) || {};
      WH_GLOBAL = Object.assign({ secret_token: '', daily_amount_cap: 5000, global_max_trades: 0 }, whs.global || {});
      WH_STRATS = {};
      Object.keys(whs).forEach(k => { if (k !== 'global') WH_STRATS[k] = whs[k]; });
      if (!Object.keys(WH_STRATS).length) WH_STRATS['default'] = _whDefaults();
      WH_TOKEN = WH_GLOBAL.secret_token || '';
      WH_PUBLIC_BASE = WH_GLOBAL.public_webhook_base
        || ((GLOBAL_CONFIG.webhook_v1 || {}).public_webhook_base) || '';
      const set = (id, v) => { const el = document.getElementById(id); if (el != null && v != null) el.value = v; };
      set('wh-daily_cap', WH_GLOBAL.daily_amount_cap != null ? WH_GLOBAL.daily_amount_cap : 5000);
      set('wh-global_max', WH_GLOBAL.global_max_trades != null ? WH_GLOBAL.global_max_trades : 0);
      whRenderConn();
      if (!WH_SEL || !WH_STRATS[WH_SEL]) WH_SEL = Object.keys(WH_STRATS).find(n => !regHidden(n)) || Object.keys(WH_STRATS)[0];
      whRenderStratList();
      whSelectStrat(WH_SEL);
    }

    function whRenderStratList() {
      const box = document.getElementById('wh-strat-list'); if (!box) return;
      box.innerHTML = '';
      // Filter at RENDER, never at load: WH_STRATS must stay complete because
      // _whSave() deletes any webhooks key absent from it, and `default` is a
      // LIVE fallback (_strat_cfg() lands on it for an unknown strategy).
      // Hiding must never become deleting.
      Object.keys(WH_STRATS).filter(n => !regHidden(n) || n === WH_SEL).forEach(name => {
        const c = WH_STRATS[name]; const sel = name === WH_SEL;
        const dot = c.active !== false ? '#3fb950' : '#6e7681';
        const sub = [c.instrument || 'options', c.broker || 'dhan', c.mode || 'paper'].join(' · ');
        const d = document.createElement('div');
        d.style.cssText = 'cursor:pointer;border-radius:6px;padding:7px 8px;border:1px solid ' + (sel ? '#1f6feb' : '#30363d') + ';background:' + (sel ? '#1f6feb22' : '#0d1117');
        d.onclick = () => whSelectStrat(name);
        d.innerHTML = '<div style="display:flex;align-items:center;gap:6px"><span style="width:7px;height:7px;border-radius:50%;background:' + dot + '"></span>'
          + '<span title="' + name.replace(/"/g, '&quot;') + '" style="font-size:12px;' + (sel ? 'font-weight:600' : '') + ';color:' + (c.active !== false ? '#e6edf3' : '#8b949e') + '">' + (regLabel(name) || name) + '</span></div>'
          + '<div style="font-size:10px;color:#8b949e;margin-top:2px">' + sub + '</div>';
        box.appendChild(d);
      });
    }

    function whSelectStrat(name) {
      if (!WH_STRATS[name]) return;
      WH_SEL = name; const c = WH_STRATS[name];
      const _hdr = document.getElementById('wh-sel-name');
      _hdr.innerText = regLabel(name) || name;
      _hdr.title = name;   // raw config_key on hover — never renamed (history)
      const set = (id, v) => { const el = document.getElementById(id); if (el != null && v != null) el.value = v; };
      set('wh-instrument', c.instrument || 'options');
      set('wh-broker', c.broker || 'dhan');
      set('wh-strike_offset', String(c.strike_offset != null ? c.strike_offset : 0));
      set('wh-qty', c.qty != null ? c.qty : 1);
      set('wh-sl_type', c.sl_type || 'aggressive');
      set('wh-max_trades_per_day', c.max_trades_per_day != null ? c.max_trades_per_day : 2);
      document.getElementById('wh-active').checked = c.active !== false;
      whSetMode(c.mode || 'paper', true);
      whSetOptMode((c.opt_action || 'SELL').toUpperCase(), true);
      whRenderStratList(); whRenderTpl();
    }

    function whAddStrat() {
      let name = prompt('Nayi webhook strategy ka naam (Pine alert ke "strategy" field se match hona chahiye):', '');
      if (name == null) return; name = name.trim(); if (!name) return;
      if (WH_STRATS[name]) { flash('Ye naam already hai', '#d29922'); whSelectStrat(name); return; }
      WH_STRATS[name] = _whDefaults();
      WH_SEL = name; whRenderStratList(); whSelectStrat(name);
      flash('“' + name + '” bana — config set karke Save dabayein', '#3fb950');
    }

    async function whRenameStrat() {
      if (!WH_SEL) return;
      let newName = prompt('Naya naam do (Pine alert ke "strategy" field se match karna chahiye):', WH_SEL);
      if (newName == null) return;
      newName = newName.trim();
      if (!newName || newName === WH_SEL) return;
      if (WH_STRATS[newName]) { flash('"' + newName + '" naam already exist karta hai', '#d29922'); return; }
      WH_STRATS[newName] = WH_STRATS[WH_SEL];
      delete WH_STRATS[WH_SEL];
      WH_SEL = newName;
      await _whPersist();
      renderWebhookTab();
      flash('"' + newName + '" rename ho gaya ✓', '#3fb950');
    }

    async function whDeleteStrat() {
      if (!WH_SEL) return;
      if (!confirm('Delete webhook strategy “' + WH_SEL + '”?')) return;
      delete WH_STRATS[WH_SEL];
      await _whPersist();
      WH_SEL = Object.keys(WH_STRATS)[0] || null;
      renderWebhookTab();
    }

    function _whReadForm() {
      const g = id => document.getElementById(id).value;
      return {
        active: document.getElementById('wh-active').checked,
        broker: g('wh-broker'), mode: WH_MODE, instrument: g('wh-instrument'),
        strike_offset: parseInt(g('wh-strike_offset')),
        qty: parseInt(g('wh-qty')),
        sl_type: g('wh-sl_type'),
        max_trades_per_day: parseInt(g('wh-max_trades_per_day')),
        opt_action: WH_OPTMODE,
        long_opt_type: WH_OPTMODE === 'SELL' ? 'PE' : 'CE',
        short_opt_type: WH_OPTMODE === 'SELL' ? 'CE' : 'PE'
      };
    }

    async function _whPersist() {
      let cfg = {}; try { const r = await fetch('/api/config'); cfg = await r.json(); } catch (e) { }
      const whs = cfg.webhooks || {};
      whs.global = Object.assign({}, whs.global || {}, {
        secret_token: WH_TOKEN,
        daily_amount_cap: parseFloat(document.getElementById('wh-daily_cap').value || 0),
        global_max_trades: parseInt(document.getElementById('wh-global_max').value || 0)
      });
      Object.keys(whs).forEach(k => { if (k !== 'global' && !WH_STRATS[k]) delete whs[k]; });
      Object.keys(WH_STRATS).forEach(k => { whs[k] = WH_STRATS[k]; });
      cfg.webhooks = whs;
      const res = await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
      GLOBAL_CONFIG = cfg;
      return res.ok;
    }

    function whRenderConn() {
      const t = document.getElementById('wh-token'); if (t) t.value = WH_TOKEN || '';
      const base = WH_PUBLIC_BASE || (location.origin + '/api/webhook/tv');
      document.getElementById('wh-url').innerText = base + '?token=' + (WH_TOKEN || '');
    }
    function whRenderTpl() {
      const sym = (document.getElementById('wh-ltp-sym') || {}).value || 'NIFTY';
      const strat = WH_SEL || 'default';
      document.getElementById('wh-tpl-entry').innerText =
        '{"id":"{{timenow}}","strategy":"STRAT","symbol":"SYM","signal":"ENTRY","action":"{{strategy.order.action}}"}'.replace('STRAT', strat).replace('SYM', sym);
      document.getElementById('wh-tpl-exit').innerText =
        '{"id":"{{timenow}}","strategy":"STRAT","symbol":"SYM","signal":"EXIT"}'.replace('STRAT', strat).replace('SYM', sym);
    }

    function whSetMode(m, silent) {
      WH_MODE = m;
      document.querySelectorAll('#wh-mode-seg .wh-seg').forEach(e => {
        const on = e.dataset.mode === m; e.classList.toggle('on', on); e.style.color = on ? '#fff' : '#8b949e';
      });
      if (!silent && m === 'live') flash('⚠️ Live mode — Save karne pe real orders lagenge', '#d29922');
    }
    function whSetOptMode(m, silent) {
      WH_OPTMODE = m;
      document.querySelectorAll('#wh-optmode-seg .wh-optseg').forEach(e => {
        const on = e.dataset.m === m; e.classList.toggle('on', on); e.style.color = on ? '#fff' : '#8b949e';
      });
    }

    function whCopy(id) {
      const el = document.getElementById(id);
      const t = (el.tagName === 'INPUT') ? el.value : el.innerText;
      navigator.clipboard.writeText(t).then(() => flash('Copied ✓')).catch(() => flash('Copy fail', '#f85149'));
    }

    async function whSaveStrat() {
      if (!WH_SEL) return;
      WH_STRATS[WH_SEL] = Object.assign({}, WH_STRATS[WH_SEL] || {}, _whReadForm());
      const ok = await _whPersist();
      const msg = document.getElementById('wh-save-msg');
      msg.innerText = ok ? 'Saved ✓' : 'Save failed'; msg.style.color = ok ? '#3fb950' : '#f85149';
      whRenderStratList(); whRenderTpl();
      setTimeout(() => msg.innerText = '', 3000);
    }

    async function whSaveGlobal() {
      const ok = await _whPersist();
      const msg = document.getElementById('wh-global-msg');
      msg.innerText = ok ? 'Saved ✓' : 'Save failed'; msg.style.color = ok ? '#3fb950' : '#f85149';
      setTimeout(() => msg.innerText = '', 3000);
    }

    async function whLtpPoll() {
      if (window.feedPaused) return;
      const symEl = document.getElementById('wh-ltp-sym'); if (!symEl) return;
      const sym = symEl.value || 'NIFTY';
      const off = (document.getElementById('wh-strike_offset') || {}).value || '0';
      whRenderTpl();
      try {
        const r = await fetch('/api/option-ltp?symbol=' + sym + '&offset=' + off);
        const d = await r.json();
        if (d.ok) {
          document.getElementById('wh-ltp-ce').innerText = d.ce_ltp != null ? (d.ce_ltp) : '—';
          document.getElementById('wh-ltp-pe').innerText = d.pe_ltp != null ? (d.pe_ltp) : '—';
          document.getElementById('wh-ltp-strike').innerText = (d.ce_sym || '') + '   /   ' + (d.pe_sym || '');
        } else {
          document.getElementById('wh-ltp-ce').innerText = '—'; document.getElementById('wh-ltp-pe').innerText = '—';
          const note = document.getElementById('wh-ltp-strike');
          note.innerText = 'LTP unavailable — Dhan token check karein';
          note.title = d.msg || '';
        }
      } catch (e) { }
    }

    async function whStatusPoll() {
      try {
        const r = await fetch('/api/webhook/status'); const d = await r.json();
        const pos = d.positions || {}; const keys = Object.keys(pos);
        let ph = keys.length ? '' : '<span style="color:#6e7681">koi open position nahi</span>';
        keys.forEach(k => {
          const p = pos[k]; const parts = k.split('|');
          ph += '<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #21262d">'
            + '<span style="color:#adbac7"><b>' + (parts[0] || '') + '</b> · ' + (parts[1] || '') + ' <span style="color:#8b949e">' + (p.opt_action || '') + '</span></span>'
            + '<span>' + (p.opt_qty || '') + ' @ ' + Number(p.entry_premium || 0).toFixed(2) + '</span></div>';
        });
        document.getElementById('wh-positions').innerHTML = ph;
        const ev = d.events || [];
        document.getElementById('wh-log').innerHTML = ev.length
          ? ev.slice().reverse().map(e => '<div><span style="color:#6e7681">' + (e.t || '').slice(11) + '</span> ' + (e.msg || '').replace(/</g, '&lt;') + '</div>').join('')
          : '<span style="color:#6e7681">—</span>';
        const g = d.global || {}; const dr = g.day_realized || 0; const cap = g.daily_amount_cap || 0;
        const drColor = dr > 0 ? '#3fb950' : (dr < 0 ? '#f85149' : '#8b949e');
        document.getElementById('wh-daypnl').innerHTML =
          'Day realized <b style="color:' + drColor + '">' + Math.round(dr) + '</b>'
          + (cap ? (' &nbsp;·&nbsp; Loss cap <b style="color:#adbac7">' + cap + '</b>') : '');
        const nstrat = Object.keys(d.strategies || {}).length;
        document.getElementById('wh-status-txt').innerText = nstrat + ' strateg' + (nstrat === 1 ? 'y' : 'ies') + ' configured';
        document.getElementById('wh-dot').style.background = nstrat ? '#3fb950' : '#6e7681';
      } catch (e) { }
    }

    // ── ORDERS TAB (DB-backed, cross-day, tagged) ──
    // Mode filter (All/Paper/Live) defaults to LIVE and is persisted — so paper
    // data-collection strategies (e.g. ARS_CHAIN_V1_PAPER scanning 23 equities)
    // never clutter the main live view as "stray orders". Strategy-agnostic: it
    // filters on the `mode` column, so ANY future paper strategy is hidden too.
    // RMS / peak-P&L / squareoff query order_store directly with their own mode
    // logic, so this is purely a display default — paper is one click away.
    function _ordApplyMode(val) {
      document.querySelectorAll('#ord-mode span').forEach(s => {
        const on = (s.dataset.v || '') === val;
        s.classList.toggle('on', on);
        s.style.color = on ? '#fff' : '#8b949e';
      });
    }
    function _ordRestoreMode() {
      let m = localStorage.getItem('ord_mode_filter');
      if (m === null) m = 'live';          // first-ever load → Live-only, not All
      _ordApplyMode(m === 'all' ? '' : m); // 'all' sentinel → the All span (data-v="")
    }
    function ordersEnter() {
      const di = document.getElementById('ord-date');
      if (di && !di.value) { di.value = new Date().toISOString().slice(0, 10); }
      _ordRestoreMode();
      ordersRender();
    }
    function ordSeg(segId, el) {
      document.querySelectorAll('#' + segId + ' span').forEach(s => { s.classList.remove('on'); s.style.color = '#8b949e'; });
      el.classList.add('on'); el.style.color = '#fff';
      // Persist the Mode choice so it survives reloads ('all' sentinel for the
      // empty-value All span, else getItem's '' would fall back to 'live').
      if (segId === 'ord-mode') localStorage.setItem('ord_mode_filter', el.dataset.v || 'all');
      ordersRender();
    }
    function _ordSegVal(segId) {
      const on = document.querySelector('#' + segId + ' span.on'); return on ? on.dataset.v : '';
    }
    function _ordTag(text, kind) {
      const C = {
        'webhook': ['#1f6feb22', '#79c0ff'], 'manual': ['#23863622', '#3fb950'],
        'strategy': ['#8957e522', '#bc8cff'], 'paper': ['#9e6a0322', '#e3a008'],
        'live': ['#f8514922', '#f85149'], 'hedge': ['#2ea04322', '#3fb950'], 'name': ['#21262d', '#8b949e']
      };
      const c = C[kind] || C['name'];
      if (!text) return '';
      return '<span style="background:' + c[0] + ';color:' + c[1] + ';padding:2px 6px;border-radius:4px;font-size:10px;margin-right:3px">' + text + '</span>';
    }
    function _ordPill(label, val, color) {
      return '<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 14px;min-width:78px">'
        + '<div style="font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.3px">' + label + '</div>'
        + '<div style="font-size:18px;font-weight:600;color:' + (color || '#e6edf3') + ';margin-top:2px">' + val + '</div></div>';
    }
    function _ordFillSelect(id, vals, cur, allLabel) {
      const sel = document.getElementById(id); if (!sel) return;
      // Filter out junk strategy names from strategy dropdowns
      const isStratSel = (id === 'ord-strat' || id === 'cal-strat');
      let filtered = isStratSel ? vals.filter(v => v && v.toLowerCase() !== 'unknown') : vals;
      const _base = v => (isStratSel && v.includes(' | ')) ? v.split(' | ')[0] : v;
      // registry ID se sort (00.01, 00.02… jaise /registry); unresolved last
      if (isStratSel && typeof regId === 'function') {
        const _sk = v => { const b = _base(v); const i = String(regId(b)); return (i !== b ? '0_' + i : '1_' + b); };
        filtered = filtered.slice().sort((a, b) => _sk(a).localeCompare(_sk(b)));
      }
      const want = JSON.stringify([''].concat(filtered));
      const have = JSON.stringify(Array.from(sel.options).map(o => o.value));
      if (have === want) return;
      sel.innerHTML = '<option value="">' + allLabel + '</option>' + filtered.map(v => {
        let display = _base(v);
        if (isStratSel && typeof regFull === 'function') display = regFull(display);     // "00.01 · Naam"
        else if (isStratSel && typeof regLabel === 'function') display = regLabel(display);
        return '<option value="' + v + '">' + display + '</option>';
      }).join('');
      sel.value = cur || '';
    }
    function _ordTags(t, skipStrat) {
      let isHedge = (t.group_id && t.group_id.startsWith('RANGE_') && t.entry === 'BUY') ||
        (t.correlation_id && t.correlation_id.startsWith('RANGE_') && t.entry === 'BUY') ||
        (t.correlation_id && t.correlation_id.startsWith('HEDGE'));

      const _sid = t.strategy ? t.strategy.split(' | ')[0] : '';
      // Strategy chip — registry code + NAME (04.04 · Ars chain - DirectWebhook),
      // raw config_key on hover. Was the other way round, which is why raw keys
      // like arschain_MAIN / ARS_CHAIN_V1 / range_v1 kept being what you actually
      // read — the registry exists so those never have to be recognised on sight.
      const _sidTxt = regId(_sid) !== _sid ? (regId(_sid) + ' · ' + regLabel(_sid)) : (regLabel(_sid) || _sid);
      const _sidChip = _sid
        ? '<span title="' + _sid.replace(/"/g, '&quot;') + '" style="background:#8957e522;color:#bc8cff;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:600;margin-right:3px">' + _sidTxt + '</span>'
        : '';
      // skipStrat: Completed/Open tables now show the strategy in its OWN column
      // (_stratCell) so the Tags chip no longer over-spills. Other surfaces (Stats
      // Point-Per-Trade) keep the strategy chip inside Tags — pass no flag.
      let h = _ordTag(t.source, t.source) + _ordTag(t.mode, t.mode) + (skipStrat ? '' : _sidChip);

      if (isHedge) {
        h += _ordTag('hedge', 'hedge');
      }

      h += _ordTag(t.broker, 'name');
      return h;
    }
    // Strategy as its OWN column — registry name + a STABLE per-strategy colour (hue
    // derived from the registry serial, so every variant of one strategy shares a
    // colour and different strategies are visually distinct). Raw config_key on hover.
    // Colour by the strategy's POSITION in the sorted registry (consecutive index
    // -> golden-angle 137.5deg = sunflower spacing) so sibling variants like
    // 04.01/02/03/04 get maximally-distinct hues instead of near-identical greens.
    // (The old incremental `%360` hash only moved the last serial digit ~1deg, so
    // every "04.xx" looked the same green.) Rebuilds when the registry loads/changes.
    let _stratHueMap = null, _stratHueN = -1;
    function _stratHue(id) {
      const strs = ((window.regRaw && window.regRaw()) || {}).strategies || {};
      const n = Object.keys(strs).length;
      if (!_stratHueMap || n !== _stratHueN) {
        const ids = Object.keys(strs).sort();
        _stratHueMap = {};
        for (let i = 0; i < ids.length; i++) _stratHueMap[ids[i]] = Math.round(i * 137.508) % 360;
        _stratHueN = n;
      }
      if (_stratHueMap[id] != null) return _stratHueMap[id];
      let s = String(id || ''), h = 0;                    // unregistered fallback (still golden-spread)
      for (let i = 0; i < s.length; i++) h = (h * 131 + s.charCodeAt(i)) >>> 0;
      return Math.round(h * 137.508) % 360;
    }
    function _stratCell(t) {
      const raw = t.strategy ? t.strategy.split(' | ')[0] : '';
      if (!raw) return '<span style="color:#6e7681">—</span>';
      const label = regId(raw) !== raw ? (regId(raw) + ' · ' + regLabel(raw)) : (regLabel(raw) || raw);
      const key = regId(raw) !== raw ? regId(raw) : raw;   // colour by registry serial (stable across variants)
      const hue = _stratHue(key);
      const bg = `hsl(${hue},55%,20%)`, fg = `hsl(${hue},80%,72%)`, bd = `hsl(${hue},45%,38%)`;
      const arg = raw.replace(/\\/g, '').replace(/'/g, '');   // safe for the inline onclick
      return `<span onclick="_ordStratChipClick(event,'${arg}')" title="Ctrl+click: is strategy pe filter (dobara Ctrl+click = All)&#10;${raw.replace(/"/g, '&quot;')}" style="cursor:pointer;display:inline-block;background:${bg};color:${fg};border:1px solid ${bd};padding:2px 7px;border-radius:5px;font-size:10.5px;font-weight:600;white-space:nowrap">${label}</span>`;
    }
    // Ctrl/Cmd+click a strategy chip → set the Orders strategy filter to that
    // strategy (match the #ord-strat option by its base, robust to " | desc"
    // pollution); Ctrl+click the SAME one again → back to All. Plain click = no-op.
    window._ordStratChipClick = function (ev, base) {
      if (!(ev.ctrlKey || ev.metaKey)) return;
      ev.preventDefault(); ev.stopPropagation();
      const sel = document.getElementById('ord-strat'); if (!sel) return;
      const opt = Array.from(sel.options).find(o => o.value &&
        (o.value === base || o.value.split(' | ')[0] === base));
      const val = opt ? opt.value : '';
      sel.value = (val && sel.value === val) ? '' : val;   // toggle off if already active
      if (typeof ordersRender === 'function') ordersRender();
    };
    // Ctrl/Cmd+click an exit-reason badge → filter Completed Trades to that reason
    // (client-side, by prefix base — there is no server-side exit_reason filter);
    // Ctrl+click the same one again → clear. Plain click = no-op.
    window._ordExitChipClick = function (ev, base) {
      if (!(ev.ctrlKey || ev.metaKey)) return;
      ev.preventDefault(); ev.stopPropagation();
      window._ordExitFilter = (window._ordExitFilter === base) ? '' : base;
      if (typeof renderCachedOrders === 'function') renderCachedOrders();
    };
    window._ordExitClear = function () {
      window._ordExitFilter = '';
      if (typeof renderCachedOrders === 'function') renderCachedOrders();
    };
    // Ctrl/Cmd+click an instrument chip → filter Completed Trades + Open Positions to that
    // underlying (client-side, like the exit-reason filter). Same one again → clear.
    window._ordInstrChipClick = function (ev, instr) {
      if (!(ev.ctrlKey || ev.metaKey)) return;
      ev.preventDefault(); ev.stopPropagation();
      window._ordInstrFilter = (window._ordInstrFilter === instr) ? '' : instr;
      if (typeof renderCachedOrders === 'function') renderCachedOrders();
    };
    window._ordInstrClear = function () {
      window._ordInstrFilter = '';
      if (typeof renderCachedOrders === 'function') renderCachedOrders();
    };
    // Clear the #ord-strat (top) strategy filter — the one a strategy-chip Ctrl+click sets.
    // Gives that filter the same ✕-pill clear affordance as instrument/exit, so it can be
    // removed right from the Completed-trades header (not only via the top dropdown).
    window._ordStratFilterClear = function () {
      const s = document.getElementById('ord-strat');
      if (s) s.value = '';
      if (typeof ordersRender === 'function') ordersRender();
    };
    // Collapse-all / Expand-all the Completed-trades groups (next to the Group dropdown).
    // If every current group is already expanded → collapse all; otherwise expand all.
    window.toggleAllGroups = function () {
      const keys = window._completedGroupKeys || [];
      if (!keys.length) return;
      const set = window._completedGroupExpanded || (window._completedGroupExpanded = new Set());
      const allOpen = keys.every(k => set.has(k));
      if (allOpen) set.clear();
      else keys.forEach(k => set.add(k));
      if (typeof renderCachedOrders === 'function') renderCachedOrders();
    };
    function _imgTagsOf(t) { return (t.tags || []).filter(tg => tg.startsWith('IMG:')).map(tg => tg.slice(4)); }
    function _noteThumbs(id, imgs) {
      if (!imgs || !imgs.length) return '';
      const urls = imgs.map(fn => `/api/orders/note-image/${id}/${fn}`);
      return '<div style="display:flex;gap:4px;margin-top:4px">' + imgs.map((fn, index) => {
        const escapedUrls = JSON.stringify(urls).replace(/"/g, '&quot;');
        return `<a href="javascript:void(0)" onclick="openImageViewer(${escapedUrls}, ${index})"><img src="/api/orders/note-image/${id}/${fn}" style="width:28px;height:28px;object-fit:cover;border-radius:4px;border:1px solid #30363d"></a>`;
      }).join('') + '</div>';
    }
    // "HH:MM" entry→exit ka duration ("23m" / "1h 12m"). Cross-din nahi (intraday).
    function _durMin(a, b) {
      if (!a || !b || String(a).indexOf(':') < 0 || String(b).indexOf(':') < 0) return null;
      const [ah, am] = a.split(':').map(Number), [bh, bm] = b.split(':').map(Number);
      return (bh * 60 + bm) - (ah * 60 + am);
    }
    function _durFmt(a, b) {
      const d = _durMin(a, b);
      if (d == null) return '—';
      let m = Math.abs(d); const h = Math.floor(m / 60); m = m % 60;
      return (d < 0 ? '-' : '') + (h ? (h + 'h ' + m + 'm') : (m + 'm'));
    }
    async function ordersRender() {
      const date = (document.getElementById('ord-date') || {}).value;
      if (!date) return;
      const q = new URLSearchParams({ date });
      const mode = _ordSegVal('ord-mode'); if (mode) q.set('mode', mode);
      const strat = document.getElementById('ord-strat').value; if (strat) q.set('strategy', strat);
      const broker = document.getElementById('ord-broker').value; if (broker) q.set('broker', broker);
      let d = {};
      try { const r = await fetch('/api/orders?' + q.toString()); d = await r.json(); } catch (e) { return; }
      // per-strategy REAL hedged basket margin (backend: risk_gate._group_capital)
      // — the Open Positions group TOTAL shows this instead of summing each
      // leg's standalone margin, which overstates a hedged structure badly.
      window._ordGroupMargin = d.group_margin || {};

      let det = d.details || [];
      // (Source filter was removed from the Orders toolbar in ef9f3cc; the old
      //  `src === 'hedge'` special-case filter went with it. Its orphaned reference
      //  to the now-undeclared `src` threw "src is not defined" on every render,
      //  freezing the Orders & P&L tab — removed here to complete that refactor.)

      _ordFillSelect('ord-strat', (d.filters || {}).strategy || [], strat, 'All strategies');
      _ordFillSelect('ord-broker', (d.filters || {}).broker || [], broker, 'All brokers');

      // ── per-trade gross / tax / net (Zerodha charges) ──
      det.forEach(t => {
        const ep = t.entry_price || 0, xp = t.exit_price || 0, qt = t.qty || 0;
        t._gross = ep && xp && qt ? (t.entry === 'BUY' ? xp - ep : ep - xp) * qt : (t.pnl || 0);
        t._tax = ep && xp && qt ? (calcCharges(ep, xp, qt, t.entry) || 0) : 0;
        t._net = t._gross - t._tax;
      });

      window._lastOrdersData = d;
      renderCachedOrders();
      renderOrderTriggers();
    }

    // ── 🎯 Price Triggers panel (Orders tab) ─────────────────────────────────
    // Armed price-triggers (Quick Order → Trigger tab) are otherwise only visible
    // inside the floating panel — easy to forget what you armed. Surface them here
    // next to positions: waiting ones show level+distance, fired ones show their
    // result (e.g. a paper fill hidden by the Live-only Open Positions filter, so
    // "trigger fired but no position" is explained right where you'd look).
    async function renderOrderTriggers() {
      const card = document.getElementById('ord-triggers-card');
      const box = document.getElementById('ord-triggers');
      if (!card || !box) return;
      let j;
      try { j = await (await fetch('/api/triggers')).json(); } catch (e) { return; }
      const rows = (j && j.triggers) || [];
      if (!rows.length) { card.style.display = 'none'; return; }
      card.style.display = '';
      const nArmed = rows.filter(t => t.status === 'armed').length;
      const cnt = document.getElementById('ord-trig-count');
      if (cnt) cnt.textContent = '— ' + nArmed + ' armed'
        + (rows.length > nArmed ? ', ' + (rows.length - nArmed) + ' fired/done today' : '');
      const esc = s => String(s == null ? '' : s).replace(/"/g, '&quot;');
      box.innerHTML = rows.map(t => {
        const armed = t.status === 'armed';
        const above = t.direction === 'above';
        const dirTxt = above ? '↑ upar cross' : '↓ neeche cross';
        const col = armed ? (above ? '#3fb950' : '#f85149') : (t.status === 'fired' ? '#58a6ff' : '#d29922');
        const offTxt = t.offset ? (t.offset > 0 ? '+' + t.offset : '' + t.offset) : '';
        const modeBadge = t.mode === 'live'
          ? '<span style="color:#f85149;font-weight:600">LIVE</span>'
          : '<span style="color:#58a6ff;font-weight:600">PAPER</span>';
        const statusBadge = armed
          ? '<span style="color:#3fb950">🟢 waiting</span>'
          : '<span style="color:' + col + '">' + esc(t.status) + '</span>';
        const dist = (t.dist != null && armed)
          ? '<span style="color:#d29922">' + Math.abs(t.dist).toFixed(0) + ' pts door</span>' : '';
        const res = (t.result && !armed)
          ? '<div style="font-size:10px;color:#8b949e;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:460px" title="' + esc(t.result) + '">' + esc(t.result) + '</div>' : '';
        return '<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:7px 9px;border:1px solid #21262d;border-radius:6px;margin-bottom:5px;background:#0d1117">'
          + '<div style="font-size:12px;color:#e6edf3;min-width:0">'
          + '<b style="color:' + col + '">' + esc(t.symbol) + ' ' + Number(t.level).toLocaleString('en-IN') + '</b> ' + dirTxt
          + ' → ' + esc(t.side) + ' ' + esc(t.lots) + 'L ATM' + offTxt + ' ' + esc(t.opt_type) + ' ' + modeBadge
          + res + '</div>'
          + '<div style="display:flex;align-items:center;gap:9px;font-size:11px;white-space:nowrap">'
          + dist + ' ' + statusBadge
          + (armed ? ' <span onclick="ordCancelTrigger(\'' + t.id + '\')" title="Cancel trigger" style="color:#8b949e;cursor:pointer;font-size:14px">✕</span>' : '')
          + '</div></div>';
      }).join('');
    }
    window.ordCancelTrigger = async tid => {
      try { await fetch('/api/triggers/' + encodeURIComponent(tid), { method: 'DELETE' }); } catch (e) { }
      renderOrderTriggers();
    };
    // Keep the panel live while the Orders tab is open (independent of a full
    // orders refresh, so a trigger firing shows up within a few seconds).
    setInterval(() => {
      const tab = document.getElementById('tab-orders');
      if (tab && tab.classList.contains('active')) renderOrderTriggers();
    }, 3000);

    // Global state for sorting & hidden notes
    window._completedSortCol = localStorage.getItem('ord_completed_sort_col') || 'entry_time';
    window._completedSortDir = localStorage.getItem('ord_completed_sort_dir') || 'desc';
    window._openSortCol = localStorage.getItem('ord_open_sort_col') || 'entry_time';
    window._openSortDir = localStorage.getItem('ord_open_sort_dir') || 'desc';
    window._hiddenNotes = new Set(JSON.parse(localStorage.getItem('ord_hidden_notes') || '[]'));

    // Completed-trades "Group by Symbol" — Zerodha Day's History style: one
    // collapsed summary row per symbol (totals), expand to see individual trades.
    // Group mode: none | symbol | strategy | pnl | hedge | exit | instrument. Migrates the
    // old boolean (`ord_completed_group_by`) → 'symbol' so existing users keep their grouping.
    window._completedGroupMode = localStorage.getItem('ord_completed_group_mode')
      || (localStorage.getItem('ord_completed_group_by') === 'true' ? 'symbol' : 'none');
    window._completedGroupBy = (window._completedGroupMode !== 'none');   // back-compat alias
    window._completedGroupExpanded = new Set();   // group keys currently expanded — not persisted, resets per page load (matches Zerodha's own transient expand state)

    function toggleCompletedGrouping() {
      window._completedGroupBy = !window._completedGroupBy;
      localStorage.setItem('ord_completed_group_by', window._completedGroupBy ? 'true' : 'false');
      const btn = document.getElementById('ord-group-btn');
      if (btn) {
        btn.style.background = window._completedGroupBy ? '#1f6feb' : '';
        btn.style.borderColor = window._completedGroupBy ? '#1f6feb' : '#30363d';
      }
      renderCachedOrders();
    }

    function toggleCompletedGroupExpand(symKey) {
      if (window._completedGroupExpanded.has(symKey)) window._completedGroupExpanded.delete(symKey);
      else window._completedGroupExpanded.add(symKey);
      renderCachedOrders();
    }

    // Run-Up / Run-Down cell: AMT (₹) | PT (points) | % — one shared formatter
    // so Completed Trades and Open Positions render identically.
    function _ruCell(pt, amt, pct) {
      if (!isFinite(pt) || !isFinite(amt) || !isFinite(pct)) return '—';
      const c = amt > 0 ? '#3fb950' : (amt < 0 ? '#f85149' : '#8b949e');
      const sign = amt > 0 ? '+' : '';
      return `<span style="color:${c};font-weight:600">${sign}${Math.round(amt).toLocaleString('en-IN')}</span>`
        + `<span style="color:#8b949e;font-size:10px"> | ${sign}${pt.toFixed(1)} | ${sign}${pct.toFixed(1)}%</span>`;
    }

    // ── Instrument (underlying) — the root of a trad_sym: NIFTY / BANKNIFTY / stock ──
    // Distinct colours: NIFTY blue, BANKNIFTY amber, every stock (& minor index) one purple.
    const _INSTR_CLR = {
      NIFTY:     { bg: '#1f6feb20', bd: '#1f6feb60', fg: '#58a6ff' },
      BANKNIFTY: { bg: '#d2992222', bd: '#d2992255', fg: '#e3b341' },
    };
    const _INSTR_STOCK = { bg: '#8957e520', bd: '#8957e555', fg: '#b083f0' };
    function _instrOf(t) {
      const s = String((t && t.sym) || '');
      const root = (s.split('-')[0] || '').trim().toUpperCase();
      return root || '—';
    }
    function _instrCell(t) {
      const r = _instrOf(t);
      if (r === '—') return '<span style="color:#6e7681">—</span>';
      const c = _INSTR_CLR[r] || _INSTR_STOCK;
      const ring = (window._ordInstrFilter === r) ? ';box-shadow:0 0 0 1px ' + c.fg : '';
      return `<span onclick="_ordInstrChipClick(event,'${r.replace(/'/g, '')}')" title="Ctrl+click: is instrument pe filter (dobara Ctrl+click = All)" style="cursor:pointer;display:inline-block;padding:1px 7px;border-radius:10px;background:${c.bg};border:1px solid ${c.bd};color:${c.fg};font-size:10px;font-weight:600;white-space:nowrap${ring}">${r}</span>`;
    }

    // ── Completed-trades grouping key/label per mode (none/symbol/strategy/pnl/hedge/exit) ──
    function _exitFamily(reason) {
      const r = String(reason || '').trim();
      if (!r || r === '-' || r === '—') return { key: 'z_none', label: '— no exit reason' };
      const lo = r.toLowerCase();
      if (lo.includes('manual_clear') || lo.includes('straddle_clear')) return { key: 'manual_clear', label: '🧹 Manual Clear' };
      if (lo.includes('manual')) return { key: 'manual', label: '✋ Manual Close' };
      if (lo.includes('daily target') || lo.includes('profit_target') || lo.includes('profit target')) return { key: 'rms_target', label: '🎯 RMS Daily Target' };
      if (lo.includes('rms') && lo.includes('loss')) return { key: 'rms_loss', label: '🛑 RMS Max Loss' };
      if (lo.includes('abort')) return { key: 'abort', label: '⚠️ Abort (naked)' };
      if (lo.includes('eod') || lo.includes('squareoff') || lo.includes('3:15') || lo.includes('315')) return { key: 'eod', label: '⏰ EOD Squareoff' };
      if (lo.includes('atr') || lo.includes('trail')) return { key: 'trail', label: '📉 Trailing / ATR' };
      if (lo.includes('straddle_sl') || lo.includes('_sl') || lo.includes('stop')) return { key: 'sl', label: '🛑 Stop Loss' };
      if (lo.includes('straddle_target') || lo.includes('_tp') || lo.includes('target')) return { key: 'target', label: '🎯 Target' };
      const short = r.split(/[·:|]/)[0].trim().slice(0, 24);
      return { key: 'x_' + short.toLowerCase(), label: short || 'Other' };
    }
    function _grpKeyLabel(t, mode) {
      switch (mode) {
        case 'strategy': { const k = t.strategy || '—'; return { key: 'S:' + k, label: (window.regFull ? regFull(k) : k) }; }
        case 'pnl':      { const w = (t._net || 0) >= 0; return { key: w ? 'p' : 'l', label: w ? '✅ Profit' : '❌ Loss' }; }
        case 'hedge':    { const h = (t.tags || []).includes('HEDGE') || /hedge/i.test(String(t.exit_reason || '')); return { key: h ? 'h' : 'm', label: h ? '🛡️ Hedge legs' : '🎯 Main legs' }; }
        case 'exit':     { const f = _exitFamily(t.exit_reason); return { key: 'E:' + f.key, label: f.label }; }
        case 'instrument': { const r = _instrOf(t); return { key: 'I:' + r, label: r }; }
        case 'symbol':
        default:         return { key: t.sym || '—', label: t.sym || '—' };
      }
    }
    function setCompletedGroupMode(mode) {
      window._completedGroupMode = mode || 'none';
      window._completedGroupBy = (window._completedGroupMode !== 'none');   // back-compat alias
      localStorage.setItem('ord_completed_group_mode', window._completedGroupMode);
      window._completedGroupExpanded = new Set();   // reset expand state on mode change
      renderCachedOrders();
    }

    // ── Payoff / Zone panel (DISPLAY-ONLY) ───────────────────────────────────
    // Per open-position GROUP: payoff at expiry + today (Black-Scholes),
    // probability-of-profit, REAL hedged basket margin (vs the standalone sum
    // the Margin column shows), the combined net-structure premium series, and
    // an n-up grid of each leg's own premium chart. Backed by /api/position-*
    // → _core/payoff.py. Nothing here places or gates an order.
    function _payoffLegIds(items) {
      return (items || []).filter(t => {
        const p = String(t.sym || '').split('-');
        const okSym = p.length >= 3 && ['CE', 'PE'].includes((p[p.length - 1] || '').toUpperCase())
          && isFinite(parseFloat(p[p.length - 2]));
        const blocked = (t.tags || []).includes('CAPITAL_BLOCKED') || t.status === 'blocked';
        return okSym && !blocked;
      }).map(t => t.id);
    }
    function _payoffBtn(items, stratName) {
      const ids = _payoffLegIds(items);
      if (ids.length < 2) return '';                                   // single leg → no structure
      if (new Set(items.map(t => String(t.sym || '').split('-')[0])).size !== 1) return '';  // mixed underlyings
      const nm = String(stratName || '').replace(/'/g, '');
      return `<button onclick="openPayoffPanel('${ids.join(',')}','${nm}', event)"
        title="Payoff, safe zone, POP, hedged margin"
        style="margin-right:10px;padding:3px 8px;font-size:11px;background:#1f6feb20;border:1px solid #1f6feb80;border-radius:4px;color:#58a6ff;cursor:pointer;font-weight:600">📊 Payoff</button>`;
    }

    const _PF = { C: { pos: '#3fb950', neg: '#f85149', amb: '#d29922', blu: '#58a6ff', mut: '#8b949e', txt: '#e6edf3', grid: '#21262d' } };
    function _pfRs(v) { const s = v < 0 ? '-' : ''; return s + '₹' + Math.round(Math.abs(v)).toLocaleString('en-IN'); }
    function _pfNf(v) { return Math.round(v).toLocaleString('en-IN'); }
    function _pfEl(p, n, a, txt) {
      const e = document.createElementNS('http://www.w3.org/2000/svg', n);
      for (const k in a) e.setAttribute(k, a[k]);
      if (txt != null) e.textContent = txt;
      p.appendChild(e); return e;
    }
    function _pfIST(ts) {
      const d = new Date((ts + 19800) * 1000), p = n => String(n).padStart(2, '0');
      return p(d.getUTCDate()) + '/' + p(d.getUTCMonth() + 1) + ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
    }

    // Open the panel for an OPEN group by its leg ids (called from the open-positions button).
    function openPayoffPanel(idsCsv, name, ev) {
      openPayoffPanelQS('ids=' + idsCsv, name, ev);
    }
    // Open the panel for ANY group — open (ids=) or closed (group_id=) — with a group selector (#01).
    function openPayoffPanelQS(qs, name, ev) {
      if (ev) { ev.preventDefault(); ev.stopPropagation(); }
      window._pfView = null; window._pfMargin = null; window._pfData = null;   // fresh per group
      window._pfInitName = name || '';
      let ov = document.getElementById('pfOverlay');
      if (!ov) {
        ov = document.createElement('div');
        ov.id = 'pfOverlay';
        ov.style.cssText = 'position:fixed;inset:0;background:#000000cc;z-index:9999;display:flex;align-items:flex-start;justify-content:center;padding:24px;overflow-y:auto';
        ov.onclick = e => { if (e.target === ov) closePayoffPanel(); };
        document.body.appendChild(ov);
      }
      ov.style.display = 'flex';
      ov.innerHTML = `<style>
        .pf-2col{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:12px;align-items:start}
        .pf-col{display:flex;flex-direction:column;gap:12px;min-width:0}
        .pf-panel{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 12px}
        #pfGrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:10px}
        .pf-navbtn{width:24px;height:28px;border-radius:6px;border:1px solid #30363d;background:#161b22;color:#e6edf3;cursor:pointer;font-size:16px;font-weight:700;line-height:1}
        .pf-navbtn:hover{background:#21262d;border-color:#1f6feb}
        #pfCards{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:14px}
        .pf-tab{flex:0 0 auto;padding:8px 15px;background:#0d1117;border:1px solid #30363d;border-radius:7px;color:#8b949e;font-size:12.5px;font-weight:700;cursor:pointer}
        .pf-tab:hover{color:#e6edf3;border-color:#1f6feb}
        .pf-tab-on{background:#1f6feb22;color:#58a6ff;border-color:#1f6feb80}
        @media(max-width:900px){.pf-2col{grid-template-columns:1fr}#pfCards{grid-template-columns:repeat(3,1fr)}}
      </style>
      <div style="background:#0d1117;border:1px solid #30363d;border-radius:10px;max-width:1280px;width:100%;padding:16px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:10px;flex-wrap:wrap">
          <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap">
            <span style="font-size:15px;font-weight:700;color:#e6edf3">📊 Payoff &amp; Zone</span>
            <button class="pf-navbtn" onclick="_pfNav(-1)" title="Previous group">‹</button>
            <select id="pfGrpSel" onchange="_pfSelectGroup(this.value)" title="open / recently-closed group"
              style="background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:5px 8px;font-size:12px;font-weight:600;cursor:pointer;max-width:320px"></select>
            <button class="pf-navbtn" onclick="_pfNav(1)" title="Next group">›</button>
            <span id="pfStatus" style="font-size:11px;font-weight:700;padding:3px 9px;border-radius:20px"></span>
          </div>
          <div style="display:flex;align-items:center;gap:14px">
            <span id="pfHdrPnl" style="font-family:ui-monospace,monospace;font-size:15px;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap"></span>
            <button onclick="closePayoffPanel()" style="padding:4px 10px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#8b949e;cursor:pointer">✕ Close</button>
          </div>
        </div>
        <div id="pfSub" style="font-size:11px;color:#8b949e;margin:-2px 0 8px">loading…</div>
        <div id="pfLegStrip" style="display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:12px;padding:9px 11px;background:#161b22;border:1px solid #30363d;border-radius:8px"></div>
        <div id="pfBody" style="color:#8b949e;font-size:12px;padding:20px;text-align:center">⏳ computing…</div>
      </div>`;
      _pfLoadGroups(qs);
      _pfLoad(qs);
    }
    function closePayoffPanel() { const o = document.getElementById('pfOverlay'); if (o) o.style.display = 'none'; }

    // Launcher for when NOTHING is open — opens the most recent (open or closed) group (#01).
    async function openPayoffLauncher(ev) {
      if (ev) { ev.preventDefault(); ev.stopPropagation(); }
      let g;
      try { const r = await fetch('/api/position-groups'); g = await r.json(); } catch (e) { g = null; }
      const groups = (g && g.groups) || [];
      if (!groups.length) { try { toast('Koi option group nahi mila (open ya recently-closed 7 din).'); } catch (e) {} return; }
      openPayoffPanelQS('group_id=' + groups[0].group_id, groups[0].label, ev);
    }

    async function _pfLoadGroups(currentQS) {
      let g;
      try { const r = await fetch('/api/position-groups'); g = await r.json(); } catch (e) { return; }
      const sel = document.getElementById('pfGrpSel'); if (!sel) return;
      const groups = (g && g.groups) || [];
      window._pfGroups = groups;
      const curIds = new Set((currentQS.indexOf('ids=') === 0 ? currentQS.slice(4) : '').split(',').filter(Boolean));
      let curVal = currentQS, matched = false;
      const opts = groups.map(gr => {
        const val = 'group_id=' + gr.group_id;
        if (currentQS === val || (gr.ids && gr.ids.some(i => curIds.has(String(i))))) { curVal = val; matched = true; }
        return `<option value="${val}">${gr.label}</option>`;
      });
      // opened via legacy ids that don't map to a group_id group → keep a raw entry
      if (!matched && curIds.size) opts.unshift(`<option value="${currentQS}">${(window._pfInitName || 'this position')} · open</option>`);
      sel.innerHTML = opts.join('') || `<option value="${currentQS}">this position</option>`;
      sel.value = curVal;
    }
    function _pfSelectGroup(val) { if (val) _pfLoad(val); }
    // ‹ Prev / Next › — cycle the group selector without opening it (wrap-around)
    function _pfNav(dir) {
      const sel = document.getElementById('pfGrpSel');
      if (!sel || !sel.options.length) return;
      const n = sel.options.length;
      sel.selectedIndex = (sel.selectedIndex + dir + n) % n;
      _pfSelectGroup(sel.value);
    }
    // Header P&L — the group's ACTUAL number: live Net MTM (open) / Realized (closed), ₹·pt·%
    function _pfSetHdrPnl(val, d) {
      const el = document.getElementById('pfHdrPnl'); if (!el) return;
      if (val == null || isNaN(val)) { el.textContent = ''; return; }
      const qty = (d && d.legs && d.legs[0] && d.legs[0].qty) ? d.legs[0].qty : 1;
      const pt = val / qty;
      const mgn = (window._pfMargin && window._pfMargin.ok && window._pfMargin.hedged) ? window._pfMargin.hedged : 0;
      const pct = mgn ? (val / mgn * 100) : null;
      const lab = window._pfClosed ? 'Realized P&L' : 'Net MTM';
      el.innerHTML = `<span style="font-size:9.5px;color:#8b949e;font-weight:600;text-transform:uppercase;letter-spacing:.03em;margin-right:5px">${lab}</span>`
        + (val >= 0 ? '+' : '') + _pfRs(val) + ' · ' + (pt >= 0 ? '+' : '') + pt.toFixed(1) + 'pt'
        + (pct != null ? ` · <span style="font-size:12px">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span>` : '');
      el.style.color = val >= 0 ? _PF.C.pos : _PF.C.neg;
    }

    function _pfRenderLegStrip(d) {
      const el = document.getElementById('pfLegStrip'); if (!el) return;
      if (!d || !d.legs || !d.legs.length) { el.innerHTML = '<span style="font-size:11px;color:#6e7681">—</span>'; return; }
      const flat = !!window._pfClosed;
      const chip = L => {
        const sell = String(L.side).toUpperCase() === 'SELL';
        return `<span style="font-family:ui-monospace,monospace;font-size:11.5px;font-weight:600;padding:3px 9px;border-radius:6px;`
          + `background:${sell ? '#f8514915' : '#3fb95015'};color:${sell ? '#ff8b82' : '#7ee787'};border:1px solid ${sell ? '#f8514940' : '#3fb95040'};${flat ? 'opacity:.55;text-decoration:line-through' : ''}">`
          + `<b style="font-size:9px;opacity:.85;margin-right:3px">${L.side}</b>${_pfNf(L.strike)} ${L.opt} @${(+L.entry).toFixed(0)}</span>`;
      };
      el.innerHTML = d.legs.map(chip).join('')
        + (d.expiry ? `<span style="font-size:10px;color:#6e7681;border-left:1px solid #30363d;padding-left:8px;margin-left:2px">exp ${d.expiry}</span>` : '')
        + (window._pfClosed ? `<span style="font-size:10px;color:#d29922;border-left:1px solid #30363d;padding-left:8px;margin-left:2px">reconstructed from entry</span>` : '');
    }

    async function _pfLoad(qs) {
      window._pfQS = qs;                       // ids=… or group_id=… — for margin/series/exit-rule
      const body = document.getElementById('pfBody');
      let d;
      try {
        const r = await fetch('/api/position-payoff?' + qs);
        d = await r.json();
      } catch (e) { body.innerHTML = '<span style="color:#f85149">payoff fetch fail: ' + e.message + '</span>'; _pfRenderLegStrip(null); return; }
      if (!d || !d.ok) { body.innerHTML = '<span style="color:#f85149">' + ((d && d.msg) || 'payoff nahi bana') + '</span>'; _pfRenderLegStrip(null); return; }

      window._pfClosed = !!d.closed;
      const st = document.getElementById('pfStatus');
      if (st) {
        st.style.cssText = 'font-size:11px;font-weight:700;padding:3px 9px;border-radius:20px;'
          + (d.closed ? 'background:#8b949e22;color:#adbac7;border:1px solid #8b949e55' : 'background:#1f6feb22;color:#58a6ff;border:1px solid #1f6feb55');
        st.textContent = d.closed ? '● CLOSED' : '● OPEN';
      }
      document.getElementById('pfSub').textContent =
        `${d.legs.length} legs · spot ${d.spot ? _pfNf(d.spot) : '—'}`
        + (d.expiry ? ` · expiry ${d.expiry} (${d.tte_days}d)` : '')
        + (d.avg_iv ? ` · IV ~${(d.avg_iv * 100).toFixed(1)}%` : '')
        + (d.closed ? ' · position closed — reconstructed from entry' : '');
      _pfRenderLegStrip(d);

      window._pfData = d;
      window._pfZoom = { z: 1, c: null };    // #00 fresh zoom per group
      window._pfExit = null;                  // #02 combined SL/target (set on first combined draw)
      _pfSetHdrPnl(d.pnl_now_today != null ? d.pnl_now_today : d.pnl_now_expiry, d);  // immediate; refined by combined
      // Expiry vs exit-day: for a one-night / intraday structure the expiry
      // numbers are theoretical — it closes at today's square-off. Default to
      // whichever the position actually is.
      if (window._pfView == null) window._pfView = d.pop_target != null ? 'target' : 'expiry';
      if (d.pop_target == null) window._pfView = 'expiry';

      body.innerHTML = `
        <!-- KEY STATS row (top) -->
        <div style="display:flex;justify-content:space-between;align-items:center;margin:0 0 7px;gap:8px;flex-wrap:wrap">
          <span style="font-size:11px;font-weight:700;color:#8b949e;text-transform:uppercase;letter-spacing:.05em">Key stats</span>
          <span id="pfViewTog"></span>
        </div>
        <div id="pfCards"></div>

        <!-- TABS -->
        <div style="display:flex;gap:7px;margin-bottom:10px;flex-wrap:wrap">
          <button class="pf-tab pf-tab-on" id="pfTabBtn0" onclick="_pfTab(0)">🎯 SL / Target</button>
          <button class="pf-tab" id="pfTabBtn1" onclick="_pfTab(1)">📈 Payoff diagram</button>
          <button class="pf-tab" id="pfTabBtn2" onclick="_pfTab(2)">📊 Charts (${d.legs.length} legs)</button>
        </div>

        <!-- TAB 0 · SL / Target (combined premium) -->
        <div id="pfTabBody0"><div id="pfComboSlot"></div></div>

        <!-- TAB 1 · Payoff diagram -->
        <div id="pfTabBody1" style="display:none">
          <div class="pf-panel">
            <div style="display:flex;justify-content:space-between;align-items:center;margin:0 0 4px;flex-wrap:wrap;gap:6px">
              <span style="font-size:11px;font-weight:600;color:#8b949e">
                Payoff — <span style="color:#3fb950;font-weight:700">━ On Expiry</span>
                ${d.curve_today ? '<span style="color:#58a6ff;font-weight:700;margin-left:8px">━ Today</span>' : ''}
                ${d.curve_target ? '<span style="color:#d29922;font-weight:700;margin-left:8px">╌ Exit day</span>' : ''}
                <span style="color:#6e7681;font-weight:400;margin-left:8px">· <b style="color:#8b949e">Alt</b>+scroll = zoom · drag = pan</span>
              </span>
              <span style="display:flex;align-items:center;gap:5px">
                <button onclick="_pfZoomBtn('in')" title="zoom in" style="width:26px;height:24px;background:#161b22;border:1px solid #30363d;border-radius:5px;color:#e6edf3;cursor:pointer;font-weight:700">＋</button>
                <button onclick="_pfZoomBtn('out')" title="zoom out" style="width:26px;height:24px;background:#161b22;border:1px solid #30363d;border-radius:5px;color:#e6edf3;cursor:pointer;font-weight:700">−</button>
                <button onclick="_pfZoomBtn('fit')" title="fit" style="height:24px;padding:0 8px;background:#161b22;border:1px solid #30363d;border-radius:5px;color:#8b949e;cursor:pointer;font-size:11px;font-weight:600">⟲ Fit</button>
              </span>
            </div>
            <div style="overflow-x:auto"><svg id="pfChart" viewBox="0 0 940 300" style="width:100%;height:auto"></svg></div>
            <div id="pfHover" style="text-align:center;font-size:11px;color:#8b949e;min-height:15px;margin-top:4px"></div>
          </div>
        </div>

        <!-- TAB 2 · Per-leg charts -->
        <div id="pfTabBody2" style="display:none"><div id="pfLegSlot"></div></div>`;
      window._pfTabIdx = 0;
      _pfRenderCards();
      _pfDrawPayoff(d);
      _pfLoadMargin(qs);
      _pfLoadSeries(qs);
    }

    // 3-tab switch — SL/Target(0) · Payoff(1) · Charts(2). Redraw the shown tab so
    // its SVG sizes correctly after the display change (harmless if already sized).
    function _pfTab(i) {
      window._pfTabIdx = i;
      for (let j = 0; j < 3; j++) {
        const b = document.getElementById('pfTabBody' + j); if (b) b.style.display = j === i ? '' : 'none';
        const t = document.getElementById('pfTabBtn' + j); if (t) t.classList.toggle('pf-tab-on', j === i);
      }
      try {
        if (i === 0 && window._pfSeries) _pfDrawCombined(window._pfSeries);
        else if (i === 1 && window._pfData) _pfDrawPayoff(window._pfData);
        else if (i === 2 && window._pfSeries) _pfDrawGrid(window._pfSeries);
      } catch (e) {}
    }

    function _pfSetView(v) { window._pfView = v; _pfRenderCards(); _pfDrawPayoff(window._pfData); }

    function _pfRenderCards() {
      const d = window._pfData; if (!d) return;
      const tgt = window._pfView === 'target' && d.pop_target != null;
      const pop = tgt ? d.pop_target : d.pop;
      const be = ((tgt ? d.breakevens_target : d.breakevens) || [])[0];
      const mp = tgt ? d.max_profit_target : d.max_profit;
      const ml = tgt ? d.max_loss_target : d.max_loss;
      const hrs = d.target_days != null ? (d.target_days * 24) : null;
      const when = tgt
        ? `exit pe (${hrs != null ? (hrs < 1 ? Math.round(hrs * 60) + ' min' : hrs.toFixed(1) + ' hr') : 'aaj'} baad)`
        : `expiry pe (${d.tte_days}d baad)`;

      // toggle — only when the position genuinely closes before expiry
      const tog = document.getElementById('pfViewTog');
      if (tog) {
        tog.innerHTML = d.pop_target == null ? '' :
          ['target', 'expiry'].map(v => {
            const on = (window._pfView === v);
            return `<button onclick="_pfSetView('${v}')" style="padding:3px 9px;font-size:10px;font-weight:700;cursor:pointer;
              background:${on ? '#1f6feb30' : '#161b22'};border:1px solid ${on ? '#1f6feb80' : '#30363d'};
              color:${on ? '#58a6ff' : '#8b949e'};border-radius:5px;margin-left:4px">
              ${v === 'target' ? 'Exit day' : 'Expiry'}</button>`;
          }).join('');
      }

      const cards = [
        ['Probability of Profit', pop != null ? (pop * 100).toFixed(1) + '%' : '—',
          pop != null && pop >= .5 ? _PF.C.pos : _PF.C.amb, when],
        ['Max Profit', mp != null ? '+' + _pfRs(mp) : '—', _PF.C.pos, tgt ? 'exit-day best' : 'capped'],
        ['Breakeven', be != null ? _pfNf(be) : '—', _PF.C.amb, 'safe line'],
        ['Max Loss', ml != null ? _pfRs(ml) : '—', _PF.C.neg, tgt ? 'exit-day worst' : 'capped (defined risk)'],
        ['Margin — hedged', '<span id="pfMargin" style="color:#8b949e">⏳</span>', _PF.C.blu, '<span id="pfMarginSub">basket calc…</span>'],
        ['Total Tax', d.total_tax != null ? '−' + _pfRs(d.total_tax) : '—', _PF.C.neg, 'round-trip, ' + (d.legs ? d.legs.length : 0) + ' legs'],
      ];
      document.getElementById('pfCards').innerHTML = cards.map(c =>
        `<div style="border:1px solid #30363d;border-radius:7px;padding:7px 9px;display:flex;flex-direction:column;gap:2px;text-align:left">
          <span style="font-size:9.5px;color:#8b949e;text-transform:uppercase;letter-spacing:.03em">${c[0]}</span>
          <span style="font-size:15px;font-weight:700;color:${c[2]}">${c[1]}</span>
          <span style="font-size:9px;color:#6e7681">${c[3]}</span>
        </div>`).join('');
      // margin arrives async and this re-renders on every view toggle — repaint
      // it from the cached result instead of dropping back to the ⏳ spinner.
      if (window._pfMargin) _pfPaintMargin(window._pfMargin);
    }

    function _pfPaintMargin(m) {
      const el = document.getElementById('pfMargin'), sub = document.getElementById('pfMarginSub');
      if (!el || !sub) return;
      if (!m || !m.ok) { el.textContent = '—'; sub.textContent = (m && m.msg) ? String(m.msg).slice(0, 40) : 'calc fail'; return; }
      el.textContent = _pfRs(m.hedged); el.style.color = _PF.C.blu;
      const pct = m.standalone ? (m.benefit / m.standalone * 100).toFixed(0) : 0;
      sub.innerHTML = `standalone <s>${_pfRs(m.standalone)}</s> · <b style="color:${_PF.C.pos}">${pct}% kam</b>`;
    }

    // #00 zoom — window range from a zoom factor + center spot (Alt+wheel / drag / buttons).
    // Display-only: re-slices the fixed server curves to a spot window; nothing re-fetched.
    function _pfWin(d) {
      const ce = d.curve_expiry;
      const LO0 = ce[0][0], HI0 = ce[ce.length - 1][0], full = HI0 - LO0;
      const Z = window._pfZoom || (window._pfZoom = { z: 1, c: null });
      const center = Z.c != null ? Z.c
        : (d.spot && d.spot >= LO0 && d.spot <= HI0 ? d.spot : (LO0 + HI0) / 2);
      const half = (full / 2) / Math.max(Z.z, 0.4);
      let lo = center - half, hi = center + half;
      if (lo < LO0) { lo = LO0; hi = Math.min(HI0, lo + 2 * half); }
      if (hi > HI0) { hi = HI0; lo = Math.max(LO0, hi - 2 * half); }
      return { lo, hi, LO0, HI0 };
    }
    function _pfZoomBtn(f) {
      const Z = window._pfZoom || (window._pfZoom = { z: 1, c: null });
      if (f === 'fit') { Z.z = 1; Z.c = null; }
      else { Z.z = Math.max(0.4, Math.min(8, Z.z * (f === 'in' ? 1.3 : 1 / 1.3))); }
      _pfDrawPayoff(window._pfData);
    }

    function _pfDrawPayoff(d) {
      const svg = document.getElementById('pfChart'); if (!svg) return;
      const C = _PF.C, W = 940, H = 300, mL = 70, mR = 14, mT = 20, mB = 40;
      const pw = W - mL - mR, ph = H - mT - mB;
      svg.innerHTML = '';   // redraw on view toggle / zoom
      const ce = d.curve_expiry, ct = d.curve_today, cx = d.curve_target;
      const win = _pfWin(d), LO = win.lo, HI = win.hi;
      const qtyLot = (d.legs && d.legs[0] && d.legs[0].qty) ? d.legs[0].qty : 1;
      const ppt = v => v / qtyLot;                         // ₹ → premium points (#01 amt+pt)
      // y-range from the VISIBLE slice only → real zoom (both axes tighten)
      const inWin = arr => arr.filter(p => p[0] >= LO && p[0] <= HI);
      let ys = inWin(ce).map(p => p[1]);
      if (ct) ys = ys.concat(inWin(ct).map(p => p[1]));
      if (cx) ys = ys.concat(inWin(cx).map(p => p[1]));
      if (!ys.length) ys = ce.map(p => p[1]);
      let yT = Math.max(...ys), yB = Math.min(...ys); const pad = (yT - yB) * .12 || 100; yT += pad; yB -= pad;
      const X = s => mL + (s - LO) / (HI - LO) * pw, Y = v => mT + (yT - v) / (yT - yB) * ph;
      const y0 = Y(0);
      // clip so a curve running past the zoom window doesn't overflow the plot
      const clip = _pfEl(svg, 'clipPath', { id: 'pfClip' });
      _pfEl(clip, 'rect', { x: mL, y: mT, width: pw, height: ph });
      [Math.max(...ys), 0, Math.min(...ys)].forEach(v => {
        _pfEl(svg, 'line', { x1: mL, y1: Y(v), x2: W - mR, y2: Y(v), stroke: C.grid, 'stroke-width': 1 });
        _pfEl(svg, 'text', { x: mL - 7, y: Y(v) + 3.5, 'text-anchor': 'end', 'font-size': 9.5, fill: C.mut }, _pfRs(v));
      });
      const area = sign => {
        let dd = '';
        ce.forEach((p, i) => {
          const v = sign > 0 ? Math.max(p[1], 0) : Math.min(p[1], 0);
          dd += (i ? 'L' : 'M' + X(ce[0][0]).toFixed(1) + ' ' + y0.toFixed(1) + ' L') + X(p[0]).toFixed(1) + ' ' + Y(v).toFixed(1) + ' ';
        });
        return dd + 'L' + X(ce[ce.length - 1][0]).toFixed(1) + ' ' + y0.toFixed(1) + ' Z';
      };
      _pfEl(svg, 'path', { d: area(1), fill: C.pos, 'fill-opacity': .15, 'clip-path': 'url(#pfClip)' });
      _pfEl(svg, 'path', { d: area(-1), fill: C.neg, 'fill-opacity': .15, 'clip-path': 'url(#pfClip)' });
      _pfEl(svg, 'line', { x1: mL, y1: y0, x2: W - mR, y2: y0, stroke: C.mut, 'stroke-width': 1, 'stroke-dasharray': '2 3' });
      [...new Set(d.legs.map(l => l.strike))].sort((a, b) => a - b).forEach(k => {
        if (k < LO || k > HI) return;
        _pfEl(svg, 'line', { x1: X(k), y1: mT, x2: X(k), y2: H - mB, stroke: C.mut, 'stroke-width': 1, 'stroke-dasharray': '4 4', 'stroke-opacity': .4 });
        _pfEl(svg, 'text', { x: X(k), y: mT + 10, 'text-anchor': 'middle', 'font-size': 9, fill: C.mut }, _pfNf(k));
      });
      for (let i = 0; i <= 5; i++) {
        const s = LO + (HI - LO) * i / 5;
        _pfEl(svg, 'text', { x: X(s), y: H - mB + 15, 'text-anchor': 'middle', 'font-size': 9.5, fill: C.mut }, _pfNf(s));
      }
      const tgtView = window._pfView === 'target' && cx;
      ((tgtView ? d.breakevens_target : d.breakevens) || []).forEach(b => {
        if (b < LO || b > HI) return;
        _pfEl(svg, 'line', { x1: X(b), y1: mT, x2: X(b), y2: H - mB, stroke: C.amb, 'stroke-width': 1.6 });
        _pfEl(svg, 'text', { x: X(b), y: H - mB + 28, 'text-anchor': 'middle', 'font-size': 10, 'font-weight': 700, fill: C.amb }, 'BE ' + _pfNf(b));
      });
      const line = (arr, col, w, dash) => {
        let dd = ''; arr.forEach((p, i) => { dd += (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1) + ' '; });
        const o = { d: dd, fill: 'none', stroke: col, 'stroke-width': w, 'stroke-linejoin': 'round', 'clip-path': 'url(#pfClip)' };
        if (dash) o['stroke-dasharray'] = dash;
        _pfEl(svg, 'path', o);
      };
      if (ct) line(ct, C.blu, 1.9);
      if (cx) line(cx, C.amb, tgtView ? 2.2 : 1.5, '6 3');
      line(ce, C.pos, tgtView ? 1.5 : 2.2);
      if (d.spot && d.spot >= LO && d.spot <= HI) {
        _pfEl(svg, 'line', { x1: X(d.spot), y1: mT, x2: X(d.spot), y2: H - mB, stroke: C.txt, 'stroke-width': 1.4, 'stroke-opacity': .6 });
        _pfEl(svg, 'text', { x: X(d.spot), y: mT - 5, 'text-anchor': 'middle', 'font-size': 9.5, 'font-weight': 700, fill: C.txt }, 'Spot ' + _pfNf(d.spot));
      }
      if (window._pfZoom && Math.abs(window._pfZoom.z - 1) > 0.01)
        _pfEl(svg, 'text', { x: W - mR, y: mT - 6, 'text-anchor': 'end', 'font-size': 9, 'font-weight': 700, fill: C.blu }, window._pfZoom.z.toFixed(1) + '×');
      const hl = _pfEl(svg, 'line', { x1: 0, y1: mT, x2: 0, y2: H - mB, stroke: C.txt, 'stroke-width': 1, 'stroke-opacity': .3, visibility: 'hidden' });
      const hb = document.getElementById('pfHover');
      const at = (arr, s) => { const lo = arr[0][0], hi = arr[arr.length - 1][0]; const i = Math.round((s - lo) / (hi - lo) * (arr.length - 1)); return arr[Math.max(0, Math.min(arr.length - 1, i))][1]; };
      svg.addEventListener('mousemove', e => {
        if (window._pfDrag) return;
        const r = svg.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * W;
        const s = Math.max(LO, Math.min(HI, LO + (px - mL) / pw * (HI - LO)));
        hl.setAttribute('x1', X(s)); hl.setAttribute('x2', X(s)); hl.setAttribute('visibility', 'visible');
        const ve = at(ce, s);
        hb.innerHTML = `NIFTY <b>${_pfNf(s)}</b> → expiry <b style="color:${ve >= 0 ? C.pos : C.neg}">${ve >= 0 ? '+' : ''}${_pfRs(ve)} · ${(ppt(ve) >= 0 ? '+' : '') + ppt(ve).toFixed(1)}pt</b>`
          + (ct ? ` · aaj <b style="color:${C.blu}">${(at(ct, s) >= 0 ? '+' : '') + _pfRs(at(ct, s))}</b>` : '')
          + (cx ? ` · exit <b style="color:${C.amb}">${(at(cx, s) >= 0 ? '+' : '') + _pfRs(at(cx, s))}</b>` : '');
      });
      svg.addEventListener('mouseleave', () => { hl.setAttribute('visibility', 'hidden'); hb.textContent = ''; });

      // ── zoom (Alt+wheel at cursor) + drag-pan — bound ONCE per svg element ──
      if (!svg._pfZoomBound) {
        svg._pfZoomBound = true;
        svg.addEventListener('wheel', e => {
          if (!e.altKey) return;                               // plain scroll = page scroll
          e.preventDefault();
          const r = svg.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * W;
          const wn = _pfWin(window._pfData); const s = wn.lo + (px - mL) / pw * (wn.hi - wn.lo);
          const Z = window._pfZoom; Z.c = s; Z.z = Math.max(0.4, Math.min(8, Z.z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
          _pfDrawPayoff(window._pfData);
        }, { passive: false });
        svg.style.cursor = 'grab';
        svg.addEventListener('mousedown', e => {
          const wn = _pfWin(window._pfData);
          window._pfDrag = { x0: e.clientX, c0: (window._pfZoom.c != null ? window._pfZoom.c : (wn.lo + wn.hi) / 2), pw, span: wn.hi - wn.lo };
          svg.style.cursor = 'grabbing';
        });
      }
      // global drag listeners — bound ONCE ever (no per-open leak)
      if (!window._pfZoomWinBound) {
        window._pfZoomWinBound = true;
        window.addEventListener('mousemove', e => {
          const M = window._pfDrag; if (!M) return;
          const s = document.getElementById('pfChart'); if (!s) return;
          const r = s.getBoundingClientRect(); const dpx = (e.clientX - M.x0) / r.width * 940;
          window._pfZoom.c = M.c0 - dpx / M.pw * M.span;
          _pfDrawPayoff(window._pfData);
        });
        window.addEventListener('mouseup', () => {
          if (window._pfDrag) { window._pfDrag = null; const s = document.getElementById('pfChart'); if (s) s.style.cursor = 'grab'; }
        });
      }
    }

    async function _pfLoadMargin(qs) {
      try {
        const rr = await fetch('/api/position-payoff-margin?' + qs);
        window._pfMargin = await rr.json();
      } catch (e) { window._pfMargin = { ok: false, msg: 'calc fail' }; }
      _pfPaintMargin(window._pfMargin);
    }

    async function _pfLoadSeries(qs) {
      const combo = document.getElementById('pfComboSlot');   // RIGHT column
      const legSlot = document.getElementById('pfLegSlot');   // full-width below
      if (!combo) return;
      combo.innerHTML = '<div class="pf-panel" style="font-size:11px;color:#8b949e;padding:24px 10px;text-align:center">⏳ legs ke candles aa rahe…</div>';
      if (legSlot) legSlot.innerHTML = '';
      window._pfSeriesToken = qs;    // only the LATEST selected group renders (drop stale slow responses)
      let s;
      try {
        const r = await fetch('/api/position-legs-series?' + qs);
        s = await r.json();
      } catch (e) { if (window._pfSeriesToken === qs) combo.innerHTML = '<div class="pf-panel" style="color:#f85149;font-size:11px;padding:12px">series fail: ' + e.message + '</div>'; return; }
      if (window._pfSeriesToken !== qs) return;   // user switched to a newer group mid-fetch — ignore this one
      if (!s || !s.ok) { combo.innerHTML = '<div class="pf-panel" style="color:#f85149;font-size:11px;padding:12px">' + ((s && s.msg) || 'series nahi mili') + '</div>'; return; }
      const n = s.legs.length;
      combo.innerHTML = `
        <div class="pf-panel">
          <div style="font-size:11px;font-weight:600;color:#8b949e;margin:0 0 4px;text-align:left">
            Combined Premium — net structure P&amp;L <span style="color:#6e7681;font-weight:400">(entry ${s.from} → ${s.to}, real 1-min)</span>
          </div>
          <div style="overflow-x:auto"><svg id="pfComb" viewBox="0 0 940 220" style="width:100%;height:auto"></svg></div>
          <div id="pfCombHover" style="text-align:center;font-size:11px;color:#8b949e;min-height:15px;margin-top:4px"></div>
          <div id="pfExitRow" style="display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin-top:8px;padding-top:9px;border-top:1px solid #30363d">
            <div style="min-width:96px"><div style="font-size:9.5px;color:#8b949e;text-transform:uppercase;letter-spacing:.03em">Target (book profit)</div><div id="pfExTgt" style="font-family:ui-monospace,monospace;font-size:13px;font-weight:700;color:#3fb950">—</div></div>
            <div style="min-width:96px"><div style="font-size:9.5px;color:#8b949e;text-transform:uppercase;letter-spacing:.03em">Stop-loss</div><div id="pfExSl" style="font-family:ui-monospace,monospace;font-size:13px;font-weight:700;color:#f85149">—</div></div>
            <div style="min-width:96px"><div style="font-size:9.5px;color:#8b949e;text-transform:uppercase;letter-spacing:.03em">Live net MTM</div><div id="pfExLive" style="font-family:ui-monospace,monospace;font-size:13px;font-weight:700">—</div></div>
            <button id="pfExApply" onclick="_pfExitApply()" style="background:#1f6feb;color:#fff;border:0;border-radius:6px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer">Apply auto-exit rule</button>
            <button id="pfExClear" onclick="_pfExitClear()" style="background:#161b22;color:#8b949e;border:1px solid #30363d;border-radius:6px;padding:7px 10px;font-size:11px;font-weight:600;cursor:pointer">Clear</button>
            <span style="font-size:9.5px;font-weight:700;color:#d29922;border:1px solid #d29922;border-radius:4px;padding:1px 5px">PAPER</span>
            <span id="pfExNote" style="font-size:11px;color:#8b949e;flex:1;min-width:170px">Green (Target) / red (SL) line ko drag karo — jaise hi combined MTM chhue, poori group ek saath square off.</span>
          </div>
        </div>`;
      if (legSlot) legSlot.innerHTML = `
        <div class="pf-panel">
          <div style="font-size:11px;font-weight:600;color:#8b949e;margin:0 0 6px;text-align:left">
            Legs — standalone (${n}) <span style="color:#6e7681;font-weight:400">· har leg ka apna premium · dashed = entry · colour = favour me hai ya nahi</span>
          </div>
          <div id="pfGrid"></div>
        </div>`;
      _pfDrawCombined(s);
      _pfDrawGrid(s);
      // a CLOSED group can't auto-exit — show the past premium path, disable Apply
      if (window._pfClosed) {
        const ab = document.getElementById('pfExApply'), cb = document.getElementById('pfExClear');
        if (ab) { ab.disabled = true; ab.style.opacity = '.45'; ab.style.cursor = 'not-allowed'; ab.title = 'position already closed'; }
        if (cb) { cb.style.display = 'none'; }
        const note = document.getElementById('pfExNote');
        if (note) note.textContent = 'Ye group band ho chuka — combined premium ka past path (auto-exit sirf open group pe).';
      }
    }

    function _pfDrawCombined(s) {
      const svg = document.getElementById('pfComb'); if (!svg || !s.combined.length) return;
      window._pfSeries = s;                                  // for drag-redraw
      const C = _PF.C, W = 940, H = 220, mL = 70, mR = 14, mT = 12, mB = 26;
      const pw = W - mL - mR, ph = H - mT - mB;
      svg.innerHTML = '';                                    // redraw on drag
      const D = s.combined, qty = s.legs[0] ? s.legs[0].qty : 1;
      const vals = D.map(p => p[1] * qty);
      let yT = Math.max(...vals), yB = Math.min(...vals);
      // #02 init the SL/Target levels (₹, whole position) so both lines sit on-chart
      if (!window._pfExit) {
        const mag = Math.max(Math.abs(yT), Math.abs(yB), 500);
        window._pfExit = { tgt: Math.round(mag * 0.6 / 100) * 100, sl: -Math.round(mag * 0.6 / 100) * 100 };
      }
      const EX = window._pfExit;
      yT = Math.max(yT, EX.tgt); yB = Math.min(yB, EX.sl);   // keep both lines visible
      const pad = (yT - yB) * .15 || 100; yT += pad; yB -= pad;
      const X = i => mL + i / Math.max(D.length - 1, 1) * pw, Y = v => mT + (yT - v) / (yT - yB) * ph;
      window._pfCombGeo = { mT, ph, yT, yB, qty, H };         // drag pixel → ₹ value
      const y0 = Y(0);
      [yT - pad, 0, yB + pad].forEach(v => {
        _pfEl(svg, 'line', { x1: mL, y1: Y(v), x2: W - mR, y2: Y(v), stroke: C.grid, 'stroke-width': 1 });
        _pfEl(svg, 'text', { x: mL - 7, y: Y(v) + 3.5, 'text-anchor': 'end', 'font-size': 9.5, fill: C.mut }, _pfRs(v));
      });
      _pfEl(svg, 'line', { x1: mL, y1: y0, x2: W - mR, y2: y0, stroke: C.mut, 'stroke-width': 1, 'stroke-dasharray': '2 3' });
      for (let i = 1; i < D.length; i++) {
        if (D[i][0] - D[i - 1][0] > 3600 * 3) {
          const bx = (X(i) + X(i - 1)) / 2;
          _pfEl(svg, 'line', { x1: bx, y1: mT, x2: bx, y2: H - mB, stroke: C.mut, 'stroke-width': 1, 'stroke-dasharray': '3 3', 'stroke-opacity': .6 });
          _pfEl(svg, 'text', { x: bx + 3, y: mT + 9, 'text-anchor': 'start', 'font-size': 8.5, fill: C.mut }, 'overnight');
        }
      }
      let dp = '', da = '';
      D.forEach((p, i) => {
        const xx = X(i), yy = Y(p[1] * qty);
        dp += (i ? 'L' : 'M') + xx.toFixed(1) + ' ' + yy.toFixed(1) + ' ';
        da += (i ? 'L' : 'M' + xx.toFixed(1) + ' ' + y0.toFixed(1) + ' L') + xx.toFixed(1) + ' ' + yy.toFixed(1) + ' ';
      });
      da += 'L' + X(D.length - 1).toFixed(1) + ' ' + y0.toFixed(1) + ' Z';
      _pfEl(svg, 'path', { d: da, fill: C.blu, 'fill-opacity': .12 });
      _pfEl(svg, 'path', { d: dp, fill: 'none', stroke: C.blu, 'stroke-width': 1.8, 'stroke-linejoin': 'round' });
      const lastV = vals[vals.length - 1];
      _pfEl(svg, 'circle', { cx: X(D.length - 1), cy: Y(lastV), r: 4, fill: lastV >= 0 ? C.pos : C.neg });
      _pfEl(svg, 'text', { x: X(D.length - 1) - 6, y: Y(lastV) - 8, 'text-anchor': 'end', 'font-size': 10, 'font-weight': 700, fill: lastV >= 0 ? C.pos : C.neg }, (lastV >= 0 ? '+' : '') + _pfRs(lastV));
      // #02 Target + SL draggable lines
      const drawLvl = (id, val, col, lab) => {
        const y = Y(val);
        _pfEl(svg, 'line', { x1: mL, y1: y, x2: W - mR, y2: y, stroke: col, 'stroke-width': 1.6, 'stroke-dasharray': '6 4' });
        const band = _pfEl(svg, 'rect', { x: mL, y: y - 7, width: pw, height: 14, fill: 'transparent', style: 'cursor:ns-resize' });
        band.dataset.lv = id;
        const pt = val / qty;
        const tag = lab + ' ' + (val >= 0 ? '+' : '') + Math.round(val / 1000) + 'k·' + (pt >= 0 ? '+' : '') + pt.toFixed(0) + 'p';
        const tw = 10 + tag.length * 5.3;
        _pfEl(svg, 'rect', { x: W - mR - tw, y: y - 9, width: tw, height: 18, rx: 4, fill: col });
        _pfEl(svg, 'text', { x: W - mR - tw / 2, y: y + 4, 'text-anchor': 'middle', 'font-size': 9.5, 'font-weight': 700, fill: '#0d1117', 'font-family': 'ui-monospace,monospace' }, tag);
      };
      drawLvl('tgt', EX.tgt, C.pos, 'Target');
      drawLvl('sl', EX.sl, C.neg, 'SL');
      const hl = _pfEl(svg, 'line', { x1: 0, y1: mT, x2: 0, y2: H - mB, stroke: C.txt, 'stroke-width': 1, 'stroke-opacity': .3, visibility: 'hidden' });
      const hb = document.getElementById('pfCombHover');
      svg.addEventListener('mousemove', e => {
        if (window._pfCombDrag) return;
        const r = svg.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * W;
        let i = Math.round((px - mL) / pw * (D.length - 1)); i = Math.max(0, Math.min(D.length - 1, i));
        hl.setAttribute('x1', X(i)); hl.setAttribute('x2', X(i)); hl.setAttribute('visibility', 'visible');
        const v = D[i][1] * qty;
        hb.innerHTML = `<b>${_pfIST(D[i][0])}</b> · net P&L <b style="color:${v >= 0 ? C.pos : C.neg}">${v >= 0 ? '+' : ''}${_pfRs(v)} · ${(D[i][1] >= 0 ? '+' : '') + D[i][1].toFixed(1)}pt</b>`;
      });
      svg.addEventListener('mouseleave', () => { hl.setAttribute('visibility', 'hidden'); hb.textContent = ''; });
      _pfUpdExitRow(lastV);
      _pfSetHdrPnl(lastV, window._pfData);   // refine header P&L with real-premium net MTM
      // drag the SL/Target lines — svg-level bound once per element, window-level once ever
      if (!svg._pfCombBound) {
        svg._pfCombBound = true;
        svg.addEventListener('mousedown', e => {
          const t = e.target; if (t && t.dataset && t.dataset.lv) { window._pfCombDrag = t.dataset.lv; e.preventDefault(); }
        });
      }
      if (!window._pfCombWinBound) {
        window._pfCombWinBound = true;
        window.addEventListener('mousemove', e => {
          if (!window._pfCombDrag) return;
          const g = window._pfCombGeo, sv = document.getElementById('pfComb'); if (!g || !sv) return;
          const r = sv.getBoundingClientRect(); const py = (e.clientY - r.top) / r.height * g.H;
          let val = g.yT - (py - g.mT) / g.ph * (g.yT - g.yB); val = Math.round(val / 100) * 100;
          if (window._pfCombDrag === 'tgt') window._pfExit.tgt = Math.max(val, 100);
          else window._pfExit.sl = Math.min(val, -100);
          if (window._pfSeries) _pfDrawCombined(window._pfSeries);
        });
        window.addEventListener('mouseup', () => { window._pfCombDrag = null; });
      }
    }

    function _pfUpdExitRow(liveVal) {
      const EX = window._pfExit; if (!EX) return;
      const qty = (window._pfCombGeo && window._pfCombGeo.qty) || 1;
      const set = (id, v, col) => {
        const e = document.getElementById(id); if (!e) return;
        const pt = v / qty;
        e.innerHTML = (v >= 0 ? '+' : '') + _pfRs(v) + ' · ' + (pt >= 0 ? '+' : '') + pt.toFixed(1) + 'p';
        if (col) e.style.color = col;
      };
      set('pfExTgt', EX.tgt, _PF.C.pos);
      set('pfExSl', EX.sl, _PF.C.neg);
      const le = document.getElementById('pfExLive');
      if (le && liveVal != null) {
        const pt = liveVal / qty;
        le.innerHTML = (liveVal >= 0 ? '+' : '') + _pfRs(liveVal) + ' · ' + (pt >= 0 ? '+' : '') + pt.toFixed(1) + 'p';
        le.style.color = liveVal >= 0 ? _PF.C.pos : _PF.C.neg;
      }
    }

    // #02 arm/clear the combined-MTM auto-exit rule for this group (PAPER; engine = trader_dashboard)
    async function _pfExitApply() {
      const EX = window._pfExit, qs = window._pfQS; if (!EX || !qs || window._pfClosed) return;
      const btn = document.getElementById('pfExApply');
      if (btn) { btn.disabled = true; btn.textContent = '⏳ applying…'; }
      let j = null;
      try {
        const r = await fetch('/api/position-exit-rule', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ qs, target_rs: EX.tgt, sl_rs: EX.sl })
        });
        j = await r.json();
      } catch (e) { j = { ok: false, msg: e.message }; }
      if (btn) {
        btn.disabled = false;
        if (j && j.ok) { btn.textContent = '✓ Auto-exit armed (paper)'; btn.style.background = '#238636'; setTimeout(() => { btn.textContent = 'Apply auto-exit rule'; btn.style.background = '#1f6feb'; }, 2400); }
        else btn.textContent = 'Apply auto-exit rule';
      }
      const note = document.getElementById('pfExNote');
      if (note) note.innerHTML = (j && j.ok)
        ? '✓ Rule armed — Target <b style="color:#3fb950">' + _pfRs(EX.tgt) + '</b> / SL <b style="color:#f85149">' + _pfRs(EX.sl) + '</b>. Combined MTM chhute hi poori group square off.'
        : '<span style="color:#f85149">' + ((j && j.msg) || 'apply fail') + '</span>';
    }
    async function _pfExitClear() {
      const qs = window._pfQS; if (!qs) return;
      try { await fetch('/api/position-exit-rule?' + qs, { method: 'DELETE' }); } catch (e) {}
      const note = document.getElementById('pfExNote'); if (note) note.textContent = 'Auto-exit rule cleared for this group.';
    }

    function _pfDrawGrid(s) {
      const grid = document.getElementById('pfGrid'); if (!grid) return;
      grid.innerHTML = s.legs.map((L, i) => {
        const sideCol = L.side === 'BUY' ? '#3fb950' : '#f85149';
        return `<div style="border:1px solid #30363d;border-radius:7px;padding:8px;text-align:left">
          <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
            <span style="font-size:11px;font-weight:700;color:#e6edf3">${L.trad_sym}</span>
            <span style="font-size:10px;font-weight:700;color:${sideCol}">${L.side}</span>
          </div>
          <div style="font-size:9.5px;color:#8b949e;margin-bottom:4px">entry ${L.entry.toFixed(2)} · qty ${L.qty}<span id="pfLegNow${i}"></span></div>
          <svg id="pfLeg${i}" viewBox="0 0 440 130" style="width:100%;height:auto"></svg>
        </div>`;
      }).join('');
      s.legs.forEach((L, i) => _pfDrawLeg(L, i));
    }

    function _pfDrawLeg(L, idx) {
      const svg = document.getElementById('pfLeg' + idx); if (!svg || !L.series.length) return;
      const C = _PF.C, W = 440, H = 130, mL = 42, mR = 8, mT = 8, mB = 18;
      const pw = W - mL - mR, ph = H - mT - mB;
      const D = L.series, cl = D.map(p => p[1]).concat([L.entry]);
      let yT = Math.max(...cl), yB = Math.min(...cl); const pad = (yT - yB) * .12 || 1; yT += pad; yB -= pad;
      const X = i => mL + i / Math.max(D.length - 1, 1) * pw, Y = v => mT + (yT - v) / (yT - yB) * ph;
      [yT - pad, yB + pad].forEach(v => {
        _pfEl(svg, 'line', { x1: mL, y1: Y(v), x2: W - mR, y2: Y(v), stroke: C.grid, 'stroke-width': 1 });
        _pfEl(svg, 'text', { x: mL - 5, y: Y(v) + 3, 'text-anchor': 'end', 'font-size': 8.5, fill: C.mut }, v.toFixed(0));
      });
      _pfEl(svg, 'line', { x1: mL, y1: Y(L.entry), x2: W - mR, y2: Y(L.entry), stroke: C.amb, 'stroke-width': 1, 'stroke-dasharray': '4 3' });
      for (let i = 1; i < D.length; i++) {
        if (D[i][0] - D[i - 1][0] > 3600 * 3) {
          const bx = (X(i) + X(i - 1)) / 2;
          _pfEl(svg, 'line', { x1: bx, y1: mT, x2: bx, y2: H - mB, stroke: C.mut, 'stroke-width': 1, 'stroke-dasharray': '2 3', 'stroke-opacity': .55 });
        }
      }
      let dp = ''; D.forEach((p, i) => { dp += (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(p[1]).toFixed(1) + ' '; });
      // a leg is in our favour when premium moved our way: SELL wants it DOWN, BUY wants it UP
      const last = D[D.length - 1][1];
      const col = (L.side === 'SELL' ? (last < L.entry) : (last > L.entry)) ? C.pos : C.neg;
      _pfEl(svg, 'path', { d: dp, fill: 'none', stroke: col, 'stroke-width': 1.6, 'stroke-linejoin': 'round' });
      _pfEl(svg, 'circle', { cx: X(D.length - 1), cy: Y(last), r: 3, fill: col });
      const legPnl = (L.side === 'SELL' ? (L.entry - last) : (last - L.entry)) * L.qty;
      const nowEl = document.getElementById('pfLegNow' + idx);
      if (nowEl) nowEl.innerHTML = ` · now <b style="color:${col}">${last.toFixed(2)}</b>`
        + ` · <b style="color:${legPnl >= 0 ? C.pos : C.neg}">${legPnl >= 0 ? '+' : ''}${_pfRs(legPnl)}</b>`;
      _pfEl(svg, 'text', { x: mL, y: H - mB + 12, 'text-anchor': 'start', 'font-size': 8, fill: C.mut }, _pfIST(D[0][0]));
      _pfEl(svg, 'text', { x: W - mR, y: H - mB + 12, 'text-anchor': 'end', 'font-size': 8, fill: C.mut }, _pfIST(D[D.length - 1][0]));
    }

    // Run-Up (best favourable ₹) / Run-Down (worst adverse ₹) for one trade,
    // from its MAX_LTP/MIN_LTP tags — shared by the summary table (task 73).
    function _tradeRunAmts(t) {
      let up = 0, down = 0;
      const ep = t.entry_price || 0;
      let maxL = null, minL = null;
      (t.tags || []).forEach(tg => {
        if (tg.startsWith('MAX_LTP:')) maxL = parseFloat(tg.split(':')[1]);
        if (tg.startsWith('MIN_LTP:')) minL = parseFloat(tg.split(':')[1]);
      });
      if (ep > 0 && maxL !== null && minL !== null && !isNaN(maxL) && !isNaN(minL)) {
        const upPt = t.entry === 'SELL' ? (ep - minL) : (maxL - ep);
        const downPt = t.entry === 'SELL' ? (ep - maxL) : (minL - ep);
        up = upPt * (t.qty || 0); down = downPt * (t.qty || 0);
      }
      return { up, down };
    }

    // One Completed-Trades <tr> — extracted so both flat mode and grouped mode
    // (see renderCachedOrders) render individual trade rows identically.
    // Returns {html, g, tx, n, pts, inv} so callers can accumulate totals.
    function _completedRowHtml(t, _idx, activeCols, ordDate, subRow, prefix = '') {
      const g = t._gross, tx = t._tax, n = t._net;
      const nc = n > 0 ? '#3fb950' : (n < 0 ? '#f85149' : '#e6edf3');
      const gc = g > 0 ? '#3fb950' : (g < 0 ? '#f85149' : '#e6edf3');

      const inv = t.qty * (t.entry_price || 0);
      const retPct = inv > 0 ? ((n / inv) * 100).toFixed(2) + '%' : '—';
      const rc = n > 0 ? '#3fb950' : (n < 0 ? '#f85149' : '#8b949e');
      const pts = t.entry === 'BUY' ? (t.exit_price || 0) - (t.entry_price || 0) : (t.entry_price || 0) - (t.exit_price || 0);
      const ptsC = pts > 0 ? '#3fb950' : (pts < 0 ? '#f85149' : '#8b949e');

      let max_pnl = '—', min_pnl = '—';
      let note = '';
      if (t.tags) {
        let max_ltp = null, min_ltp = null;
        t.tags.forEach(tg => {
          if (tg.startsWith('MAX_LTP:')) max_ltp = parseFloat(tg.split(':')[1]);
          if (tg.startsWith('MIN_LTP:')) min_ltp = parseFloat(tg.split(':')[1]);
          if (tg.startsWith('NOTE:')) note = tg.substring(5);
        });

        const ep = t.entry_price || 0;
        // Run-Up = best favourable excursion, Run-Down = worst adverse excursion,
        // each shown as AMT (₹) | PT (points) | % (was ₹-only before).
        // PT = per-unit premium move, AMT = PT × qty, % = PT / entry_price × 100.
        if (ep > 0 && max_ltp !== null && min_ltp !== null) {
          const upPt   = t.entry === 'SELL' ? (ep - min_ltp) : (max_ltp - ep);
          const downPt = t.entry === 'SELL' ? (ep - max_ltp) : (min_ltp - ep);
          max_pnl = _ruCell(upPt,   upPt   * t.qty, upPt   / ep * 100);
          min_pnl = _ruCell(downPt, downPt * t.qty, downPt / ep * 100);
        }
      }

      let imgs = _imgTagsOf(t);

      const isNoteHidden = window._hiddenNotes.has(t.id) || localStorage.getItem('global_notes_show') !== 'true';
      let dispNote = '';
      if (note || (imgs && imgs.length)) {
        dispNote = `<div id="note-wrapper-${t.id}" style="${isNoteHidden ? 'display:none;' : ''}">`
          + (note ? `<div style="color:#d29922;font-size:10px;margin-top:4px;white-space:normal;line-height:1.3;max-width:300px" title="${note.replace(/"/g, '&quot;')}">${note.replace(/</g, '&lt;').replace(/\n/g, '<br>')}</div>` : '')
          + _noteThumbs(t.id, imgs)
          + `</div>`;
      }

      let rowHtml = `<tr style="border-bottom:1px solid #21262d${subRow ? ';background:#0d1117' : ''}">`;

      activeCols.forEach(c => {
        let val = '';
        let colorStyle = '';

        switch (c.id) {
          case 'date':
            val = t.entry_date || ordDate;
            colorStyle = 'color:#6e7681;';
            val = `<span style="white-space:nowrap;">${val}</span>`;
            break;
          case 'symbol':
            const isNoteColOn = activeCols.some(x => x.id === 'note');
            val = (subRow ? '<span style="color:#6e7681;margin-right:4px">↳</span>' : '') + `<b>${t.sym}</b>` + (isNoteColOn ? '' : dispNote);
            colorStyle = 'color:#adbac7;';
            break;
          case 'instrument':
            val = _instrCell(t);
            break;
          case 'strategy':
            val = _stratCell(t);
            break;
          case 'tags':
            val = _ordTags(t, true);
            break;
          case 'side':
            val = t.entry;
            colorStyle = 'color:' + (t.entry === 'BUY' ? '#3fb950' : '#f85149') + ';font-weight:600;';
            break;
          case 'entry_px':
            val = Number(t.entry_price || 0).toFixed(2);
            colorStyle = 'color:#8b949e;';
            break;
          case 'exit_px':
            val = Number(t.exit_price || 0).toFixed(2);
            colorStyle = 'color:#8b949e;';
            break;
          case 'entry_time':
            val = t.entry_time || '—';
            colorStyle = 'color:#6e7681;';
            break;
          case 'exit_time':
            val = t.exit_time || '—';
            colorStyle = 'color:#6e7681;';
            break;
          case 'exit_reason': {
            const _erB = (t.exit_reason || '').split(':')[0];
            const _erBadge = _exitReasonBadge(t.exit_reason);
            val = _erB
              ? `<span onclick="_ordExitChipClick(event,'${_erB.replace(/'/g, '')}')" title="Ctrl+click: is exit reason pe filter (dobara Ctrl+click = All)" style="cursor:pointer">${_erBadge}</span>`
              : _erBadge;
            break;
          }
          case 'duration':
            val = _durFmt(t.entry_time, t.exit_time);
            colorStyle = 'color:#8b949e;';
            val = `<span style="white-space:nowrap;">${val}</span>`;
            break;
          case 'qty':
            val = t.qty;
            break;
          case 'points':
            val = (pts >= 0 ? '+' : '') + pts.toFixed(2);
            colorStyle = 'color:' + ptsC + ';';
            break;
          case 'gross':
            val = Math.round(g);
            colorStyle = 'color:' + gc + ';';
            break;
          case 'tax':
            val = '−' + Math.round(tx);
            colorStyle = 'color:#f85149;';
            break;
          case 'net':
            val = Math.round(n);
            colorStyle = 'color:' + nc + ';font-weight:700;';
            break;
          case 'ret_pct':
            val = retPct;
            colorStyle = 'color:' + rc + ';';
            break;
          case 'run_up':
            val = max_pnl;
            break;
          case 'run_down':
            val = min_pnl;
            break;
          case 'cumulative':
            // Only meaningful when rows are in a fixed chronological order — blank
            // otherwise so it's never misread as a real running total under any
            // other sort. renderCachedOrders() sets t._cumulative before calling
            // this function, only when sorted by exit_time ascending + not grouped.
            if (t._cumulative != null) {
              const cc = t._cumulative >= 0 ? '#3fb950' : '#f85149';
              val = `<span style="color:${cc}">${t._cumulative >= 0 ? '+' : ''}${Math.round(t._cumulative).toLocaleString('en-IN')}</span>`;
            } else {
              val = '<span style="color:#6e7681">—</span>';
            }
            break;
          case 'chart':
            val = `<button onclick="openTradeChart('${(t.sym || '').replace(/'/g, '')}','${t.entry || ''}',${t.entry_price || 0},${t.exit_price || 0},'${t.entry_time || ''}','${t.exit_time || ''}',${t.qty || 0},'${(t.entry_date || ordDate)}',null,null,${_idx},null,null,'${(t.strategy || '').replace(/'/g, '')}')" title="Premium chart" style="padding:3px 9px;font-size:13px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#58a6ff;cursor:pointer">📈</button>`;
            break;
          case 'actions':
            const ddId = prefix ? `${prefix}-dropdown-${t.id}` : `dropdown-${t.id}`;
            const clickFn = prefix ? `toggleCalDropdown` : `toggleDropdown`;
            val = `
          <div class="dropdown">
            <span class="dropdown-trigger" onclick="${clickFn}(event, ${t.id})">⋮</span>
            <div id="${ddId}" class="dropdown-content">
              <a href="javascript:void(0)" onclick="openNoteModal(${t.id})">📝 Edit Note</a>
              <a href="javascript:void(0)" onclick="openTradeChart('${t.sym}','${t.entry}',${t.entry_price},${t.exit_price},'${t.entry_time}','${t.exit_time}',${t.qty},'${(t.entry_date || ordDate)}',null,null,${_idx},null,null,'${(t.strategy || '').replace(/'/g, '')}')">📈 Chart</a>
              <a href="javascript:void(0)" onclick="toggleNoteDesc(${t.id})">👁️ Toggle Note</a>
            </div>
          </div>`;
            break;
        }

        const isRight = ['entry_px', 'exit_px', 'points', 'gross', 'tax', 'net', 'ret_pct', 'run_up', 'run_down', 'cumulative'].includes(c.id);
        const isCenter = ['entry_time', 'exit_time', 'duration', 'actions', 'chart'].includes(c.id);
        const alignStyle = isRight ? 'text-align:right;' : (isCenter ? 'text-align:center;' : '');

        rowHtml += `<td style="padding:7px 6px;vertical-align:top;${alignStyle}${colorStyle}">${val}</td>`;
      });

      rowHtml += '</tr>';
      return { html: rowHtml, g, tx, n, pts, inv };
    }

    function toggleSort(tableType, colId) {
      if (colId === 'actions' || colId === 'chart') return;
      if (tableType === 'completed') {
        if (window._completedSortCol === colId) {
          window._completedSortDir = window._completedSortDir === 'asc' ? 'desc' : 'asc';
        } else {
          window._completedSortCol = colId;
          window._completedSortDir = 'desc';
        }
        localStorage.setItem('ord_completed_sort_col', window._completedSortCol);
        localStorage.setItem('ord_completed_sort_dir', window._completedSortDir);
      } else {
        if (window._openSortCol === colId) {
          window._openSortDir = window._openSortDir === 'asc' ? 'desc' : 'asc';
        } else {
          window._openSortCol = colId;
          window._openSortDir = 'desc';
        }
        localStorage.setItem('ord_open_sort_col', window._openSortCol);
        localStorage.setItem('ord_open_sort_dir', window._openSortDir);
      }
      renderCachedOrders();
    }

    function toggleDropdown(event, tradeId) {
      event.stopPropagation();
      const dropdown = document.getElementById(`dropdown-${tradeId}`);
      if (!dropdown) return;
      const isCurrentlyOpen = dropdown.style.display === 'block';

      document.querySelectorAll('.dropdown-content').forEach(el => {
        el.style.display = 'none';
      });
      if (isCurrentlyOpen) return;

      // Position the ⋮ menu as FIXED relative to the trigger so it escapes the
      // table's overflow-x:auto wrapper — that wrapper used to clip the menu when
      // there were only a row or two ("3-dot menu ajeeb / jagha khatam"). Flips up
      // near the viewport bottom so it's never cut off.
      const trig = event.currentTarget || event.target;
      const r = trig.getBoundingClientRect();
      dropdown.style.display = 'block';
      dropdown.style.position = 'fixed';
      dropdown.style.right = 'auto';
      const mw = dropdown.offsetWidth || 160, mh = dropdown.offsetHeight || 190;
      let left = r.right - mw;            // right-align the menu to the ⋮
      if (left < 8) left = 8;
      let top = r.bottom + 4;
      if (top + mh > window.innerHeight - 8) top = r.top - mh - 4;   // flip up
      if (top < 8) top = 8;
      dropdown.style.left = left + 'px';
      dropdown.style.top = top + 'px';
    }

    window.addEventListener('click', function (e) {
      if (!e.target.matches('.dropdown-trigger')) {
        document.querySelectorAll('.dropdown-content').forEach(el => {
          el.style.display = 'none';
        });
      }
    });

    function toggleNoteDesc(tradeId) {
      const elements = document.querySelectorAll(`[id="note-wrapper-${tradeId}"]`);
      if (elements.length > 0) {
        const isHidden = elements[0].style.display === 'none';
        elements.forEach(el => {
          el.style.display = isHidden ? '' : 'none';
        });
        if (isHidden) {
          window._hiddenNotes.delete(tradeId);
        } else {
          window._hiddenNotes.add(tradeId);
        }
        localStorage.setItem('ord_hidden_notes', JSON.stringify([...window._hiddenNotes]));
      }
    }

    function toggleAllNotes(show) {
      localStorage.setItem('global_notes_show', show ? 'true' : 'false');
      saveUiConfigToBackend('global_notes_show', show ? 'true' : 'false');
      const elements = document.querySelectorAll('[id^="note-wrapper-"]');
      elements.forEach(el => {
        el.style.display = show ? '' : 'none';
        const idAttr = el.getAttribute('id');
        const tradeId = parseInt(idAttr.replace('note-wrapper-', ''));
        if (tradeId) {
          if (show) {
            window._hiddenNotes.delete(tradeId);
          } else {
            window._hiddenNotes.add(tradeId);
          }
        }
      });
      localStorage.setItem('ord_hidden_notes', JSON.stringify([...window._hiddenNotes]));

      // Two checkboxes share this one state (Calendar tab + Orders/P&L tab, next
      // to Reconcile vs Broker per 2026-07-02) — keep both in sync regardless of
      // which one was clicked.
      ['global-notes-toggle', 'global-notes-toggle-2'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.checked = show;
      });
    }

    function initGlobalNotesToggle() {
      const globalShow = localStorage.getItem('global_notes_show') === 'true';
      ['global-notes-toggle', 'global-notes-toggle-2'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.checked = globalShow;
      });
    }

    function _sortData(arr, col, dir) {
      const isDesc = dir === 'desc';
      return arr.sort((a, b) => {
        let valA, valB;
        switch (col) {
          case 'date':
            valA = a.entry_date || '';
            valB = b.entry_date || '';
            break;
          case 'symbol':
            valA = a.sym || '';
            valB = b.sym || '';
            break;
          case 'tags':
            valA = (a.tags || []).join(',');
            valB = (b.tags || []).join(',');
            break;
          case 'side':
            valA = a.entry || '';
            valB = b.entry || '';
            break;
          case 'entry_px':
            valA = a.entry_price || 0;
            valB = b.entry_price || 0;
            break;
          case 'exit_px':
            valA = a.exit_price || 0;
            valB = b.exit_price || 0;
            break;
          case 'entry_time':
            valA = a.entry_time || '';
            valB = b.entry_time || '';
            break;
          case 'exit_time':
            valA = a.exit_time || '';
            valB = b.exit_time || '';
            break;
          case 'duration':
            valA = _durMin(a.entry_time, a.exit_time) || 0;
            valB = _durMin(b.entry_time, b.exit_time) || 0;
            break;
          case 'qty':
            valA = a.qty || 0;
            valB = b.qty || 0;
            break;
          case 'points':
            valA = a.entry === 'BUY' ? (a.exit_price || 0) - (a.entry_price || 0) : (a.entry_price || 0) - (a.exit_price || 0);
            valB = b.entry === 'BUY' ? (b.exit_price || 0) - (b.entry_price || 0) : (b.entry_price || 0) - (b.exit_price || 0);
            break;
          case 'gross':
            valA = a._gross || 0;
            valB = b._gross || 0;
            break;
          case 'tax':
            valA = a._tax || 0;
            valB = b._tax || 0;
            break;
          case 'net':
            valA = a._net || 0;
            valB = b._net || 0;
            break;
          case 'ret_pct':
            {
              const invA = (a.qty || 0) * (a.entry_price || 0);
              const invB = (b.qty || 0) * (b.entry_price || 0);
              valA = invA > 0 ? (a._net || 0) / invA : 0;
              valB = invB > 0 ? (b._net || 0) / invB : 0;
            }
            break;
          case 'run_up':
            {
              let max_ltpA = null, min_ltpA = null;
              if (a.tags) a.tags.forEach(tg => {
                if (tg.startsWith('MAX_LTP:')) max_ltpA = parseFloat(tg.split(':')[1]);
                if (tg.startsWith('MIN_LTP:')) min_ltpA = parseFloat(tg.split(':')[1]);
              });
              if (a.entry_price > 0) {
                if (max_ltpA !== null && a.entry === 'BUY') valA = (max_ltpA - a.entry_price) * a.qty;
                else if (min_ltpA !== null && a.entry === 'SELL') valA = (a.entry_price - min_ltpA) * a.qty;
                else valA = -Infinity;
              } else {
                valA = -Infinity;
              }

              let max_ltpB = null, min_ltpB = null;
              if (b.tags) b.tags.forEach(tg => {
                if (tg.startsWith('MAX_LTP:')) max_ltpB = parseFloat(tg.split(':')[1]);
                if (tg.startsWith('MIN_LTP:')) min_ltpB = parseFloat(tg.split(':')[1]);
              });
              if (b.entry_price > 0) {
                if (max_ltpB !== null && b.entry === 'BUY') valB = (max_ltpB - b.entry_price) * b.qty;
                else if (min_ltpB !== null && b.entry === 'SELL') valB = (b.entry_price - min_ltpB) * b.qty;
                else valB = -Infinity;
              } else {
                valB = -Infinity;
              }
            }
            break;
          case 'run_down':
            {
              let max_ltpA = null, min_ltpA = null;
              if (a.tags) a.tags.forEach(tg => {
                if (tg.startsWith('MAX_LTP:')) max_ltpA = parseFloat(tg.split(':')[1]);
                if (tg.startsWith('MIN_LTP:')) min_ltpA = parseFloat(tg.split(':')[1]);
              });
              if (a.entry_price > 0) {
                if (min_ltpA !== null && a.entry === 'BUY') valA = (min_ltpA - a.entry_price) * a.qty;
                else if (max_ltpA !== null && a.entry === 'SELL') valA = (a.entry_price - max_ltpA) * a.qty;
                else valA = Infinity;
              } else {
                valA = Infinity;
              }

              let max_ltpB = null, min_ltpB = null;
              if (b.tags) b.tags.forEach(tg => {
                if (tg.startsWith('MAX_LTP:')) max_ltpB = parseFloat(tg.split(':')[1]);
                if (tg.startsWith('MIN_LTP:')) min_ltpB = parseFloat(tg.split(':')[1]);
              });
              if (b.entry_price > 0) {
                if (min_ltpB !== null && b.entry === 'BUY') valB = (min_ltpB - b.entry_price) * b.qty;
                else if (max_ltpB !== null && b.entry === 'SELL') valB = (b.entry_price - max_ltpB) * b.qty;
                else valB = Infinity;
              } else {
                valB = Infinity;
              }
            }
            break;
          case 'ltp':
            {
              const rawA = _ltpLive[a.sym];
              valA = typeof rawA === 'number' ? rawA : (rawA?.ltp || a.entry_price || 0);
              const rawB = _ltpLive[b.sym];
              valB = typeof rawB === 'number' ? rawB : (rawB?.ltp || b.entry_price || 0);
            }
            break;
          case 'pnl':
            {
              const rawA = _ltpLive[a.sym];
              const ltpA = typeof rawA === 'number' ? rawA : (rawA?.ltp || a.entry_price || 0);
              const ptsA = a.entry === 'BUY' ? ltpA - a.entry_price : a.entry_price - ltpA;
              valA = ptsA * (a.qty || 0);

              const rawB = _ltpLive[b.sym];
              const ltpB = typeof rawB === 'number' ? rawB : (rawB?.ltp || b.entry_price || 0);
              const ptsB = b.entry === 'BUY' ? ltpB - b.entry_price : b.entry_price - ltpB;
              valB = ptsB * (b.qty || 0);
            }
            break;
          case 'margin':
            valA = a.margin_used || 0;
            valB = b.margin_used || 0;
            break;
          default:
            valA = a[col] || '';
            valB = b[col] || '';
        }

        if (valA === valB) return 0;
        if (valA == null || valA === '') return 1;
        if (valB == null || valB === '') return -1;

        if (typeof valA === 'string' && typeof valB === 'string') {
          return isDesc ? valB.localeCompare(valA) : valA.localeCompare(valB);
        } else {
          return isDesc ? valB - valA : valA - valB;
        }
      });
    }

    // ── 📄 Export Completed Trades → PDF report ──────────────────────────────────
    // Data se banata hai (DOM scrape nahi) taaki actions/sort-arrows/expand-arrows
