// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // ── ORDER SOUND NOTIFICATION ──
    const _lastOrderLine = {};  // key → last seen order line to avoid duplicate beeps
    function _playOrderSound(isWin) {
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const beep = (freq, start, dur, vol = 0.4) => {
          const o = ctx.createOscillator();
          const g = ctx.createGain();
          o.connect(g); g.connect(ctx.destination);
          o.type = 'sine';
          o.frequency.value = freq;
          g.gain.setValueAtTime(vol, ctx.currentTime + start);
          g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + start + dur);
          o.start(ctx.currentTime + start);
          o.stop(ctx.currentTime + start + dur + 0.05);
        };
        if (isWin) {
          beep(880, 0, 0.12);
          beep(1100, 0.13, 0.18);
        } else {
          beep(440, 0, 0.15);
          beep(330, 0.16, 0.2);
        }
      } catch (e) { }
    }

    // LOG TAB
    const _logPaused = {};

    // Status dot for a strategy in the Logs sidebar (replaces the old file-type
    // icon): red = live, blue = paper, grey = not active (stopped). Reads the
    // SAME RUNNING_PIDS (/api/status) state the Paper/Live/Stop badge uses.
    function _logStatusDot(key) {
      const pid = RUNNING_PIDS[key] || RUNNING_PIDS[key.toLowerCase()];
      const mode = RUNNING_PIDS[key + '_mode'] || RUNNING_PIDS[key.toLowerCase() + '_mode'] || 'paper';
      if (!pid) return { color: '#8b949e', title: 'Not active' };
      if (mode === 'live') return { color: '#f85149', title: 'Live' };
      return { color: '#58a6ff', title: 'Paper' };
    }

    // Refresh every sidebar dot in place (called on the 5s status poll via
    // renderLogRunControls, so the dot tracks live status without a full rebuild).
    function updateLogStatusDots() {
      Object.keys(GLOBAL_CONFIG).forEach(key => {
        if (key === '_risk' || key === 'webhooks' || key === '_ui_config') return;
        const dot = document.getElementById(`${key}-log-statusdot`);
        if (!dot) return;
        const sd = _logStatusDot(key);
        dot.style.background = sd.color;
        dot.title = sd.title;
      });
    }

    // Shared strategy display maps — used by BOTH the Logs sidebar (renderLogTab)
    // and the Risk > Per-Strategy Override table so they stay in sync (Rule 6B).
    //
    // 2026-07-17: `regLabel`/`regId`/`regHidden` + the registry fetch ab
    // `static/js/registry.js` me hain (har page pe load hoti hai, aur Python ke
    // `strategy_registry.resolve()` ki tarah id+config_key+slug+aliases CHARON pe
    // match karti hai). Yahan sirf usi data ke do VIEW bachte hain jo purane
    // callers maangte hain. Do cheezein jaan-boojh ke GAYI:
    //   - hardcoded seed maps: unme RETIRE ho chuke naam the ("Range Breakout",
    //     "Ars Chain (live)") — registry fetch ek baar fail hoti to app purana
    //     naam confidently dikha deti. Ab load na ho to raw key dikhega.
    //   - duplicate fetch + duplicate lookup: dusra resolver = dusra sach.
    // Ye maps ab HAR alias pe indexed hain (sirf config_key pe nahi) — isliye
    // `MISSION_NAME[alias]` bhi wahi jawab deta hai jo regLabel(alias).
    let MISSION_NUM = {};
    let MISSION_NAME = {};
    let STRAT_GROUPS = [];

    function _rebuildStratMaps(reg){
      const fams = reg.families || {}, strs = reg.strategies || {};
      const num = {}, name = {}, byFam = {};
      for (const id in strs) {
        const s = strs[id] || {};
        const aliases = [id, s.config_key, s.slug].concat(s.aliases || []).filter(Boolean);
        aliases.forEach(a => { const k = String(a).toLowerCase(); num[k] = id; name[k] = s.name || id; });
        const fid = id.split('.')[0];
        // group key = config_key (ye keys nifty_config ki keys se match hoti hain);
        // jiska config_key nahi (research-only entries) wo kisi tab me nahi aati.
        if (s.config_key) (byFam[fid] = byFam[fid] || []).push({ ck: String(s.config_key).toLowerCase(), id });
      }
      MISSION_NUM = num; MISSION_NAME = name;
      STRAT_GROUPS = Object.keys(fams).sort().map(fid => ({
        title: (fams[fid] && fams[fid].name) ? (fid + ' · ' + fams[fid].name) : fid,
        keys: (byFam[fid] || []).sort((a, b) => a.id < b.id ? -1 : 1).map(x => x.ck).filter(ck => !regHidden(ck))
      })).filter(g => g.keys.length);
    }

    // registry.js fetch khud kar chuki hoti hai — aa jaane pe maps bhar do + jo
    // tab khula hai use dobara render kar do (pehli paint raw keys pe hui hogi).
    regOnLoad(reg => {
      _rebuildStratMaps(reg);
      try{ if(typeof activeTab!=='undefined'){
        if(activeTab==='log' && document.getElementById('log-folder-card')) renderLogTab();
        else if(activeTab==='risk' && document.getElementById('risk-strategy-table')) renderRiskTab();
        else if(activeTab==='orders' && typeof renderCachedOrders==='function') renderCachedOrders();
      } }catch(e){}
    });

    function renderLogTab() {
      const container = document.getElementById('log-folder-card');
      if (!container) return;
      renderRateLimitEvents();   // Rate Limit Room lives in this tab now — first paint without waiting for the 5s interval

      let keys = Object.keys(GLOBAL_CONFIG).filter(key => key !== '_risk' && key !== 'webhooks' && key !== '_ui_config' && !regHidden(key));
      if (keys.length === 0) {
        container.innerHTML = '<div style="padding:20px;color:#8b949e">No active strategies found.</div>';
        return;
      }

      // --- logical ordering + grouping (was raw config-key / alphabetical order) ---
      // A = option-strategy mission (numbered 01/02/03), B = other live traders + their
      // paper shadows, C = webhook/infra. Unknown keys fall into a trailing "Other" group.
      // shared maps (defined once above — Rule 6B, same data drives the Risk table)
      const _LOG_GROUPS = STRAT_GROUPS;
      const _LOG_NUM = MISSION_NUM;
      const _LOG_NAME = MISSION_NAME;
      const _logName = k => regLabel(k);   // ek hi labeller poori app me (registry.js)
      const _lcKey = k => String(k).toLowerCase();
      const _usedKeys = new Set();
      const _logGroups = [];
      _LOG_GROUPS.forEach(g => {
        const present = keys.filter(k => g.keys.includes(_lcKey(k)))
                            .sort((a, b) => g.keys.indexOf(_lcKey(a)) - g.keys.indexOf(_lcKey(b)));
        present.forEach(k => _usedKeys.add(k));
        if (present.length) _logGroups.push({ title: g.title, keys: present });
      });
      const _leftoverKeys = keys.filter(k => !_usedKeys.has(k)).sort();
      if (_leftoverKeys.length) _logGroups.push({ title: 'Other', keys: _leftoverKeys });
      const orderedKeys = _logGroups.reduce((a, g) => a.concat(g.keys), []);

      let activeLogTab = localStorage.getItem('activeLogTab') || orderedKeys[0];
      if (!orderedKeys.includes(activeLogTab)) {
        activeLogTab = orderedKeys[0];
      }

      let sidebarHtml = '<div class="folder-sidebar">';
      sidebarHtml += '<div class="folder-sidebar-header">Strategies</div>';
      // ── SAB EK SAATH — bulk start/stop (webhook/_risk skip; loops real traders) ──
      sidebarHtml += `
      <div style="padding:8px 10px;border-bottom:1px solid #21262d;background:#12161c">
        <div style="font-size:9px;color:#6e7681;text-transform:uppercase;letter-spacing:.6px;margin-bottom:5px">⚡ Sab ek saath</div>
        <div style="display:flex;gap:5px">
          <button onclick="bulkLogControl('paper')" title="Saari strategies PAPER me start"
            style="flex:1;padding:5px 0;background:#12261a;border:1px solid #238636;color:#3fb950;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">▶ Paper</button>
          <button onclick="bulkLogControl('live')" title="Saari strategies LIVE me start (asli paisa!)"
            style="flex:1;padding:5px 0;background:#0d1f33;border:1px solid #1f6feb;color:#58a6ff;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">💰 Live</button>
          <button onclick="bulkLogControl('stop')" title="Saari strategies STOP"
            style="flex:1;padding:5px 0;background:#2a1416;border:1px solid #da3633;color:#f85149;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer">⏹ Stop</button>
        </div>
        <div id="bulk-log-status" style="font-size:10px;color:#8b949e;margin-top:5px;min-height:13px"></div>
      </div>`;

      let contentHtml = '<div class="folder-content" style="padding: 24px;">';

      _logGroups.forEach(group => {
        sidebarHtml += `<div style="padding:10px 12px 4px;font-size:10px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;color:#6e7681">${group.title}</div>`;
        group.keys.forEach((key) => {
        const type = key.split('_')[0];
        const color = getStratColor(type);
        const isActive = key === activeLogTab;

        let tabClassColor = '';
        let emoji = '📄';   // content-panel header still uses a file-type icon
        if (type === 'ema') { tabClassColor = 'blue-tab'; emoji = '📈'; }
        else if (type === 'rsi') { tabClassColor = 'active'; emoji = '📉'; } // yellow/amber is standard active tab
        else if (type === 'range') { tabClassColor = 'green-tab'; emoji = '↕️'; }

        // Status dot instead of a file-type icon: red = live, blue = paper,
        // grey = not active. Kept fresh by updateLogStatusDots() on the 5s poll.
        const _sd = _logStatusDot(key);
        const _num = _LOG_NUM[_lcKey(key)];
        const _numBadge = _num ? `<span style="display:inline-block;min-width:16px;padding:0 4px;margin-right:6px;border-radius:4px;background:rgba(88,166,255,.18);color:#58a6ff;font-size:10px;font-weight:700;text-align:center;vertical-align:middle">${_num}</span>` : '';
        sidebarHtml += `
      <div class="folder-tab ${isActive ? 'active ' + tabClassColor : ''}"
           data-log-key="${key}" title="${key} — double-click: Strategy Lab page (naye tab me)"
           onclick="switchLogTab('${key}', this)" ondblclick="openStratLab('${key}')">
        <span class="log-status-dot" id="${key}-log-statusdot" title="${_sd.title}" style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${_sd.color};margin-right:7px;vertical-align:middle;flex-shrink:0"></span>${_numBadge}${_logName(key)}
      </div>
    `;

        contentHtml += `
      <div class="settings-section ${isActive ? 'active' : ''}" id="${key}-log-section">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px">
          <div>
            <h2 style="color:${color};margin:0;font-size:17px;font-weight:600;display:flex;align-items:center;gap:8px">
              <span>${emoji}</span> ${_num ? _num + ' · ' : ''}${_logName(key)} LOG
              <span style="font-size:11px;color:#6e7681;font-weight:500">(${key})</span>
            </h2>
            <div style="font-size:11.5px;color:#8b949e;margin-top:4px">Strategy process log streaming.</div>
          </div>
          <div style="display:flex;align-items:center;gap:10px">
            <div id="${key}-log-runctl"><!-- run status/start/stop, synced w/ Risk tab --></div>
            <button id="${key}-pause-btn" onclick="toggleLogPause('${key}')"
              title="Auto-scroll freeze karta hai — log stream chalta rehta hai"
              style="padding:5px 12px;background:#21262d;border:1px solid #30363d;border-radius:6px;color:#e6edf3;font-size:12px;cursor:pointer;font-weight:500;transition:all 0.15s">
              ⏸ Freeze scroll
            </button>
          </div>
        </div>
        <div class="log-box" id="${key}-log" style="height: 550px; overflow-y: auto; font-family: monospace; font-size: 12.5px; background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 12px; line-height: 1.7;">Loading...</div>
      </div>
    `;
        });
      });

      sidebarHtml += '</div>';
      contentHtml += '</div>';

      container.innerHTML = sidebarHtml + contentHtml;
      renderLogRunControls();
    }

    // Double-click a Logs-sidebar strategy → open its Strategy Lab (3-pass) page in
    // a new tab. config-key → runs/<slug>/ (same 8 mission strategies as _LOG_NUM/
    // _LOG_NAME). No mapped run → open the Lab hub so the user can pick.
    const _LAB_SLUG = {
      orb_v1: 'mid_orb_nifty', straddle_v1: 'long_straddle_orb', dvert_v1: 'debit_vertical_orb',
      orbst_v1: 'orb_supertrend', chainzone_v1: 'chain_zone_longatm', backspread_v1: 'ratio_backspread',
      shortvol_v1: 'shortvol_ironfly', banknifty_v1: 'banknifty_hunt',
    };
    function openStratLab(key) {
      const slug = _LAB_SLUG[String(key).toLowerCase()];
      if (slug) {
        window.open('/lab/runs/' + slug + '/index.html', '_blank');
      } else {
        window.open('/lab/hub.html', '_blank');
        if (typeof toast === 'function') toast('Is strategy ka apna Lab page nahi — hub khola', 'info');
      }
    }

    // ── Bulk start/stop all real strategies from the Logs sidebar ──
    // webhook (dashboard-process, no PID) + _risk/webhooks/_ui_config (config-only) skip.
    // Sequential (Dhan ~1 req/sec — parallel start rate-limit cascade karta, TRAP #2).
    async function bulkLogControl(mode) {
      const skip = new Set(['_risk', 'webhooks', '_ui_config']);
      const keys = Object.keys(GLOBAL_CONFIG).filter(k =>
        !skip.has(k) && !k.toLowerCase().startsWith('webhook'));
      if (!keys.length) return;
      const verb = mode === 'stop' ? 'STOP' : ('start in ' + mode.toUpperCase());
      let msg = `Saari ${keys.length} strategies ko ${verb} karein?`;
      if (mode === 'live') msg += '\n\n⚠️ LIVE = ASLI PAISA. Har strategy real order bhejegi. Pakka?';
      if (!confirm(msg)) return;

      const st = document.getElementById('bulk-log-status');
      let done = 0, ok = 0;
      for (const k of keys) {
        if (st) st.textContent = `${verb}... ${done + 1}/${keys.length} (${k})`;
        try {
          const url = mode === 'stop' ? `/api/stop?s=${k}` : `/api/start?s=${k}&mode=${mode}`;
          const r = await fetch(url, { method: 'POST' });
          const j = await r.json();
          if (!j.msg || j.msg.includes('✅') || mode === 'stop') ok++;
        } catch (e) { /* ek fail baaki na roke */ }
        done++;
      }
      await checkStatus();
      renderLogRunControls();
      updateLogStatusDots();
      if (typeof renderRmsSummary === 'function') renderRmsSummary();
      if (st) st.textContent = `✅ ${ok}/${keys.length} ${mode === 'stop' ? 'stopped' : mode}`;
      if (typeof flash === 'function') flash(`Bulk ${verb}: ${ok}/${keys.length} done`, mode === 'stop' ? '#f85149' : '#3fb950');
    }

    function switchLogTab(key, el) {
      document.querySelectorAll('#log-folder-card .settings-section').forEach(s => s.classList.remove('active'));
      const sec = document.getElementById(`${key}-log-section`);
      if (sec) sec.classList.add('active');

      document.querySelectorAll('#log-folder-card .folder-tab').forEach(t => {
        t.classList.remove('active', 'blue-tab', 'green-tab');
      });

      if (el) {
        el.classList.add('active');
        const type = key.split('_')[0];
        if (type === 'ema') el.classList.add('blue-tab');
        else if (type === 'range') el.classList.add('green-tab');
      }

      localStorage.setItem('activeLogTab', key);
      updateLogs();
    }

    // Run status/Start/Stop badge per strategy card on the Logs tab — reads the
    // SAME RUNNING_PIDS state (from /api/status) and calls the SAME
    // riskStartBot/riskStopBot used by the Risk tab's "Run Controls" column, so
    // both pages always agree and either one can be used to start/stop a bot.
    function renderLogRunControls() {
      Object.keys(GLOBAL_CONFIG).forEach(key => {
        if (key === '_risk' || key === 'webhooks') return;
        const el = document.getElementById(`${key}-log-runctl`);
        if (!el) return;
        if (key.toLowerCase().startsWith('webhook')) { el.innerHTML = ''; return; }
        const pid = RUNNING_PIDS[key] || RUNNING_PIDS[key.toLowerCase()];
        const mode = RUNNING_PIDS[key + '_mode'] || RUNNING_PIDS[key.toLowerCase() + '_mode'] || 'paper';
        if (pid) {
          const modeBadge = mode === 'live'
            ? '<span style="color:#f85149;font-weight:600">🔴 Live</span>'
            : '<span style="color:#58a6ff;font-weight:600">🔵 Paper</span>';
          el.innerHTML = `<div style="display:flex;align-items:center;white-space:nowrap;font-size:11px">${modeBadge}`
            + `<button class="btn btn-red" style="padding:3px 7px;font-size:11px;margin-left:6px;line-height:1" onclick="riskStartStopFromLog('${key}','stop')">Stop</button>`
            + `<button class="btn btn-gray" style="padding:3px 7px;font-size:11px;margin-left:6px;line-height:1" onclick="openWatchlist('${key}')">👀 Watch</button></div>`;
        } else {
          el.innerHTML = `<div style="display:flex;align-items:center;white-space:nowrap;font-size:11px"><span style="color:#8b949e">⚫ Stopped</span>`
            + `<button class="btn btn-green" style="padding:3px 7px;font-size:11px;margin-left:6px;line-height:1" onclick="riskStartStopFromLog('${key}','paper')">Paper</button>`
            + `<button class="btn btn-blue" style="padding:3px 7px;font-size:11px;margin-left:4px;line-height:1" onclick="riskStartStopFromLog('${key}','live')">Live</button>`
            + `<button class="btn btn-gray" style="padding:3px 7px;font-size:11px;margin-left:6px;line-height:1" onclick="openWatchlist('${key}')">👀 Watch</button></div>`;
        }
      });
      updateLogStatusDots();   // keep the sidebar status dots in sync with the 5s poll
    }

    async function riskStartStopFromLog(s, action) {
      if (action === 'stop') await riskStopBot(s);
      else await riskStartBot(s, action);
      renderLogRunControls();
    }

    function toggleLogPause(key) {
      _logPaused[key] = !_logPaused[key];
      const btn = document.getElementById(`${key}-pause-btn`);
      if (_logPaused[key]) {
        btn.textContent = '▶ Resume scroll';
        btn.style.color = '#3fb950';
        btn.style.borderColor = '#3fb950';
      } else {
        btn.textContent = '⏸ Freeze scroll';
        btn.style.color = '#e6edf3';
        btn.style.borderColor = '#30363d';
        // scroll to bottom when resuming
        const box = document.getElementById(`${key}-log`);
        if (box) box.scrollTop = box.scrollHeight;
      }
    }

    let _watchPollTimer = null, _watchKey = null;
    let _watchSort = { col: 'symbol', dir: 1 };
    let _watchLastSyms = [];
    async function openWatchlist(key) {
      const modal = document.getElementById('watch-modal');
      document.getElementById('watch-strat-name').textContent = key;
      modal.style.display = 'flex';
      _watchKey = key;
      await _watchRefresh(key);
      clearInterval(_watchPollTimer);
      _watchPollTimer = setInterval(() => _watchRefresh(key), 5000);
    }
    function closeWatchlist() {
      document.getElementById('watch-modal').style.display = 'none';
      clearInterval(_watchPollTimer);
      _watchPollTimer = null;
      _watchKey = null;
    }
    function openWatchChart(symbol) {
      if (!_watchKey || !symbol) return;
      window.open(`/watch-chart?symbol=${encodeURIComponent(symbol)}&strategy=${encodeURIComponent(_watchKey)}`, '_blank');
    }
    function watchSortBy(col) {
      if (_watchSort.col === col) _watchSort.dir *= -1;
      else _watchSort = { col, dir: 1 };
      _renderWatchTable(_watchLastSyms);
    }
    function _watchSortVal(s, col) {
      const v = s[col];
      if (v == null) return col === 'symbol' ? '' : -Infinity;
      if (typeof v === 'boolean') return v ? 1 : 0;
      if (typeof v === 'string') return v;
      return Number(v);
    }
    function _renderWatchTable(syms) {
      const box = document.getElementById('watch-content');
      if (!syms.length) { box.innerHTML = '<div style="color:#8b949e;font-size:12px;padding:10px">No symbols yet — strategy still loading.</div>'; return; }
      const { col, dir } = _watchSort;
      const sorted = [...syms].sort((a, b) => {
        const av = _watchSortVal(a, col), bv = _watchSortVal(b, col);
        if (av < bv) return -1 * dir;
        if (av > bv) return 1 * dir;
        return 0;
      });
      const arrow = c => c === col ? (dir === 1 ? ' ▲' : ' ▼') : '';
      const th = (c, label) => `<th onclick="watchSortBy('${c}')" style="padding:5px 8px;cursor:pointer;user-select:none" title="Sort">${label}${arrow(c)}</th>`;
      const rows = sorted.map(s => `
    <tr style="border-bottom:1px solid #21262d;cursor:pointer" onclick="openWatchChart('${s.symbol || ''}')" title="Click to open chart">
      <td style="padding:5px 8px;font-weight:600">${s.symbol || ''}</td>
      <td style="padding:5px 8px">${s.ltp != null ? Number(s.ltp).toFixed(2) : '—'}</td>
      <td style="padding:5px 8px;color:${s.zone_type === 'GREEN' ? '#3fb950' : s.zone_type === 'RED' ? '#f85149' : '#8b949e'}">${s.zone_type || '—'}</td>
      <td style="padding:5px 8px">${s.zone_lower != null ? Number(s.zone_lower).toFixed(1) : '—'} - ${s.zone_upper != null ? Number(s.zone_upper).toFixed(1) : '—'}</td>
      <td style="padding:5px 8px">${s.zone_fresh ? '🟢 fresh' : '⚪ stale'}</td>
      <td style="padding:5px 8px">${s.touch_active ? `touching ${s.active_touch_type || ''}` : '—'}</td>
      <td style="padding:5px 8px;color:${s.position ? '#58a6ff' : '#8b949e'}">${s.position || 'flat'}</td>
      <td style="padding:5px 8px">${s.trades_today || 0}</td>
    </tr>`).join('');
      box.innerHTML = `
    <div style="font-size:10px;color:#8b949e;margin-bottom:8px">${_watchUpdatedTxt || ''} — auto-refresh 5s — click a column to sort, click a row to open its chart</div>
    <table style="width:100%;font-size:12px;border-collapse:collapse">
      <thead style="position:sticky;top:0;background:#161b22"><tr style="text-align:left;color:#8b949e">
        ${th('symbol', 'Symbol')}${th('ltp', 'LTP')}${th('zone_type', 'Zone')}${th('zone_upper', 'Range')}
        ${th('zone_fresh', 'Freshness')}${th('touch_active', 'Touch')}${th('position', 'Position')}${th('trades_today', 'Trades')}
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
    }
    let _watchUpdatedTxt = '';
    async function _watchRefresh(key) {
      const box = document.getElementById('watch-content');
      try {
        const r = await fetch(`/api/watch/${key}`);
        const d = await r.json();
        if (d.error) { box.innerHTML = `<div style="color:#8b949e;font-size:12px;padding:10px">${d.error}</div>`; return; }
        const syms = d.symbols || [];
        _watchLastSyms = syms;
        _watchUpdatedTxt = 'Updated ' + new Date(d.timestamp * 1000).toLocaleTimeString();
        _renderWatchTable(syms);
      } catch (e) {
        box.innerHTML = `<div style="color:#f85149;font-size:12px;padding:10px">Fetch failed: ${e}</div>`;
      }
    }

    // ── CONFIG TAB DYNAMIC GRID ──
