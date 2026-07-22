// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // ── COLUMN PREFS (localStorage) ──────────────────────────────────────────────
    const COMPLETED_COLS_DEF = [
      { id: 'date', l: 'Date', a: 'left', on: true, fixed: true },
      { id: 'symbol', l: 'Symbol', a: 'left', on: true, fixed: true },
      { id: 'strategy', l: 'Strategy', a: 'left', on: true },
      { id: 'tags', l: 'Tags', a: 'left', on: true },
      { id: 'manual_tags', l: 'Manual Tags', a: 'left', on: true },
      { id: 'side', l: 'Side', a: 'center', on: true },
      { id: 'entry_px', l: 'Entry Px', a: 'right', on: true },
      { id: 'exit_px', l: 'Exit Px', a: 'right', on: true },
      { id: 'entry_time', l: 'In', a: 'center', on: true },
      { id: 'exit_time', l: 'Out', a: 'center', on: true },
      { id: 'exit_reason', l: 'Exit Reason', a: 'left', on: true },
      { id: 'duration', l: 'Dur', a: 'center', on: true },
      { id: 'qty', l: 'Qty', a: 'left', on: true },
      { id: 'points', l: 'Points', a: 'right', on: true },
      { id: 'gross', l: 'Gross', a: 'right', on: true },
      { id: 'tax', l: 'Tax', a: 'right', on: true },
      { id: 'net', l: 'Net', a: 'right', on: true },
      { id: 'ret_pct', l: '% Ret', a: 'right', on: true },
      { id: 'run_up', l: 'Run-Up', a: 'right', on: true },
      { id: 'run_down', l: 'Run-Down', a: 'right', on: true },
      { id: 'cumulative', l: 'Cumulative', a: 'right', on: true },
      { id: 'chart', l: 'Chart', a: 'center', on: true },
      { id: 'actions', l: 'Actions', a: 'center', on: true, fixed: true }
    ];

    const OPEN_COLS_DEF = [
      { id: 'date', l: 'Date', a: 'left', on: true, fixed: true },
      { id: 'symbol', l: 'Symbol', a: 'left', on: true, fixed: true },
      { id: 'strategy', l: 'Strategy', a: 'left', on: true },
      { id: 'tags', l: 'Tags', a: 'left', on: true },
      { id: 'manual_tags', l: 'Manual Tags', a: 'left', on: true },
      { id: 'side', l: 'Side', a: 'center', on: true },
      { id: 'entry_px', l: 'Entry Px', a: 'right', on: true },
      { id: 'ltp', l: 'LTP', a: 'right', on: true },
      { id: 'entry_time', l: 'Entry Time', a: 'center', on: true },
      { id: 'points', l: 'Points', a: 'right', on: true },
      { id: 'pnl', l: 'P&L', a: 'right', on: true },
      { id: 'ret_pct', l: '% Ret', a: 'right', on: true },
      { id: 'margin', l: 'Margin', a: 'right', on: true },
      { id: 'run_up', l: 'Run-Up', a: 'right', on: true },
      { id: 'run_down', l: 'Run-Down', a: 'right', on: true },
      { id: 'qty', l: 'Qty', a: 'left', on: true },
      { id: 'chart', l: 'Chart', a: 'center', on: true },
      { id: 'actions', l: 'Actions', a: 'center', on: true, fixed: true }
    ];

    const CAL_POINTS_COLS_DEF = [
      { id: 'date', l: 'Date', a: 'left', on: true, fixed: true },
      { id: 'symbol', l: 'Symbol', a: 'left', on: true, fixed: true },
      { id: 'tags', l: 'Tags', a: 'left', on: true },
      { id: 'manual_tags', l: 'Manual Tags', a: 'left', on: true },
      { id: 'side', l: 'Side', a: 'center', on: true },
      { id: 'entry_px', l: 'Entry Px', a: 'right', on: true },
      { id: 'exit_px', l: 'Exit Px', a: 'right', on: true },
      { id: 'entry_time', l: 'In', a: 'center', on: true },
      { id: 'exit_time', l: 'Out', a: 'center', on: true },
      { id: 'exit_reason', l: 'Exit Reason', a: 'left', on: true },
      { id: 'duration', l: 'Dur', a: 'center', on: true },
      { id: 'qty', l: 'Qty', a: 'left', on: true },
      { id: 'points', l: 'Points', a: 'right', on: true },
      { id: 'gross', l: 'Gross', a: 'right', on: true },
      { id: 'tax', l: 'Tax', a: 'right', on: true },
      { id: 'net', l: 'Net', a: 'right', on: true },
      { id: 'margin', l: 'Margin', a: 'right', on: true, title: 'Capital required to execute the trade. BUY option = premium × qty (EXACT debit). SELL option = ~real SPAN margin (Dhan calc, background-computed) — shows "—" for a moment on first view then fills in; "—" stays only for expired contracts. Drag next to Exit Reason via ⚙ Columns if you prefer.' },
      { id: 'opt_fixed', l: 'Opt Fixed', a: 'right', on: false, title: 'What-if NET, SAME hold as actual: fixed Target ₹4,000/lot · SL ₹1,000/lot. — = no 1-min bar data.' },
      { id: 'opt_aggr', l: 'Opt Aggr', a: 'right', on: false, title: 'What-if NET, SAME hold as actual: aggressive trail Target ₹6,000/lot · init SL ₹2,500/lot · step ₹100. — = no bar data.' },
      { id: 'opt_aggr_eod', l: 'Opt Aggr→EOD', a: 'right', on: false, title: 'What-if NET if the ATR SL were REPLACED by the aggressive trail RIDDEN to 15:15 (init SL ₹1,500/lot, no target cap). Directional — NOT a validated backtest. — = no bar data.' },
      { id: 'ret_pct', l: '% Ret', a: 'right', on: true },
      { id: 'run_up', l: 'Run-Up', a: 'right', on: true },
      { id: 'run_down', l: 'Run-Down', a: 'right', on: true },
      { id: 'cumulative', l: 'Cumulative', a: 'right', on: true },
      { id: 'chart', l: 'Chart', a: 'center', on: true },
      { id: 'actions', l: 'Actions', a: 'center', on: true, fixed: true }
    ];

    window._ordCompletedCols = null;
    window._ordOpenCols = null;
    window._calPointsCols = null;

    function _loadCalPointsColPrefs() {
      try {
        const s = localStorage.getItem('cal_points_cols');
        if (s && (s.includes('entry_price') || s.includes('exit_price') || s.includes('pnl'))) {
          localStorage.removeItem('cal_points_cols');
          window._calPointsCols = JSON.parse(JSON.stringify(CAL_POINTS_COLS_DEF));
          return;
        }
        window._calPointsCols = s ? JSON.parse(s) : JSON.parse(JSON.stringify(CAL_POINTS_COLS_DEF));
        CAL_POINTS_COLS_DEF.forEach(def => {
          if (!window._calPointsCols.find(x => x.id === def.id)) {
            window._calPointsCols.push(JSON.parse(JSON.stringify(def)));
          }
        });
      } catch (e) {
        window._calPointsCols = JSON.parse(JSON.stringify(CAL_POINTS_COLS_DEF));
      }
    }

    function _loadOrdColPrefs() {
      try {
        const c = localStorage.getItem('ord_completed_cols');
        const o = localStorage.getItem('ord_open_cols');
        window._ordCompletedCols = c ? JSON.parse(c) : JSON.parse(JSON.stringify(COMPLETED_COLS_DEF));
        window._ordOpenCols = o ? JSON.parse(o) : JSON.parse(JSON.stringify(OPEN_COLS_DEF));

        // 2026-07-02: 'note' was removed as a standalone column (button now lives
        // in the ⋮ actions menu, preview shows under Symbol automatically) — drop
        // any stale 'note' entry a user's OLD saved localStorage prefs still have,
        // so it doesn't linger as an empty dead column.
        window._ordCompletedCols = window._ordCompletedCols.filter(x => x.id !== 'note');
        window._ordOpenCols = window._ordOpenCols.filter(x => x.id !== 'note');

        COMPLETED_COLS_DEF.forEach(def => {
          if (!window._ordCompletedCols.find(x => x.id === def.id)) {
            window._ordCompletedCols.push(JSON.parse(JSON.stringify(def)));
          }
        });
        OPEN_COLS_DEF.forEach(def => {
          if (!window._ordOpenCols.find(x => x.id === def.id)) {
            window._ordOpenCols.push(JSON.parse(JSON.stringify(def)));
          }
        });
      } catch (e) {
        window._ordCompletedCols = JSON.parse(JSON.stringify(COMPLETED_COLS_DEF));
        window._ordOpenCols = JSON.parse(JSON.stringify(OPEN_COLS_DEF));
      }
    }

    function openColModal() {
      if (!window._ordCompletedCols || !window._ordOpenCols) _loadOrdColPrefs();

      const renderList = (elId, defs) => {
        const el = document.getElementById(elId);
        el.innerHTML = defs.map(c => `
      <label style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:#0d1117;border-radius:6px;cursor:${c.fixed ? 'default' : 'pointer'};opacity:${c.fixed ? '.5' : '1'}">
        <input type="checkbox" data-col="${c.id}" ${c.on ? 'checked' : ''} ${c.fixed ? 'disabled' : ''} style="accent-color:#1f6feb;width:14px;height:14px">
        <span style="font-size:12px;color:#e6edf3">${c.l}</span>
      </label>`).join('');
      };

      renderList('col-completed-list', window._ordCompletedCols);
      renderList('col-open-list', window._ordOpenCols);
      document.getElementById('col-modal').style.display = 'flex';
    }

    function closeColModal() {
      document.getElementById('col-modal').style.display = 'none';
    }

    function saveColPrefs() {
      const read = (elId, cols) => {
        document.querySelectorAll(`#${elId} input[data-col]`).forEach(inp => {
          const c = cols.find(x => x.id === inp.dataset.col);
          if (c && !c.fixed) c.on = inp.checked;
        });
      };
      read('col-completed-list', window._ordCompletedCols);
      read('col-open-list', window._ordOpenCols);
      localStorage.setItem('ord_completed_cols', JSON.stringify(window._ordCompletedCols));
      localStorage.setItem('ord_open_cols', JSON.stringify(window._ordOpenCols));
      saveUiConfigToBackend({
        ord_completed_cols: JSON.stringify(window._ordCompletedCols),
        ord_open_cols: JSON.stringify(window._ordOpenCols)
      });
      closeColModal();
      renderCachedOrders();
    }

    function resetColPrefs() {
      window._ordCompletedCols = JSON.parse(JSON.stringify(COMPLETED_COLS_DEF));
      window._ordOpenCols = JSON.parse(JSON.stringify(OPEN_COLS_DEF));
      localStorage.removeItem('ord_completed_cols');
      localStorage.removeItem('ord_open_cols');
      saveUiConfigToBackend({ ord_completed_cols: null, ord_open_cols: null });
      closeColModal();
      renderCachedOrders();
    }

    _loadOrdColPrefs();
    _loadCalPointsColPrefs();

    // ── Column toggle + DRAG-REORDER modal (Point-Per-Trade table) ───────────
    // Table renders columns in window._calPointsCols array order, so reordering
    // the array (via drag) reorders the table; Save persists the array (order +
    // on/off) to localStorage.
    let _calColDragId = null;
    function _calColSyncChecks() {   // pull current checkbox states into the array (survive a mid-edit drag)
      document.querySelectorAll('#cal-points-col-list input[data-col]').forEach(inp => {
        const c = window._calPointsCols.find(x => x.id === inp.dataset.col);
        if (c && !c.fixed) c.on = inp.checked;
      });
    }
    function _calColDragStart(ev, id) { _calColDragId = id; try { ev.dataTransfer.effectAllowed = 'move'; } catch (e) { } }
    function _calColDragOver(ev) { ev.preventDefault(); try { ev.dataTransfer.dropEffect = 'move'; } catch (e) { } }
    function _calColDrop(ev, targetId) {
      ev.preventDefault();
      if (!_calColDragId || _calColDragId === targetId) { _calColDragId = null; return; }
      _calColSyncChecks();
      const arr = window._calPointsCols;
      const from = arr.findIndex(c => c.id === _calColDragId);
      if (from < 0) { _calColDragId = null; return; }
      const [moved] = arr.splice(from, 1);
      const to = arr.findIndex(c => c.id === targetId);        // recompute after removal
      arr.splice(to < 0 ? arr.length : to, 0, moved);          // drop BEFORE the target row
      _calColDragId = null;
      openCalPointsColModal();          // re-render list in the new order
      renderPointsPerTradeTable();      // live preview in the table
    }
    function openCalPointsColModal() {
      if (!window._calPointsCols) _loadCalPointsColPrefs();
      const el = document.getElementById('cal-points-col-list');
      el.innerHTML = window._calPointsCols.map(c => `
    <label ondragover="_calColDragOver(event)" ondrop="_calColDrop(event,'${c.id}')"
      style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:#0d1117;border-radius:6px;opacity:${c.fixed ? '.5' : '1'}">
      <span draggable="${c.fixed ? 'false' : 'true'}" ondragstart="_calColDragStart(event,'${c.id}')"
        style="color:#6e7681;font-size:14px;line-height:1;cursor:${c.fixed ? 'default' : 'grab'};user-select:none" title="${c.fixed ? 'Fixed column' : 'Drag to reorder'}">${c.fixed ? '·' : '⠿'}</span>
      <input type="checkbox" data-col="${c.id}" ${c.on ? 'checked' : ''} ${c.fixed ? 'disabled' : ''} style="accent-color:#1f6feb;width:14px;height:14px;cursor:pointer">
      <span style="font-size:12px;color:#e6edf3">${c.l}</span>
    </label>`).join('');
      document.getElementById('cal-points-col-modal').style.display = 'flex';
    }

    function saveCalPointsColPrefs() {
      _calColSyncChecks();
      localStorage.setItem('cal_points_cols', JSON.stringify(window._calPointsCols));
      // backend sync is best-effort — it lives in app-03 which stats2 doesn't
      // load, so guard it (was an unguarded ReferenceError that aborted Save on
      // /stats2 after localStorage but before the modal closed → "Save nahi hota").
      try { if (typeof saveUiConfigToBackend === 'function') saveUiConfigToBackend('cal_points_cols', JSON.stringify(window._calPointsCols)); } catch (e) { }
      document.getElementById('cal-points-col-modal').style.display = 'none';
      renderPointsPerTradeTable();
    }

    function toggleCalDropdown(event, tradeId) {
      event.stopPropagation();
      const dropdown = document.getElementById(`cal-dropdown-${tradeId}`);
      if (!dropdown) return;
      const isCurrentlyOpen = dropdown.style.display === 'block';

      document.querySelectorAll('.dropdown-content').forEach(el => {
        el.style.display = 'none';
      });
      if (isCurrentlyOpen) return;

      const trig = event.currentTarget || event.target;
      const r = trig.getBoundingClientRect();
      dropdown.style.display = 'block';
      dropdown.style.position = 'fixed';
      dropdown.style.right = 'auto';
      const mw = dropdown.offsetWidth || 160, mh = dropdown.offsetHeight || 190;
      let left = r.right - mw;
      if (left < 8) left = 8;
      let top = r.bottom + 4;
      if (top + mh > window.innerHeight - 8) top = r.top - mh - 4;
      if (top < 8) top = 8;
      dropdown.style.left = left + 'px';
      dropdown.style.top = top + 'px';
    }

    // ── Stats tab: toggleable-columns closed-positions table ───────────────────
    const STATS_COLS_DEF = [
      { id: 'entry_date', l: 'Date', a: 'left', on: true, fixed: true },
      { id: 'sym', l: 'Symbol', a: 'left', on: true, fixed: true },
      { id: 'entry', l: 'Side', a: 'center', on: true },
      { id: 'qty', l: 'Qty', a: 'right', on: true },
      { id: 'entry_price', l: 'Entry Px', a: 'right', on: true },
      { id: 'exit_price', l: 'Exit Px', a: 'right', on: true },
      { id: 'entry_time', l: 'In', a: 'center', on: false },
      { id: 'exit_time', l: 'Out', a: 'center', on: false },
      { id: 'pnl', l: 'P&L', a: 'right', on: true },
      { id: 'strategy', l: 'Strategy', a: 'left', on: false },
      { id: 'source', l: 'Source', a: 'left', on: false },
      { id: 'mode', l: 'Mode', a: 'left', on: false },
    ];
    window._statsCols = null;
    function _loadStatsColPrefs() {
      try {
        const s = localStorage.getItem('stats_cols');
        window._statsCols = s ? JSON.parse(s) : JSON.parse(JSON.stringify(STATS_COLS_DEF));
        STATS_COLS_DEF.forEach(def => { if (!window._statsCols.find(x => x.id === def.id)) window._statsCols.push(JSON.parse(JSON.stringify(def))); });
      } catch (e) { window._statsCols = JSON.parse(JSON.stringify(STATS_COLS_DEF)); }
    }
    function openStatsColModal() {
      if (!window._statsCols) _loadStatsColPrefs();
      const el = document.getElementById('stats-col-list');
      el.innerHTML = window._statsCols.map(c => `
    <label style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:#0d1117;border-radius:6px;cursor:${c.fixed ? 'default' : 'pointer'};opacity:${c.fixed ? '.5' : '1'}">
      <input type="checkbox" data-col="${c.id}" ${c.on ? 'checked' : ''} ${c.fixed ? 'disabled' : ''} style="accent-color:#1f6feb;width:14px;height:14px">
      <span style="font-size:12px;color:#e6edf3">${c.l}</span>
    </label>`).join('');
      document.getElementById('stats-col-modal').style.display = 'flex';
    }
    function saveStatsColPrefs() {
      document.querySelectorAll('#stats-col-list input[data-col]').forEach(inp => {
        const c = window._statsCols.find(x => x.id === inp.dataset.col);
        if (c && !c.fixed) c.on = inp.checked;
      });
      localStorage.setItem('stats_cols', JSON.stringify(window._statsCols));
      saveUiConfigToBackend('stats_cols', JSON.stringify(window._statsCols));
      document.getElementById('stats-col-modal').style.display = 'none';
      renderStatsClosedTable(window._statsLastTrades || []);
    }
    function renderStatsClosedTable(trades) {
      if (!window._statsCols) _loadStatsColPrefs();
      const cols = window._statsCols.filter(c => c.on);
      document.getElementById('stats-cols-thead').innerHTML =
        cols.map(c => `<th style="padding:6px;font-weight:500;text-align:${c.a}">${c.l}</th>`).join('');
      const closed = trades.filter(t => t.pnl !== null && t.pnl !== undefined);
      document.getElementById('stats-closed-tbody').innerHTML = closed.map(t => {
        return '<tr style="border-bottom:1px solid #21262d">' + cols.map(c => {
          let v = t[c.id];
          if (c.id === 'pnl' && v != null) v = `<span style="color:${v >= 0 ? '#3fb950' : '#f85149'}">${v >= 0 ? '+' : ''}${v}</span>`;
          else if (v == null) v = '—';
          return `<td style="padding:6px;text-align:${c.a}">${v}</td>`;
        }).join('') + '</tr>';
      }).join('') || '<tr><td colspan="' + cols.length + '" style="padding:14px;text-align:center;color:#8b949e">No closed trades in range</td></tr>';
    }
    function renderStatsGroupedList(trades) {
      const byDay = {};
      trades.forEach(t => { const k = t.entry_date || t.exit_date || '—'; (byDay[k] = byDay[k] || []).push(t); });
      const days = Object.keys(byDay).sort().reverse();
      document.getElementById('stats-grouped-list').innerHTML = days.map(day => {
        const rows = byDay[day];
        const dayPnl = rows.reduce((s, t) => s + (t.pnl || 0), 0);
        return `<div style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:10px 12px">
      <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span style="color:#58a6ff;font-weight:600;font-size:12px">${day}</span>
        <span style="font-size:12px;font-weight:700;color:${dayPnl >= 0 ? '#3fb950' : '#f85149'}">${dayPnl >= 0 ? '+' : ''}${Math.round(dayPnl).toLocaleString('en-IN')}</span>
      </div>
      ${rows.map(t => `<div style="display:flex;gap:6px;font-size:11px;color:#adbac7;padding:2px 0">
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.sym || ''}</span>
        <span style="color:#8b949e">${t.entry || ''}</span>
        <span style="min-width:50px;text-align:right;color:${(t.pnl || 0) >= 0 ? '#3fb950' : '#f85149'}">${t.pnl != null ? (t.pnl >= 0 ? '+' : '') + t.pnl : 'open'}</span>
      </div>`).join('')}
    </div>`;
      }).join('') || '<div style="color:#8b949e;font-size:12px;padding:10px">Koi trade nahi is range mein</div>';
    }
    async function statsMetricsRender(dateFrom, dateTo, filt) {
      try {
        const q = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, ...filt });
        const r = await fetch('/api/orders/stats-summary?' + q.toString());
        const j = await r.json();
        const m = j.metrics || {};
        window._statsLastTrades = j.trades || [];
        document.getElementById('sm-pf').textContent = m.profit_factor ?? '—';
        document.getElementById('sm-exp').textContent = m.expectancy ?? '—';
        document.getElementById('sm-sharpe').textContent = m.sharpe ?? '—';
        document.getElementById('sm-wr').textContent = (m.win_rate ?? 0) + '%';
        document.getElementById('sm-n').textContent = m.n_trades ?? 0;
        renderStatsGroupedList(window._statsLastTrades);
        renderStatsClosedTable(window._statsLastTrades);
      } catch (e) { console.error('[statsMetrics] error:', e); }
    }

    let _pnlTagMap = {};   // "sym|entry_time" -> trade-DB row (source/mode/strategy tags)
    function _pnlTagChips(d) {
      const t = _pnlTagMap[(d.sym || '') + '|' + (d.entry_time || '')];
      if (!t || typeof _ordTag !== 'function') return '';
      return ' ' + _ordTag(t.source, t.source) + _ordTag(t.mode, t.mode) + (t.strategy ? _ordTag(t.strategy.split(' | ')[0], 'name') : '');
    }

    function openTradeChart(sym, side, entry, exit, et, xt, qty, date, tf, ind, idx, sl, tp, strategy) {
      const params = { sym, side, entry, exit, et, xt, qty };
      if (date) params.date = date;
      if (tf) params.tf = tf;
      if (ind) params.ind = ind;
      if (idx != null) params.idx = idx;
      if (sl != null && sl !== '') params.sl = sl;   // SL as a premium price level (item F)
      if (tp != null && tp !== '') params.tp = tp;   // Target as a premium price level
      if (strategy) params.strategy = strategy;      // for underlying-pane zone/key-level overlay
      const q = new URLSearchParams(params);
      window.open('/trade-chart?' + q.toString(), 'trade_chart_win');
    }

    function formatLogLine(L) {
      L = L.replace(/</g, '&lt;').replace(/>/g, '&gt;');

      // [CONFIG] line → split into 3 readable rows
      if (L.includes('[CONFIG]')) {
        const ts = L.match(/^[\d\-]+ [\d:,]+/)?.[0] || '';
        const rest = L.replace(/^.*\[CONFIG\]\s*/, '');
        const parts = rest.split(' | ');
        // Group: line1 = TF/Instrument/Qty/MaxTrades, line2 = Entry, line3 = Exit
        const cfg = parts.filter(p => !p.startsWith('Entry') && !p.startsWith('Exit'));
        const entry = parts.find(p => p.startsWith('Entry')) || '';
        const exit_ = parts.find(p => p.startsWith('Exit')) || '';
        return `
      <div class="lb-dim" style="border-top:1px solid #21262d;margin-top:4px;padding-top:4px">
        <span style="color:#8b949e">${ts}</span>
        <span style="color:#58a6ff;font-weight:bold"> [CONFIG]</span>
        ${cfg.map(p => `<span style="color:#d29922"> ${p}</span>`).join(' <span style="color:#30363d">|</span>')}
      </div>
      <div class="lb-dim" style="padding-left:16px">
        <span style="color:#3fb950">&#x2192; ENTRY:</span>
        <span style="color:#c9d1d9"> ${entry.replace('Entry: ', '')}</span>
      </div>
      <div class="lb-dim" style="padding-left:16px;border-bottom:1px solid #21262d;padding-bottom:4px;margin-bottom:4px">
        <span style="color:#f85149">&#x2192; EXIT:</span>
        <span style="color:#c9d1d9"> ${exit_.replace('Exit: ', '')}</span>
      </div>`;
      }

      let cls = 'lb-info';
      let formattedL = L;

      if (/ERROR|error|Traceback|ModuleNot|TypeError/.test(L)) {
        cls = 'lb-err';
        formattedL = formattedL.replace(/ERROR/, "<span style='background:#f8514933;color:#f85149;padding:2px 6px;border-radius:4px;font-weight:bold'>❌ ERROR</span>");
      }
      else if (/WARNING|warning|Skipping stale|Failed|fail|HTTP 400/.test(L)) {
        cls = 'lb-warn';
        formattedL = formattedL.replace(/WARNING/, "<span style='background:#d2992233;color:#d29922;padding:2px 6px;border-radius:4px;font-weight:bold'>⚠️ WARNING</span>");
      }
      else if (L.includes('INFO SIGNAL ')) {
        cls = 'lb-info';
        formattedL = formattedL
          .replace('INFO SIGNAL BUY', "<span style='background:#3fb95033;color:#3fb950;padding:2px 6px;border-radius:4px;font-weight:bold;margin:0 4px'>⚡ SIGNAL BUY</span>")
          .replace('INFO SIGNAL SELL', "<span style='background:#f8514933;color:#f85149;padding:2px 6px;border-radius:4px;font-weight:bold;margin:0 4px'>⚡ SIGNAL SELL</span>")
          .replace(/reason=(.+)/, "<span style='color:#8b949e;font-size:11px;border:1px solid #30363d;padding:1px 4px;border-radius:4px;margin-left:8px'>reason=$1</span>");
      }
      else if (/\[PAPER\]/.test(L)) {
        cls = 'lb-info';
        formattedL = formattedL
          .replace('[PAPER] BUY', "<span style='background:#d2992233;color:#d29922;padding:2px 6px;border-radius:4px;font-weight:bold;margin:0 4px'>📄 PAPER BUY</span>")
          .replace('[PAPER] SELL', "<span style='background:#d2992233;color:#d29922;padding:2px 6px;border-radius:4px;font-weight:bold;margin:0 4px'>📄 PAPER SELL</span>")
          .replace(/correlationId=([\w-]+)/, "<span style='color:#8b949e;font-size:10px;margin-left:8px'>id: $1</span>");
      }
      else if (/\[LIVE\]/.test(L)) {
        cls = 'lb-info';
        formattedL = formattedL
          .replace('[LIVE] BUY', "<span style='background:#8957e533;color:#bc8cff;padding:2px 6px;border-radius:4px;font-weight:bold;margin:0 4px'>🔥 LIVE BUY</span>")
          .replace('[LIVE] SELL', "<span style='background:#8957e533;color:#bc8cff;padding:2px 6px;border-radius:4px;font-weight:bold;margin:0 4px'>🔥 LIVE SELL</span>")
          .replace(/correlationId=([\w-]+)/, "<span style='color:#8b949e;font-size:10px;margin-left:8px'>id: $1</span>");
      }
      else if (/\[BROKER-SHADOW\]/.test(L)) {
        cls = 'lb-dim';
        formattedL = formattedL.replace('[BROKER-SHADOW]', "<span style='color:#8b949e;font-weight:bold;background:#161b22;padding:1px 4px;border-radius:3px;border:1px solid #30363d'>SHADOW</span>");
      }
      else if (/levels loaded|sleeping|Loop done/.test(L)) {
        cls = 'lb-dim';
      }

      return `<div class="${cls}" style="margin-bottom:2px">${formattedL}</div>`;
    }

    async function updateLogs() {
      if (activeTab !== 'log') return;
      for (let key of Object.keys(GLOBAL_CONFIG)) {
        try {
          let r = await fetch(`/api/log?s=${key}`);
          let j = await r.json();
          let lines = (j.lines || []).slice(-120);
          let html = lines.map(formatLogLine).join('');
          let box = document.getElementById(`${key}-log`);
          if (box) {
            // "Stick to bottom" — only auto-scroll if the user is already near
            // the bottom AND hasn't paused. If they've scrolled up (to read), we
            // keep the log flowing but DON'T yank the viewport down. Pause = an
            // explicit lock that freezes auto-scroll regardless of position.
            const nearBottom = (box.scrollHeight - box.scrollTop - box.clientHeight) < 40;
            const prevTop = box.scrollTop;
            box.innerHTML = html || '<div class="lb-dim">No logs yet.</div>';
            if (!_logPaused[key] && nearBottom) {
              box.scrollTop = box.scrollHeight;   // follow new lines
            } else {
              box.scrollTop = prevTop;            // stay where the user is reading
            }
          }
          // Sound notification on new order lines
          const orderLines = lines.filter(l => /\[(PAPER|LIVE)\]\s+(BUY|SELL)/.test(l));
          if (orderLines.length > 0) {
            const lastLine = orderLines[orderLines.length - 1];
            if (lastLine !== _lastOrderLine[key]) {
              _lastOrderLine[key] = lastLine;
              const isWin = /WIN/.test(lastLine);
              _playOrderSound(isWin);
            }
          }
        } catch (e) { }
      }
    }

    // Token
    async function loadTokenStatus() {
      try {
        let r = await fetch('/api/token');
        let j = await r.json();
        const msg = document.getElementById('token-msg');
        if (j.has_token) {
          msg.style.color = '#3fb950';
          msg.innerText = `Token saved (saved at: ${j.saved_at}) — ends with ...${j.preview}`;
          document.getElementById('token-input').placeholder = 'Token already saved. Paste new one to update.';
        } else {
          msg.style.color = '#f85149';
          msg.innerText = 'No token saved yet.';
        }
      } catch (e) { }
      kiteLoadKeyStatus();
    }

    // Kite api_key + api_secret are PERMANENT (saved once in data/config.json, nothing
    // wipes them). Only the daily access_token changes. This just reflects that so the
    // user doesn't re-type key+secret every day out of habit — the form used to render
    // blank, making it look like they were required daily.
    async function kiteLoadKeyStatus() {
      const el = document.getElementById('kite-key-status');
      if (!el) return;
      try {
        const j = await fetch('/api/kite-key-status').then(r => r.json());
        if (j.has_key) {
          el.style.color = '#3fb950';
          el.innerHTML = `✅ API Key &amp; Secret already saved (…${j.api_key_preview || ''}) — `
            + `roz sirf niche <b>request_token</b> daalo, key/secret dobara nahi.`;
        } else {
          el.style.color = '#d29922';
          el.innerHTML = `⚙️ API Key/Secret abhi save nahi — pehli baar niche "One-time Setup" me daalo (ek hi baar).`;
        }
      } catch (e) { }
    }
    async function saveToken() {
      let t = document.getElementById('token-input').value.trim();
      if (!t) { flash('Token empty hai!'); return; }
      let r = await fetch('/api/token', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: t }) });
      let j = await r.json();
      flash(j.msg);
      document.getElementById('token-input').value = '';
      loadTokenStatus();
    }
    async function checkToken() {
      let r = await fetch('/api/token_check');
      let j = await r.json();
      document.getElementById('token-msg').innerText = j.msg;
    }

    // ── KITE TOKEN ──────────────────────────────────────────────────────────────
