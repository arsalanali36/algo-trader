// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // ── PINE VERSION MANAGER ──────────────────────────────────────
    async function pineLoadLatest() {
      const r = await fetch('/api/pine/latest');
      const d = await r.json();
      const badge = document.getElementById('pine-latest-badge');
      if (d.version === 0) {
        badge.innerHTML = 'Koi version nahi — pehli script paste karo';
      } else {
        badge.innerHTML = `<span style="color:#3fb950">v${d.version}</span> &nbsp;|&nbsp; <span style="color:#e6edf3">${d.name}</span> &nbsp;|&nbsp; <span style="color:#8b949e">${d.timestamp}</span>`;
      }
    }

    let _pineData = [];
    let _pineSortKey = 'version';
    let _pineSortAsc = false;

    // ── Pine inner sub-tabs ───────────────────────────────────────────────────────
    // ── Pine inner sub-tabs ───────────────────────────────────────────────────────
    function pineSwitchSub(tab) {
      document.getElementById('pine-panel-editor').style.display = tab === 'editor' ? '' : 'none';
      document.getElementById('pine-panel-history').style.display = tab === 'history' ? '' : 'none';
      ['editor', 'history'].forEach(t => {
        const el = document.getElementById('pine-sub-' + t);
        el.style.borderBottomColor = tab === t ? '#1f6feb' : 'transparent';
        el.style.color = tab === t ? '#e6edf3' : '#8b949e';
      });
      if (tab === 'history') pineLoadHistory();
    }

    // ── Run Modal ─────────────────────────────────────────────────────────────────
    let _runMode = 'paper';
    let _runStrategyId = '';
    const _RUN_PARAM_LABELS = {
      rsi_period: 'RSI Period', overbought: 'Overbought (OB)',
      oversold: 'Oversold (OS)', rsi_exit: 'Exit at RSI',
      qty: 'Qty (lots)', timeframe: 'Timeframe',
      max_trades_per_symbol: 'Max Trades', instrument: 'Instrument'
    };
    const _RUN_PARAM_DEFAULTS = {
      rsi_period: 14, overbought: 70, oversold: 30, rsi_exit: 50,
      qty: 1, timeframe: '5m', max_trades_per_symbol: 2, instrument: 'options'
    };

    // VWAP-EMA Failure's saved variants are equity-only and need a richer field
    // set (symbol choice, R-multiples, partial exits) than the generic RSI-shaped
    // _RUN_PARAM_LABELS/_RUN_PARAM_DEFAULTS this modal was originally built for —
    // those were never updated for any strategy besides RSI, so e.g. range/ema's
    // Run modal still mislabels its own fields too (pre-existing, left as-is here
    // since it's not what broke). vwap gets its own explicit field list instead.
    const _VWAP_FIELDS = [
      { id: 'symbol', label: 'Symbol', type: 'select', opts: ['TCS', 'POLYCAB', 'RELIANCE'] },
      { id: 'instrument', label: 'Instrument', type: 'select', opts: ['equity', 'options'], default: 'equity' },
      { id: 'timeframe', label: 'Timeframe', type: 'select', opts: ['1m', '3m', '5m', '15m', '30m'], default: '5m' },
      { id: 'qty', label: 'Quantity', type: 'number', default: 1 },
      { id: 'ema_len', label: 'EMA Length', type: 'number', default: 10 },
      { id: 'sl_buffer_points', label: 'SL Buffer (pts)', type: 'number', default: 2 },
      { id: 'r1', label: 'Target 1 (R)', type: 'number', default: 1 },
      { id: 'r2', label: 'Target 2 (R)', type: 'number', default: 2 },
      { id: 'r3', label: 'Target 3 (R)', type: 'number', default: 3 },
      { id: 'max_sl_percent', label: 'Max SL %', type: 'number', default: 2.5 },
      { id: 'partial1_pct', label: 'Qty% Exit @ T1', type: 'number', default: 40 },
      { id: 'partial2_pct', label: 'Qty% Exit @ T2', type: 'number', default: 30 },
      { id: 'use_partial_exits', label: 'Use Partial Exits', type: 'checkbox', default: true },
      { id: 'use_daily_trend_filter', label: 'Daily Trend Filter', type: 'checkbox', default: true },
      { id: 'use_session_filter', label: 'Session Filter', type: 'checkbox', default: true }
    ];

    function _runModalFieldHtml(f, val) {
      if (val === undefined) val = f.default;
      if (f.type === 'select') {
        return `<div><label style="font-size:11px;color:#8b949e;display:block;margin-bottom:3px">${f.label}</label>
      <select id="rmp-${f.id}" style="width:100%;background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:5px;padding:5px 8px;font-size:12px;box-sizing:border-box">
        ${f.opts.map(o => `<option value="${o}" ${String(val) === String(o) ? 'selected' : ''}>${o}</option>`).join('')}
      </select></div>`;
      }
      if (f.type === 'checkbox') {
        return `<div style="display:flex;align-items:end"><label style="font-size:12px;color:#e6edf3;display:flex;align-items:center;gap:6px"><input type="checkbox" id="rmp-${f.id}" ${val ? 'checked' : ''}> ${f.label}</label></div>`;
      }
      return `<div><label style="font-size:11px;color:#8b949e;display:block;margin-bottom:3px">${f.label}</label>
    <input id="rmp-${f.id}" type="number" value="${val ?? ''}" style="width:100%;background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:5px;padding:5px 8px;font-size:12px;box-sizing:border-box"></div>`;
    }

    // vwap_ema_failure.py is shared by 3 saved configs (vwap_v1/v2/v3 — one per
    // symbol). The old "Variant" dropdown just listed those 3 raw config keys,
    // which wasn't useful for actually deciding what to deploy — instead, show
    // this strategy's saved Backtest Results here so the user can pick a result
    // (by symbol/PF/win-rate) and deploy straight from it. Clicking a result's
    // "🚀 Use This" button loads its symbol+cfg into the variant whose saved
    // `symbol` matches (falling back to vwap_v1 if none match yet).
    async function _runModalRenderVwap(allCfg, preferId) {
      const variantIds = Object.keys(allCfg).filter(k => k.startsWith('vwap_v'));
      if (!variantIds.length) variantIds.push('vwap_v1');
      _runStrategyId = variantIds.includes(preferId) ? preferId : variantIds[0];

      const sc = allCfg[_runStrategyId] || {};
      const fieldsHtml = _VWAP_FIELDS.map(f => _runModalFieldHtml(f, sc[f.id])).join('');
      document.getElementById('run-modal-params').innerHTML =
        `<div id="run-modal-backtest-results" style="margin-bottom:12px"></div>
     <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">${fieldsHtml}</div>`;

      const stR = await fetch('/api/run-status');
      const st = await stR.json();
      _runModalUpdateStatus(st[_runStrategyId] || false);
      _runModalLoadBacktestResults(allCfg);
    }

    async function _runModalLoadBacktestResults(allCfg) {
      const wrap = document.getElementById('run-modal-backtest-results');
      if (!wrap) return;
      wrap.innerHTML = '<div style="font-size:11px;color:#8b949e">Loading backtest results...</div>';
      try {
        const r = await fetch('/api/backtest/saved');
        const all = await r.json();
        const results = (Array.isArray(all) ? all : []).filter(e => (e.strategy || '').startsWith('vwap'));
        if (!results.length) {
          wrap.innerHTML = '<div style="font-size:11px;color:#8b949e">No saved backtest results yet for this strategy — run one from 📊 Results.</div>';
          return;
        }
        wrap.innerHTML = `<label style="font-size:11px;color:#8b949e;display:block;margin-bottom:5px">📊 Backtest Results — pick one to deploy</label>
      <div style="display:flex;flex-direction:column;gap:5px;max-height:140px;overflow-y:auto">
        ${results.map(e => {
          const sym = (e.cfg || {}).symbol || (e.symbols || [])[0] || '?';
          const pf = e.summary && e.summary.profit_factor != null ? e.summary.profit_factor : '—';
          const wr = e.summary && e.summary.win_rate != null ? e.summary.win_rate + '%' : '—';
          const pnl = e.summary && e.summary.pnl_points != null ? e.summary.pnl_points : '—';
          return `<div style="display:flex;align-items:center;justify-content:space-between;background:#161b22;border:1px solid #30363d;border-radius:5px;padding:6px 8px;font-size:11px">
            <span style="color:#e6edf3">${e.name} <span style="color:#8b949e">(${sym})</span></span>
            <span style="color:#8b949e">PF ${pf} · Win ${wr} · PnL ${pnl}</span>
            <button type="button" class="btn btn-gray" style="padding:3px 8px;font-size:10px" onclick='_runModalUseBacktestResult(${JSON.stringify(e).replace(/'/g, "&apos;")})'>🚀 Use This</button>
          </div>`;
        }).join('')}
      </div>`;
      } catch (e) {
        wrap.innerHTML = '<div style="font-size:11px;color:#f85149">Failed to load backtest results</div>';
      }
    }

    async function _runModalUseBacktestResult(entry) {
      const r = await fetch('/api/config');
      const allCfg = await r.json();
      const variantIds = Object.keys(allCfg).filter(k => k.startsWith('vwap_v'));
      const sym = (entry.cfg || {}).symbol || (entry.symbols || [])[0];
      const targetId = variantIds.find(id => (allCfg[id] || {}).symbol === sym) || entry.strategy || variantIds[0] || 'vwap_v1';
      allCfg[targetId] = { ...(allCfg[targetId] || {}), ...(entry.cfg || {}) };
      await _runModalRenderVwap(allCfg, targetId);
      const msg = document.getElementById('run-modal-msg');
      if (msg) { msg.textContent = `✅ Loaded "${entry.name}" into ${targetId}`; setTimeout(() => { msg.textContent = ''; }, 3000); }
    }

    async function runModalOpen(stratId, versionName, timestamp, pyFile) {
      stratId = stratId || versionName.toLowerCase().replace(/[^a-z0-9]/g, '_');
      _runStrategyId = stratId;
      document.getElementById('run-modal-title').textContent = stratId;
      document.getElementById('run-modal-sub').textContent = versionName + (timestamp ? '  ·  ' + timestamp : '');
      document.getElementById('run-modal-msg').textContent = '';
      document.getElementById('run-modal-cfg-msg').textContent = '';

      // load config + status in parallel
      const [cfgR, stR] = await Promise.all([fetch('/api/config'), fetch('/api/run-status')]);
      const cfg = await cfgR.json();
      const st = await stR.json();

      runModalMode('paper');

      if (stratId.startsWith('vwap')) {
        await _runModalRenderVwap(cfg, stratId);
      } else {
        _runModalUpdateStatus(st[stratId] || false);
        const sc = cfg[stratId] || {};
        const params = { ..._RUN_PARAM_DEFAULTS, ...sc };
        document.getElementById('run-modal-params').innerHTML = Object.entries(params).map(([k, v]) => {
          const label = _RUN_PARAM_LABELS[k] || k;
          return `<div>
        <label style="font-size:11px;color:#8b949e;display:block;margin-bottom:3px">${label}</label>
        <input id="rmp-${k}" value="${v}" style="width:100%;background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:5px;padding:5px 8px;font-size:12px;box-sizing:border-box">
      </div>`;
        }).join('');
      }

      const ov = document.getElementById('run-modal-overlay');
      ov.style.display = 'flex';
    }

    function runModalClose() {
      document.getElementById('run-modal-overlay').style.display = 'none';
    }

    function runModalMode(m) {
      _runMode = m;
      document.getElementById('run-modal-paper').style.background = m === 'paper' ? '#d29922' : 'transparent';
      document.getElementById('run-modal-paper').style.color = m === 'paper' ? '#0d1117' : '#8b949e';
      document.getElementById('run-modal-live').style.background = m === 'live' ? '#f85149' : 'transparent';
      document.getElementById('run-modal-live').style.color = m === 'live' ? '#fff' : '#8b949e';
    }

    let _runModalRunning = false;

    function _runModalUpdateStatus(running) {
      _runModalRunning = running;
      const p = document.getElementById('run-modal-status');
      p.textContent = running ? '● Running' : '● Stopped';
      p.style.background = running ? '#23863633' : '#30363d';
      p.style.color = running ? '#3fb950' : '#8b949e';

      const btn = document.getElementById('run-modal-toggle');
      if (running) {
        btn.textContent = '⏹ Stop';
        btn.style.background = '#da3633';
      } else {
        btn.textContent = '▶ Start';
        btn.style.background = '#238636';
      }
    }

    async function runModalToggle() {
      const msg = document.getElementById('run-modal-msg');
      if (_runModalRunning) {
        const r = await fetch(`/api/stop?s=${_runStrategyId}`, { method: 'POST' });
        const d = await r.json();
        msg.textContent = d.msg;
        _runModalUpdateStatus(false);
      } else {
        const r = await fetch(`/api/start?s=${_runStrategyId}&mode=${_runMode}`, { method: 'POST' });
        const d = await r.json();
        msg.textContent = d.msg;
        _runModalUpdateStatus(true);
      }
      setTimeout(() => { msg.textContent = ''; }, 4000);
    }

    // Backtest now lives only on the dedicated Results page (📊 Results button,
    // /backtest-chart) — this Run modal's old embedded mini-backtest panel
    // always ran against NIFTY regardless of which symbol/variant was selected
    // here (it hardcoded the index data path), so it's removed rather than fixed.

    async function runModalSaveConfig() {
      const inputs = document.querySelectorAll('[id^="rmp-"]');
      const patch = {};
      inputs.forEach(inp => {
        if (inp.id === 'rmp-variant-picker') return;   // selector itself, not a config field
        const k = inp.id.replace('rmp-', '');
        if (inp.type === 'checkbox') { patch[k] = inp.checked; return; }
        const v = inp.value;
        patch[k] = (v !== '' && !isNaN(v)) ? Number(v) : v;
      });
      const r = await fetch('/api/config');
      const cfg = await r.json();
      cfg[_runStrategyId] = { ...(cfg[_runStrategyId] || {}), ...patch };
      await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
      const msg = document.getElementById('run-modal-cfg-msg');
      msg.textContent = '✅ Saved';
      setTimeout(() => { msg.textContent = ''; }, 3000);
    }

    async function pineLoadHistory() {
      const r = await fetch('/api/pine/history');
      _pineData = await r.json();
      pineRenderTable();
    }

    function pineSortBy(key) {
      if (_pineSortKey === key) _pineSortAsc = !_pineSortAsc;
      else { _pineSortKey = key; _pineSortAsc = false; }
      pineRenderTable();
    }

    function pineRenderTable() {
      const el = document.getElementById('pine-history');
      if (!_pineData.length) { el.innerHTML = '<tr><td colspan="7" style="padding:16px;color:#8b949e;text-align:center">Abhi koi version saved nahi hai</td></tr>'; return; }

      // update sort indicators
      ['version', 'name', 'timestamp', 'accuracy'].forEach(k => {
        const el = document.getElementById('sort-ind-' + k);
        if (el) el.textContent = _pineSortKey === k ? (_pineSortAsc ? '↑' : '↓') : '';
      });

      const sorted = [..._pineData].sort((a, b) => {
        let av = a[_pineSortKey] ?? '', bv = b[_pineSortKey] ?? '';
        if (_pineSortKey === 'accuracy') { av = av || -1; bv = bv || -1; }
        const res = av < bv ? -1 : av > bv ? 1 : 0;
        return _pineSortAsc ? res : -res;
      });

      // group by strategy name (preserve sort order within groups if sorting by name/version)
      const groups = [];
      const seen = {};
      sorted.forEach(v => {
        if (!seen[v.name]) { seen[v.name] = []; groups.push({ name: v.name, items: seen[v.name] }); }
        seen[v.name].push(v);
      });

      el.innerHTML = groups.map(g => {
        const headerRow = `<tr><td colspan="10" style="padding:10px 10px 4px;color:#8b949e;font-size:11px;font-weight:600;letter-spacing:0.5px;border-bottom:1px solid #30363d;border-top:2px solid #30363d;background:#161b2288">${g.name}</td></tr>`;
        const rows = g.items.map(v => {
          const acc = v.accuracy != null ? v.accuracy : null;
          const accColor = acc >= 85 ? '#3fb950' : acc >= 60 ? '#d29922' : acc != null ? '#f85149' : '#8b949e';
          const accCell = acc != null ? `<span style="color:${accColor};font-weight:700">${acc}%</span>` : `<span style="color:#8b949e">—</span>`;
          const author = v.author || 'Arsalan';
          const isClaud = author === 'Claude';
          const authorBadge = `<span style="background:${isClaud ? '#6e40c922' : '#d2992222'};color:${isClaud ? '#a371f7' : '#d29922'};font-size:10px;padding:2px 8px;border-radius:10px;font-weight:600;white-space:nowrap">${isClaud ? '🤖 Claude' : '👤 Arsalan'}</span>`;
          const pyFile = v.py_file && v.py_file !== 'none' ? v.py_file : null;
          const pyBadge = pyFile ? `<span style="background:#1f6feb22;color:#1f6feb;font-size:10px;padding:1px 6px;border-radius:3px;font-family:monospace">${pyFile}</span>` : `<span style="background:#30363d33;color:#8b949e;font-size:10px;padding:1px 6px;border-radius:3px">not converted</span>`;
          const reportCell = v.report_stats
            ? `<a href="/pine/report/${v.version}" target="_blank" style="color:#58a6ff;font-size:12px;text-decoration:none;white-space:nowrap">📊 Report<br><span style="font-size:10px;color:#8b949e">${v.report_stats.tv_trades}TV / ${v.report_stats.eng_trades}Eng</span></a>`
            : `<span style="color:#30363d;font-size:12px">—</span>`;
          const sv = v.strat_version || v.version;
          const scriptLang = v.lang || (pyFile ? 'python' : 'pine');
          const _lm = { pine: ['📌 Pine', '#d2992222', '#d29922'], python: ['🐍 Py', '#1f6feb22', '#58a6ff'], dsl: ['⚙️ DSL', '#3fb95022', '#3fb950'] }[scriptLang] || ['?', '#30363d33', '#8b949e'];
          const langChip = `<span style="background:${_lm[1]};color:${_lm[2]};font-size:9px;padding:1px 5px;border-radius:8px;font-weight:600;white-space:nowrap">${_lm[0]}</span>`;
          const runnable = !!v.script_id;
          return `<tr style="border-bottom:1px solid #21262d" onmouseover="this.style.background='#161b22'" onmouseout="this.style.background='transparent'">
      <td style="padding:10px;color:#3fb950;font-weight:700;white-space:nowrap;cursor:pointer" onclick="pineLoadVersion(${v.version})" title="Click karke code textarea mein load karo">v${sv}<br>${langChip}</td>
      <td style="padding:10px">${authorBadge}</td>
      <td style="padding:10px">${pyBadge}</td>
      <td style="padding:10px;color:#8b949e;font-size:12px;white-space:nowrap">${v.timestamp}</td>
      <td style="padding:10px;min-width:200px">
        <div id="pine-view-${v.version}" style="display:flex;align-items:flex-start;gap:6px;cursor:default">
          <span style="color:${v.desc ? '#e6edf3' : '#8b949e'};font-size:12px;flex:1;line-height:1.5">${v.desc || 'Add notes...'}</span>
          <span onclick="pineEditStart(${v.version})" title="Edit" style="color:#8b949e;font-size:13px;cursor:pointer;flex-shrink:0;padding:1px 3px;border-radius:3px" onmouseover="this.style.color='#e6edf3'" onmouseout="this.style.color='#8b949e'">✏️</span>
        </div>
        <div id="pine-edit-${v.version}" style="display:none">
          <textarea id="pine-desc-${v.version}" style="width:100%;background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:4px;padding:5px 7px;font-size:12px;resize:vertical;min-height:50px;font-family:inherit;box-sizing:border-box">${v.desc || ''}</textarea>
          <div style="display:flex;gap:6px;margin-top:4px">
            <button onclick="pineDescSave(${v.version})" style="background:#1f6feb33;border:1px solid #1f6feb55;color:#58a6ff;border-radius:4px;padding:3px 10px;font-size:11px;cursor:pointer">💾 Save</button>
            <button onclick="pineEditCancel(${v.version})" style="background:#21262d;border:1px solid #30363d;color:#8b949e;border-radius:4px;padding:3px 10px;font-size:11px;cursor:pointer">✕</button>
          </div>
        </div>
      </td>
      <td style="padding:10px;text-align:center">${accCell}</td>
      <td style="padding:10px;text-align:center">${reportCell}</td>
      <td style="padding:10px;text-align:center" id="pine-imgcell-${v.version}">
        <button onclick="pineOpenImages(${v.version})" style="background:#21262d;border:1px solid #30363d;color:#8b949e;border-radius:4px;padding:4px 8px;font-size:12px;cursor:pointer" onmouseover="this.style.color='#e6edf3';this.style.borderColor='#58a6ff'" onmouseout="this.style.color='#8b949e';this.style.borderColor='#30363d'" title="Screenshots dekho / add karo">🖼 <span id="pine-imgcount-${v.version}">…</span></button>
      </td>
      <td style="padding:10px;text-align:center">
        ${runnable
              ? `<button onclick="window.open('/backtest-chart?strategy=${v.script_id}','_blank')"
               title="Backtest dropdown me '${v.script_id}' khulega — dates pick karke run karo"
               style="background:#1f6feb;border:1px solid #388bfd;color:#fff;border-radius:4px;padding:4px 12px;font-size:12px;cursor:pointer;font-weight:600;white-space:nowrap"
               onmouseover="this.style.background='#388bfd'" onmouseout="this.style.background='#1f6feb'">📊 Backtest</button>`
              : `<button onclick="runModalOpen('${pyFile ? pyFile.replace('strategies/', '').replace('.py', '') : ''}','${v.name.replace(/'/g, "\\'")} v${sv}','${v.timestamp}','${pyFile || ''}')"
               style="background:#238636;border:1px solid #2ea043;color:#fff;border-radius:4px;padding:4px 12px;font-size:12px;cursor:pointer;font-weight:600;white-space:nowrap"
               onmouseover="this.style.background='#2ea043'" onmouseout="this.style.background='#238636'">▶ Run</button>`}
      </td>
      <td style="padding:10px;text-align:center;white-space:nowrap">
        <button onclick="pineCopyCode(${v.version})" title="Code clipboard mein copy karo" style="background:#21262d;border:1px solid #30363d;color:#8b949e;border-radius:4px;padding:4px 8px;font-size:12px;cursor:pointer" onmouseover="this.style.color='#e6edf3';this.style.borderColor='#58a6ff'" onmouseout="this.style.color='#8b949e';this.style.borderColor='#30363d'">📋 Copy</button>
        <button onclick="pineDelete(${v.version})" title="Is version ko delete karo" style="background:#21262d;border:1px solid #30363d;color:#8b949e;border-radius:4px;padding:4px 8px;font-size:12px;cursor:pointer;margin-left:4px" onmouseover="this.style.color='#f85149';this.style.borderColor='#f85149'" onmouseout="this.style.color='#8b949e';this.style.borderColor='#30363d'">🗑</button>
      </td>
    </tr>`;
        }).join('');
        return headerRow + rows;
      }).join('');
      // load image counts for all versions after render
      _pineData.forEach(v => _pineLoadImgCount(v.version));
    }

    async function _pineLoadImgCount(version) {
      const el = document.getElementById('pine-imgcount-' + version);
      if (!el) return;
      try {
        const r = await fetch('/api/pine/images/' + version);
        const imgs = await r.json();
        el.textContent = imgs.length ? imgs.length : '0';
        el.style.color = imgs.length ? '#58a6ff' : '#484f58';
      } catch (e) { el.textContent = '0'; }
    }

    // ── Script language: detection + manual pills ────────────────────────────────
    let _scriptLang = null;       // null = user hasn't overridden; follow auto-detect
    let _scriptLangAuto = 'pine'; // last auto-detected value

    function scriptDetectLang(code) {
      const c = (code || ''), low = c.toLowerCase();
      if (low.includes('//@version') || low.includes('strategy(') || low.includes('indicator(') || low.includes('ta.')) return 'pine';
      if (low.includes('def evaluate') || low.includes('def backtest') || /\n\s*import\s/.test(c) || c.startsWith('import ') || /\n\s*def\s/.test(c) || low.includes('class ')) return 'python';
      if (low.includes('entry_long') || low.includes('exit_long') || low.includes('entry_short')) return 'dsl';
      return 'pine';
    }
    function _scriptCurrentLang() { return _scriptLang || _scriptLangAuto; }
    function _scriptPaintPills() {
      const cur = _scriptCurrentLang();
      document.querySelectorAll('.script-lang-pill').forEach(b => {
        const on = b.dataset.lang === cur;
        b.style.background = on ? '#1f6feb22' : '#161b22';
        b.style.borderColor = on ? '#1f6feb' : '#30363d';
        b.style.color = on ? '#58a6ff' : '#8b949e';
      });
      const note = document.getElementById('script-lang-auto');
      if (note) note.textContent = _scriptLang ? '(manually set)' : '(auto-detected — change if wrong)';
    }
    function scriptSetLang(lang) { _scriptLang = lang; _scriptPaintPills(); }
    function scriptAutoDetect() {
      _scriptLangAuto = scriptDetectLang(document.getElementById('pine-code').value);
      _scriptPaintPills();
    }
    function scriptUploadFile(files) {
      if (!files || !files.length) return;
      const f = files[0];
      const rd = new FileReader();
      rd.onload = e => {
        document.getElementById('pine-code').value = e.target.result || '';
        const ext = (f.name.split('.').pop() || '').toLowerCase();
        _scriptLang = ext === 'py' ? 'python' : ext === 'pine' ? 'pine' : ext === 'rules' ? 'dsl' : null;
        scriptAutoDetect();
        if (!document.getElementById('script-name').value.trim())
          document.getElementById('script-name').value = f.name.replace(/\.[^.]+$/, '');
      };
      rd.readAsText(f);
    }

