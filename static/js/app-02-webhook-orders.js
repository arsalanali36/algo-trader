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
      const filtered = isStratSel ? vals.filter(v => v && v.toLowerCase() !== 'unknown') : vals;
      const want = JSON.stringify([''].concat(filtered));
      const have = JSON.stringify(Array.from(sel.options).map(o => o.value));
      if (have === want) return;
      sel.innerHTML = '<option value="">' + allLabel + '</option>' + filtered.map(v => {
        let display = v;
        if (isStratSel && v.includes(' | ')) display = v.split(' | ')[0];
        if (isStratSel && typeof regLabel === 'function') display = regLabel(display);
        return '<option value="' + v + '">' + display + '</option>';
      }).join('');
      sel.value = cur || '';
    }
    function _ordTags(t) {
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
      let h = _ordTag(t.source, t.source) + _ordTag(t.mode, t.mode) + _sidChip;

      if (isHedge) {
        h += _ordTag('hedge', 'hedge');
      }

      h += _ordTag(t.broker, 'name');
      return h;
    }
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
      const src = _ordSegVal('ord-src'); if (src && src !== 'hedge') q.set('source', src);
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
      if (src === 'hedge') {
        det = det.filter(t => {
          return (t.group_id && t.group_id.startsWith('RANGE_') && t.entry === 'BUY') ||
            (t.correlation_id && t.correlation_id.startsWith('RANGE_') && t.entry === 'BUY') ||
            (t.correlation_id && t.correlation_id.startsWith('HEDGE'));
        });
        d.details = det;
      }

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
    }

    // Global state for sorting & hidden notes
    window._completedSortCol = localStorage.getItem('ord_completed_sort_col') || 'entry_time';
    window._completedSortDir = localStorage.getItem('ord_completed_sort_dir') || 'desc';
    window._openSortCol = localStorage.getItem('ord_open_sort_col') || 'entry_time';
    window._openSortDir = localStorage.getItem('ord_open_sort_dir') || 'desc';
    window._hiddenNotes = new Set(JSON.parse(localStorage.getItem('ord_hidden_notes') || '[]'));

    // Completed-trades "Group by Symbol" — Zerodha Day's History style: one
    // collapsed summary row per symbol (totals), expand to see individual trades.
    window._completedGroupBy = localStorage.getItem('ord_completed_group_by') === 'true';
    window._completedGroupExpanded = new Set();   // symbol keys currently expanded — not persisted, resets per page load (matches Zerodha's own transient expand state)

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

    function openPayoffPanel(idsCsv, name, ev) {
      if (ev) { ev.preventDefault(); ev.stopPropagation(); }
      window._pfView = null; window._pfMargin = null; window._pfData = null;  // fresh per group
      let ov = document.getElementById('pfOverlay');
      if (!ov) {
        ov = document.createElement('div');
        ov.id = 'pfOverlay';
        ov.style.cssText = 'position:fixed;inset:0;background:#000000cc;z-index:9999;display:flex;align-items:flex-start;justify-content:center;padding:24px;overflow-y:auto';
        ov.onclick = e => { if (e.target === ov) closePayoffPanel(); };
        document.body.appendChild(ov);
      }
      ov.style.display = 'flex';
      ov.innerHTML = `<div style="background:#0d1117;border:1px solid #30363d;border-radius:10px;max-width:1000px;width:100%;padding:16px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
          <div><div style="font-size:15px;font-weight:700;color:#e6edf3">📊 ${name} — Payoff &amp; Zone</div>
          <div id="pfSub" style="font-size:11px;color:#8b949e;margin-top:2px">loading…</div></div>
          <button onclick="closePayoffPanel()" style="padding:4px 10px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#8b949e;cursor:pointer">✕ Close</button>
        </div>
        <div id="pfBody" style="color:#8b949e;font-size:12px;padding:20px;text-align:center">⏳ computing…</div>
      </div>`;
      _pfLoad(idsCsv);
    }
    function closePayoffPanel() { const o = document.getElementById('pfOverlay'); if (o) o.style.display = 'none'; }

    async function _pfLoad(idsCsv) {
      const body = document.getElementById('pfBody');
      let d;
      try {
        const r = await fetch('/api/position-payoff?ids=' + encodeURIComponent(idsCsv));
        d = await r.json();
      } catch (e) { body.innerHTML = '<span style="color:#f85149">payoff fetch fail: ' + e.message + '</span>'; return; }
      if (!d || !d.ok) { body.innerHTML = '<span style="color:#f85149">' + ((d && d.msg) || 'payoff nahi bana') + '</span>'; return; }

      document.getElementById('pfSub').textContent =
        `${d.legs.length} legs · spot ${d.spot ? _pfNf(d.spot) : '—'}`
        + (d.expiry ? ` · expiry ${d.expiry} (${d.tte_days}d)` : '')
        + (d.avg_iv ? ` · IV ~${(d.avg_iv * 100).toFixed(1)}%` : '');

      window._pfData = d;
      // Expiry vs exit-day: for a one-night / intraday structure the expiry
      // numbers are theoretical — it closes at today's square-off. Default to
      // whichever the position actually is.
      if (window._pfView == null) window._pfView = d.pop_target != null ? 'target' : 'expiry';
      if (d.pop_target == null) window._pfView = 'expiry';

      body.innerHTML = `
        <div id="pfCards" style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:12px"></div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin:6px 0 4px">
          <span style="font-size:11px;font-weight:600;color:#8b949e">
            Payoff — <span style="color:#3fb950;font-weight:700">━ On Expiry</span>
            ${d.curve_today ? '<span style="color:#58a6ff;font-weight:700;margin-left:8px">━ Today</span>' : ''}
            ${d.curve_target ? '<span style="color:#d29922;font-weight:700;margin-left:8px">╌ Exit day</span>' : ''}
          </span>
          <span id="pfViewTog"></span>
        </div>
        <div style="overflow-x:auto"><svg id="pfChart" viewBox="0 0 940 300" style="width:100%;height:auto"></svg></div>
        <div id="pfHover" style="text-align:center;font-size:11px;color:#8b949e;min-height:15px;margin-top:4px"></div>
        <div id="pfSeriesWrap"></div>`;
      _pfRenderCards();
      _pfDrawPayoff(d);
      _pfLoadMargin(idsCsv);
      _pfLoadSeries(idsCsv);
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

    function _pfDrawPayoff(d) {
      const svg = document.getElementById('pfChart'); if (!svg) return;
      const C = _PF.C, W = 940, H = 300, mL = 70, mR = 14, mT = 20, mB = 40;
      const pw = W - mL - mR, ph = H - mT - mB;
      svg.innerHTML = '';   // redraw on view toggle
      const ce = d.curve_expiry, ct = d.curve_today, cx = d.curve_target;
      const LO = ce[0][0], HI = ce[ce.length - 1][0];
      let ys = ce.map(p => p[1]);
      if (ct) ys = ys.concat(ct.map(p => p[1]));
      if (cx) ys = ys.concat(cx.map(p => p[1]));
      let yT = Math.max(...ys), yB = Math.min(...ys); const pad = (yT - yB) * .12; yT += pad; yB -= pad;
      const X = s => mL + (s - LO) / (HI - LO) * pw, Y = v => mT + (yT - v) / (yT - yB) * ph;
      const y0 = Y(0);
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
      _pfEl(svg, 'path', { d: area(1), fill: C.pos, 'fill-opacity': .15 });
      _pfEl(svg, 'path', { d: area(-1), fill: C.neg, 'fill-opacity': .15 });
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
      // breakevens of the ACTIVE view — an exit-day BE sits somewhere else than
      // the expiry BE (time value still in the legs), so showing expiry's line
      // while the cards read exit-day would be a lie.
      const tgtView = window._pfView === 'target' && cx;
      ((tgtView ? d.breakevens_target : d.breakevens) || []).forEach(b => {
        _pfEl(svg, 'line', { x1: X(b), y1: mT, x2: X(b), y2: H - mB, stroke: C.amb, 'stroke-width': 1.6 });
        _pfEl(svg, 'text', { x: X(b), y: H - mB + 28, 'text-anchor': 'middle', 'font-size': 10, 'font-weight': 700, fill: C.amb }, 'BE ' + _pfNf(b));
      });
      const line = (arr, col, w, dash) => {
        let dd = ''; arr.forEach((p, i) => { dd += (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1) + ' '; });
        const o = { d: dd, fill: 'none', stroke: col, 'stroke-width': w, 'stroke-linejoin': 'round' };
        if (dash) o['stroke-dasharray'] = dash;
        _pfEl(svg, 'path', o);
      };
      if (ct) line(ct, C.blu, 1.9);
      if (cx) line(cx, C.amb, tgtView ? 2.2 : 1.5, '6 3');
      line(ce, C.pos, tgtView ? 1.5 : 2.2);
      if (d.spot) {
        _pfEl(svg, 'line', { x1: X(d.spot), y1: mT, x2: X(d.spot), y2: H - mB, stroke: C.txt, 'stroke-width': 1.4, 'stroke-opacity': .6 });
        _pfEl(svg, 'text', { x: X(d.spot), y: mT - 5, 'text-anchor': 'middle', 'font-size': 9.5, 'font-weight': 700, fill: C.txt }, 'Spot ' + _pfNf(d.spot));
      }
      const hl = _pfEl(svg, 'line', { x1: 0, y1: mT, x2: 0, y2: H - mB, stroke: C.txt, 'stroke-width': 1, 'stroke-opacity': .3, visibility: 'hidden' });
      const hb = document.getElementById('pfHover');
      const at = (arr, s) => { const i = Math.round((s - LO) / (HI - LO) * (arr.length - 1)); return arr[Math.max(0, Math.min(arr.length - 1, i))][1]; };
      svg.addEventListener('mousemove', e => {
        const r = svg.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * W;
        const s = Math.max(LO, Math.min(HI, LO + (px - mL) / pw * (HI - LO)));
        hl.setAttribute('x1', X(s)); hl.setAttribute('x2', X(s)); hl.setAttribute('visibility', 'visible');
        const ve = at(ce, s);
        hb.innerHTML = `NIFTY <b>${_pfNf(s)}</b> → expiry <b style="color:${ve >= 0 ? C.pos : C.neg}">${ve >= 0 ? '+' : ''}${_pfRs(ve)}</b>`
          + (ct ? ` · aaj <b style="color:${C.blu}">${(at(ct, s) >= 0 ? '+' : '') + _pfRs(at(ct, s))}</b>` : '')
          + (cx ? ` · exit <b style="color:${C.amb}">${(at(cx, s) >= 0 ? '+' : '') + _pfRs(at(cx, s))}</b>` : '');
      });
      svg.addEventListener('mouseleave', () => { hl.setAttribute('visibility', 'hidden'); hb.textContent = ''; });
    }

    async function _pfLoadMargin(idsCsv) {
      try {
        const rr = await fetch('/api/position-payoff-margin?ids=' + encodeURIComponent(idsCsv));
        window._pfMargin = await rr.json();
      } catch (e) { window._pfMargin = { ok: false, msg: 'calc fail' }; }
      _pfPaintMargin(window._pfMargin);
    }

    async function _pfLoadSeries(idsCsv) {
      const wrap = document.getElementById('pfSeriesWrap');
      wrap.innerHTML = '<div style="font-size:11px;color:#8b949e;padding:10px;text-align:center">⏳ legs ke candles aa rahe…</div>';
      let s;
      try {
        const r = await fetch('/api/position-legs-series?ids=' + encodeURIComponent(idsCsv));
        s = await r.json();
      } catch (e) { wrap.innerHTML = '<div style="color:#f85149;font-size:11px;padding:8px">series fail: ' + e.message + '</div>'; return; }
      if (!s || !s.ok) { wrap.innerHTML = '<div style="color:#f85149;font-size:11px;padding:8px">' + ((s && s.msg) || 'series nahi mili') + '</div>'; return; }
      const n = s.legs.length, cols = n <= 2 ? n : (n <= 4 ? 2 : 3);
      wrap.innerHTML = `
        <div style="font-size:11px;font-weight:600;color:#8b949e;margin:16px 0 4px;text-align:left">
          Combined Premium — net structure P&amp;L <span style="color:#6e7681;font-weight:400">(entry ${s.from} → ${s.to}, real 1-min)</span>
        </div>
        <div style="overflow-x:auto"><svg id="pfComb" viewBox="0 0 940 190" style="width:100%;height:auto"></svg></div>
        <div id="pfCombHover" style="text-align:center;font-size:11px;color:#8b949e;min-height:15px;margin-top:4px"></div>
        <div style="font-size:11px;font-weight:600;color:#8b949e;margin:16px 0 6px;text-align:left">
          Legs — standalone (${n}-view) <span style="color:#6e7681;font-weight:400">· har leg ka apna premium · dashed = entry · colour = favour me hai ya nahi</span>
        </div>
        <div id="pfGrid" style="display:grid;grid-template-columns:repeat(${cols},1fr);gap:10px"></div>`;
      _pfDrawCombined(s);
      _pfDrawGrid(s);
    }

    function _pfDrawCombined(s) {
      const svg = document.getElementById('pfComb'); if (!svg || !s.combined.length) return;
      const C = _PF.C, W = 940, H = 190, mL = 70, mR = 14, mT = 12, mB = 26;
      const pw = W - mL - mR, ph = H - mT - mB;
      const D = s.combined, qty = s.legs[0] ? s.legs[0].qty : 1;
      const vals = D.map(p => p[1] * qty);
      let yT = Math.max(...vals), yB = Math.min(...vals); const pad = (yT - yB) * .15 || 100; yT += pad; yB -= pad;
      const X = i => mL + i / Math.max(D.length - 1, 1) * pw, Y = v => mT + (yT - v) / (yT - yB) * ph;
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
      const hl = _pfEl(svg, 'line', { x1: 0, y1: mT, x2: 0, y2: H - mB, stroke: C.txt, 'stroke-width': 1, 'stroke-opacity': .3, visibility: 'hidden' });
      const hb = document.getElementById('pfCombHover');
      svg.addEventListener('mousemove', e => {
        const r = svg.getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * W;
        let i = Math.round((px - mL) / pw * (D.length - 1)); i = Math.max(0, Math.min(D.length - 1, i));
        hl.setAttribute('x1', X(i)); hl.setAttribute('x2', X(i)); hl.setAttribute('visibility', 'visible');
        const v = D[i][1] * qty;
        hb.innerHTML = `<b>${_pfIST(D[i][0])}</b> · net P&L <b style="color:${v >= 0 ? C.pos : C.neg}">${v >= 0 ? '+' : ''}${_pfRs(v)}</b> (${D[i][1].toFixed(2)}/unit)`;
      });
      svg.addEventListener('mouseleave', () => { hl.setAttribute('visibility', 'hidden'); hb.textContent = ''; });
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
          case 'tags':
            val = _ordTags(t);
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
          case 'exit_reason':
            val = _exitReasonBadge(t.exit_reason);
            break;
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
