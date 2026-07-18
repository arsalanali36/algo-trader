// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // ── CALENDAR TAB LOGIC ──
    let calYear = new Date().getFullYear();
    let calMonth = new Date().getMonth(); // 0-indexed, so 5 = June
    const CAL_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

    const CAL_MARKET_HOLIDAYS = {
      '2026-01-26': 'Republic Day',
      '2026-03-03': 'Holi',
      '2026-03-26': 'Shri Ram Navami',
      '2026-03-31': 'Shri Mahavir Jayanti',
      '2026-04-03': 'Good Friday',
      '2026-04-14': 'Dr. Baba Saheb Ambedkar Jayanti',
      '2026-05-01': 'Maharashtra Day',
      '2026-05-28': 'Bakri Id',
      '2026-06-26': 'Muharram',
      '2026-09-14': 'Ganesh Chaturthi',
      '2026-10-02': 'Mahatma Gandhi Jayanti',
      '2026-10-20': 'Dussehra',
      '2026-11-10': 'Diwali-Balipratipada',
      '2026-11-24': 'Prakash Gurpurb Sri Guru Nanak Dev',
      '2026-12-25': 'Christmas',
    };

    // Segment selection for calendar tab
    function calSeg(id, el) {
      const p = document.getElementById(id);
      p.querySelectorAll('span').forEach(x => {
        x.classList.remove('on');
        x.style.color = '#8b949e';
      });
      el.classList.add('on');
      el.style.color = '';
      calendarRender();
    }

    function _calSegVal(id) {
      const el = document.querySelector(`#${id} .on`);
      return el ? el.getAttribute('data-v') : '';
    }

    // ── Total Summary period grouping (Monthly / Weekly) helpers ────────────
    const _CAL_MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    // Week bucket key = the Monday of that trade's week (YYYY-MM-DD), so weekly
    // grouping is calendar-week aligned and sorts correctly as a string.
    function _calWeekKey(dateStr) {
      if (!dateStr) return '—';
      const d = new Date(dateStr + 'T00:00:00');
      if (isNaN(d)) return '—';
      const dow = (d.getDay() + 6) % 7;   // Mon=0 … Sun=6
      d.setDate(d.getDate() - dow);
      const p = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    }
    function _calPeriodLabel(key, mode) {
      if (!key || key === '—') return key || '—';
      if (mode === 'monthly') {
        const [y, m] = key.split('-');
        return `${_CAL_MON[(+m) - 1] || m} ${y}`;               // "Jul 2026"
      }
      if (mode === 'weekly') {
        const d = new Date(key + 'T00:00:00');
        if (isNaN(d)) return key;
        const e = new Date(d); e.setDate(e.getDate() + 6);       // Mon → Sun
        const p = n => String(n).padStart(2, '0');
        return `${p(d.getDate())} ${_CAL_MON[d.getMonth()]} – ${p(e.getDate())} ${_CAL_MON[e.getMonth()]}`;
      }
      return key;
    }
    // Group key for a trade under the current Total-Summary mode.
    function _calGroupKey(t, mode) {
      const d = t.exit_date || t.entry_date || '';
      if (mode === 'monthly') return d.slice(0, 7) || '—';       // YYYY-MM
      if (mode === 'weekly') return _calWeekKey(d);
      if (mode === 'day') return d || '—';
      return t.strategy || t.strat || 'unknown';                 // strategy
    }

    // ── Backtest view mode ──────────────────────────────────────────────────
    // The Stats calendar can show either live/paper trades (order_store) OR a
    // backtest run's day-by-day results (runs/<slug>/results.js). Same grid,
    // table, filters, range — only the data source switches. window.calBtMode
    // is the flag every render branch reads.
    function calSetView(v, el) {
      const p = document.getElementById('cal-view');
      p.querySelectorAll('span').forEach(x => { x.classList.remove('on'); x.style.color = '#8b949e'; });
      el.classList.add('on'); el.style.color = '';
      window.calBtMode = (v === 'bt');
      const liveOnly = document.getElementById('cal-live-only');
      const btOnly = document.getElementById('cal-bt-only');
      const broker = document.getElementById('cal-broker');
      if (liveOnly) liveOnly.style.display = window.calBtMode ? 'none' : 'inline-flex';
      if (btOnly) btOnly.style.display = window.calBtMode ? 'inline-flex' : 'none';
      if (broker) broker.style.display = window.calBtMode ? 'none' : '';
      const prem = document.getElementById('cal-prem');           // BS premium overlay: live-mode only
      if (prem) prem.style.display = window.calBtMode ? 'none' : 'inline-flex';
      const bsCard = document.getElementById('bs-div-card');
      if (bsCard && window.calBtMode) bsCard.style.display = 'none';
      const viewsWrap = document.getElementById('cal-views-wrap');
      if (viewsWrap) viewsWrap.style.display = '';   // Views dono modes me (live=strategies, bt=runs)
      window.calActiveView = null;                    // active view mode-specific → switch pe clear
      if (typeof _renderViewsBtn === 'function') _renderViewsBtn();
      const strat = document.getElementById('cal-strat');
      if (window.calBtMode) {
        _calLoadBtRuns();   // fill cal-strat with runs, then calendarRender()
      } else {
        if (strat) { strat.innerHTML = '<option value="">All strategies</option>'; strat.value = ''; }
        calendarRender();
      }
    }

    // Segment click for the backtest Pass/Period controls (mirrors calSeg).
    function calBtSeg(id, el) {
      const p = document.getElementById(id);
      p.querySelectorAll('span').forEach(x => { x.classList.remove('on'); x.style.color = '#8b949e'; });
      el.classList.add('on'); el.style.color = '';
      calendarRender();
    }

    // Populate the strategy dropdown with available backtest runs, then render.
    async function _calLoadBtRuns() {
      const strat = document.getElementById('cal-strat');
      try {
        const r = await fetch('/api/backtest/runs');
        const j = await r.json();
        const runs = j.runs || [];
        window._calBtRuns = runs;
        if (strat) {
          const prev = strat.value;
          // "All runs" (like live's "All strategies") — combines EVERY run into
          // one portfolio result. Sentinel value __ALL__ expanded in calendarRender.
          const allOpt = `<option value="__ALL__">⚡ All runs (combined)</option>`;
          strat.innerHTML = allOpt + runs.map(rn => {
            const badge = rn.deployed ? ' ✅' : (rn.significant ? '' : ' ⚠️');
            const tf = rn.tf ? ` · ${rn.tf}` : '';
            const lbl = `${rn.label}${badge}${tf}`.replace(/</g, '&lt;');
            return `<option value="${rn.slug}">${lbl}</option>`;
          }).join('');
          if (prev === '__ALL__' || (prev && runs.some(rn => rn.slug === prev))) strat.value = prev;
          else if (runs.length) strat.value = runs[0].slug;
        }
      } catch (e) { console.error('[bt runs] load failed', e); }
      calendarRender();
    }

    // Fill the metric pills from a backtest run's OWN report card (combo
    // metrics — real Sharpe/PF/etc. for the whole run window, not the visible
    // date sub-range) and relabel the metrics card so the scope is honest.
    function _renderBtMetrics(m, meta) {
      const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
      set('sm-pf', m.profit_factor == null ? '—' : Number(m.profit_factor).toFixed(2));
      set('sm-exp', m.expectancy == null ? '—' : Math.round(m.expectancy).toLocaleString('en-IN'));
      set('sm-sharpe', m.sharpe == null ? '—' : Number(m.sharpe).toFixed(2));
      set('sm-wr', (m.win_rate == null ? 0 : Number(m.win_rate).toFixed(1)) + '%');
      set('sm-n', m.n_trades == null ? 0 : m.n_trades);
      const h = document.getElementById('cal-metrics-heading');
      if (h) {
        const key = meta.combo_key_used || '';
        const nm = (meta.label || meta.slug || 'backtest');
        h.textContent = `📐 Backtest report card — ${nm} (${key}, full run)`;
      }
    }

    // ── Saved Views (strategy-group filters) ────────────────────────────────
    // A View = named set of strategy config-keys. Build from the Total Summary
    // Compare selection → Save. Applying a view filters the WHOLE Stats tab
    // (calendar grid / top gain / equity / points / Total Summary) to that
    // set's COMBINED result. Server-persisted (data/stat_views.json). Live only.
    window._calViews = window._calViews || [];
    window.calActiveView = window.calActiveView || null;

    function _viewEsc(s) { return String(s).replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

    // clean run/strategy label for display — strip verdict emoji + timeframe + trailing separators
    function _cleanViewLabel(s) {
      return String(s)
        .replace(/[✅❌⚠️🧪🏆🔴🟢]/g, '') // ✅❌⚠️🧪🏆🔴🟢
        .replace(/[·•|]\s*\d+\s*m\b/gi, '')      // "· 15m" / "• 5m" timeframe tag
        .replace(/\s+\d+\s*m\s*$/i, '')          // trailing " 15m"
        .replace(/[·•|\-\s]+$/g, '')             // trailing separators
        .replace(/\s{2,}/g, ' ')
        .trim();
    }

    async function loadCalViews() {
      try {
        const r = await fetch('/api/stat-views');
        const j = await r.json();
        window._calViews = j.views || [];
      } catch (e) { window._calViews = []; }
      // one-time outside-click close for the views menu
      if (!window._calViewsClickInit) {
        window._calViewsClickInit = true;
        document.addEventListener('click', (e) => {
          const wrap = document.getElementById('cal-views-wrap');
          const menu = document.getElementById('cal-views-menu');
          if (menu && wrap && !wrap.contains(e.target)) menu.style.display = 'none';
        });
      }
      _renderViewsBtn();
      _renderViewsMenu();
    }

    function _renderViewsBtn() {
      const b = document.getElementById('cal-views-btn');
      if (!b) return;
      if (window.calActiveView) {
        b.innerHTML = `🗂 ${_viewEsc(window.calActiveView.name)} <span onclick="clearCalView(event)" style="margin-left:6px;color:#f85149;font-weight:bold;cursor:pointer">✕</span>`;
        b.style.background = '#1f6feb'; b.style.color = '#fff'; b.style.borderColor = '#1f6feb';
      } else {
        b.textContent = '🗂 Views ▾';
        b.style.background = '#21262d'; b.style.color = '#8b949e'; b.style.borderColor = '#30363d';
      }
    }

    function _renderViewsMenu() {
      const m = document.getElementById('cal-views-menu');
      if (!m) return;
      // views are mode-specific: live views hold strategies, bt views hold run slugs
      const kind = window.calBtMode ? 'bt' : 'live';
      const views = (window._calViews || []).filter(v => (v.kind || 'live') === kind);
      const noun = window.calBtMode ? 'runs' : 'strategies';
      let html = '';
      html += `<div onclick="openStatViewModal(null)" style="padding:6px 12px;cursor:pointer;font-size:11.5px;color:#3fb950;" onmouseover="this.style.background='#21262d'" onmouseout="this.style.background=''">＋ New ${window.calBtMode ? 'Backtest ' : ''}View…</div>`;
      if (window.calActiveView) {
        html += `<div onclick="clearCalView(event)" style="padding:6px 12px;cursor:pointer;font-size:11.5px;color:#8b949e;" onmouseover="this.style.background='#21262d'" onmouseout="this.style.background=''">✕ Clear view</div>`;
      }
      html += `<div style="border-top:1px solid #21262d;margin:4px 0;"></div>`;
      if (!views.length) {
        html += `<div style="padding:6px 12px;font-size:11px;color:#6e7681;line-height:1.5;">Koi view nahi.<br>"＋ New View" se ${noun} select karke banao.</div>`;
      } else {
        views.forEach(v => {
          const active = window.calActiveView && window.calActiveView.id === v.id;
          const tip = _viewEsc((v.strategies || []).map(s => (typeof regLabel === 'function' ? regLabel(s) : s)).join(', '));
          html += `<div style="display:flex;align-items:center;gap:6px;padding:5px 10px;font-size:11.5px;${active ? 'background:#1f6feb22;' : ''}" onmouseover="this.style.background='#21262d'" onmouseout="this.style.background='${active ? '#1f6feb22' : ''}'">
            <span onclick="applyCalView(${v.id})" style="flex:1;cursor:pointer;color:${active ? '#58a6ff' : '#e6edf3'};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${tip}">${_viewEsc(v.name)} <span style="color:#6e7681;font-size:10px">(${(v.strategies || []).length})</span></span>
            <span onclick="openStatViewModal(${v.id})" style="cursor:pointer;color:#8b949e;" title="Edit (naam + strategies)">✏️</span>
            <span onclick="deleteCalView(${v.id},event)" style="cursor:pointer;color:#f85149;" title="Delete">🗑</span>
          </div>`;
        });
      }
      m.innerHTML = html;
    }

    function toggleViewsMenu(ev) {
      if (ev) ev.stopPropagation();
      const m = document.getElementById('cal-views-menu');
      if (!m) return;
      const show = m.style.display !== 'block';
      _renderViewsMenu();
      m.style.display = show ? 'block' : 'none';
    }

    // Full strategy list for the view builder — same set as the top "All
    // strategies" dropdown (order_store distinct strategies, registry-labeled).
    function _allStrategiesForView() {
      const sel = document.getElementById('cal-strat');
      const out = [];
      if (sel) [...sel.options].forEach(o => { if (o.value) out.push({ key: o.value, label: o.textContent }); });
      return out;
    }

    // Open the New/Edit View builder: a checklist of ALL strategies + Select
    // All, pre-checked for the strategies already in the view (edit mode).
    function openStatViewModal(viewId) {
      window._statViewEditId = viewId || null;
      const modal = document.getElementById('stat-view-modal');
      const title = document.getElementById('stat-view-modal-title');
      const nameI = document.getElementById('stat-view-name');
      const list = document.getElementById('stat-view-strat-list');
      if (!modal || !list) return;
      const view = viewId ? (window._calViews || []).find(v => v.id === viewId) : null;
      const checked = new Set(view ? (view.strategies || []) : []);
      const bt = !!window.calBtMode;
      if (title) title.textContent = (view ? '✏️ Edit ' : '＋ New ') + (bt ? 'Backtest View' : 'View');
      const lbl = document.getElementById('stat-view-list-label');
      if (lbl) lbl.textContent = bt ? 'Runs (jo combine karne hain tick karo)' : 'Strategies (jo view me chahiye tick karo)';
      if (nameI) nameI.value = view ? view.name : '';
      const strats = _allStrategiesForView();
      if (!strats.length) {
        list.innerHTML = '<div style="color:#6e7681;font-size:11px;padding:10px">Strategies abhi load nahi hui — Stats tab ek baar refresh karo phir kholo.</div>';
      } else {
        list.innerHTML = strats.map(s => {
          // registry ka canonical "ID · Naam" (apni cleaning nahi) — resolve na ho to cleaned label fallback
          const disp = (window.regId && String(regId(s.key)) !== String(s.key)) ? regFull(s.key) : _cleanViewLabel(s.label);
          const ds = (disp + ' ' + s.key).toLowerCase().replace(/"/g, '&quot;');
          return `<label data-s="${ds}" style="display:flex;align-items:center;gap:8px;padding:6px 6px;cursor:pointer;font-size:12px;color:#e6edf3;border-radius:4px;" onmouseover="this.style.background='#21262d'" onmouseout="this.style.background=''">
          <input type="checkbox" value="${s.key.replace(/"/g, '&quot;')}" ${checked.has(s.key) ? 'checked' : ''} style="accent-color:#1f6feb;width:14px;height:14px;margin:0;flex:none;">
          <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_viewEsc(disp)}</span>
          <span style="flex:none;color:#6e7681;font-size:9.5px;white-space:nowrap;">${_viewEsc(s.key)}</span>
        </label>`;
        }).join('');
      }
      const srch = document.getElementById('stat-view-search'); if (srch) { srch.value = ''; }
      const menu = document.getElementById('cal-views-menu'); if (menu) menu.style.display = 'none';
      modal.style.display = 'flex';
      if (srch) setTimeout(() => srch.focus(), 30);
    }

    // filter the run/strategy checklist by typed text (matches label OR key)
    function statViewFilter(q) {
      const t = (q || '').trim().toLowerCase();
      document.querySelectorAll('#stat-view-strat-list label').forEach(l => {
        l.style.display = (!t || (l.dataset.s || '').indexOf(t) >= 0) ? '' : 'none';
      });
    }

    function closeStatViewModal() {
      const m = document.getElementById('stat-view-modal'); if (m) m.style.display = 'none';
      window._statViewEditId = null;
    }

    function statViewCheckAll(all) {
      document.querySelectorAll('#stat-view-strat-list input[type=checkbox]').forEach(c => { c.checked = all; });
    }

    async function saveStatViewModal() {
      const name = (document.getElementById('stat-view-name').value || '').trim();
      if (!name) { if (typeof toast === 'function') toast('View ka naam do'); return; }
      const checked = [...document.querySelectorAll('#stat-view-strat-list input[type=checkbox]:checked')].map(c => c.value);
      if (!checked.length) { if (typeof toast === 'function') toast('Kam se kam ek strategy select karo'); return; }
      const editId = window._statViewEditId;
      try {
        const url = editId ? ('/api/stat-views/' + editId) : '/api/stat-views';
        const payload = { name: name, strategies: checked };
        if (!editId) payload.kind = window.calBtMode ? 'bt' : 'live';   // new view = current mode's kind
        const r = await fetch(url, { method: editId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const j = await r.json();
        if (!j.ok) { if (typeof toast === 'function') toast('Save fail: ' + (j.error || '')); return; }
        await loadCalViews();
        closeStatViewModal();
        if (typeof toast === 'function') toast('✅ View ' + (editId ? 'updated' : 'saved') + ': ' + name);
        if (editId) {
          if (window.calActiveView && window.calActiveView.id === editId) { window.calActiveView = j.view; _renderViewsBtn(); calendarRender(); }
        } else {
          applyCalView(j.view.id);   // new view → auto-apply
        }
      } catch (e) { if (typeof toast === 'function') toast('Save fail'); }
    }

    function applyCalView(id) {
      const v = (window._calViews || []).find(x => x.id === id);
      if (!v) return;
      window.calActiveView = v;
      // Live: multi-strategy filter → clear single-strategy dropdown (server
      // returns all, calendarRender narrows). Backtest: leave the run dropdown
      // as-is (the view supplies its own comma-joined slugs; no 'All' option).
      const strat = document.getElementById('cal-strat'); if (strat && !window.calBtMode) strat.value = '';
      const menu = document.getElementById('cal-views-menu'); if (menu) menu.style.display = 'none';
      _renderViewsBtn();
      calendarRender();
    }

    function clearCalView(ev) {
      if (ev) ev.stopPropagation();
      window.calActiveView = null;
      const menu = document.getElementById('cal-views-menu'); if (menu) menu.style.display = 'none';
      _renderViewsBtn();
      calendarRender();
    }

    async function deleteCalView(id, ev) {
      if (ev) ev.stopPropagation();
      const v = (window._calViews || []).find(x => x.id === id);
      if (!v) return;
      if (!confirm(`View "${v.name}" delete karein?`)) return;
      try {
        const r = await fetch('/api/stat-views/' + id, { method: 'DELETE' });
        const j = await r.json();
        if (!j.ok) { if (typeof toast === 'function') toast('Delete fail'); return; }
        if (window.calActiveView && window.calActiveView.id === id) { window.calActiveView = null; _renderViewsBtn(); calendarRender(); }
        await loadCalViews();
      } catch (e) { if (typeof toast === 'function') toast('Delete fail'); }
    }

    // View-scoped metric pills (server stats route is single-strategy only, so a
    // multi-strategy view is computed client-side from the filtered trades).
    function _renderViewMetrics(trades, view) {
      const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
      let gWin = 0, gLoss = 0, wins = 0, n = 0, netSum = 0; const byDay = {};
      (trades || []).forEach(t => {
        const net = (t.pnl != null) ? t.pnl : 0; n++; netSum += net;
        if (net > 0) { gWin += net; wins++; } else if (net < 0) { gLoss += -net; }
        const d = t.entry_date || t.exit_date; if (d) byDay[d] = (byDay[d] || 0) + net;
      });
      const pf = gLoss > 0 ? gWin / gLoss : (gWin > 0 ? Infinity : 0);
      const days = Object.values(byDay); let sharpe = null;
      if (days.length > 1) {
        const m = days.reduce((a, b) => a + b, 0) / days.length;
        const sd = Math.sqrt(days.reduce((a, b) => a + (b - m) * (b - m), 0) / (days.length - 1));
        if (sd > 0) sharpe = (m / sd) * Math.sqrt(252);
      }
      set('sm-pf', isFinite(pf) ? pf.toFixed(2) : '∞');
      set('sm-exp', n > 0 ? Math.round(netSum / n).toLocaleString('en-IN') : '—');
      set('sm-sharpe', sharpe == null ? '—' : sharpe.toFixed(2));
      set('sm-wr', n > 0 ? Math.round(wins / n * 100) + '%' : '0%');
      set('sm-n', n);
      const h = document.getElementById('cal-metrics-heading');
      if (h) h.textContent = `📐 Metrics — 🗂 ${view.name} (${(view.strategies || []).length} strategies, combined)`;
    }

    function calPrevMonth() {
      calMonth--;
      if (calMonth < 0) { calMonth = 11; calYear--; }
      calClearDateRange();
    }

    function calNextMonth() {
      calMonth++;
      if (calMonth > 11) { calMonth = 0; calYear++; }
      calClearDateRange();
    }

    function calGoToday() {
      const today = new Date();
      calYear = today.getFullYear();
      calMonth = today.getMonth();
      calClearDateRange();
    }

    // Default date range: 21 Jun 2026 (go-live) → today (dynamic). Applied once
    // per page load; user can still change or clear it afterwards.
    const _CAL_DEFAULT_FROM = '2026-06-21';
    function _ensureDefaultDateRange() {
      if (window._calDefaultRangeApplied) return;
      window._calDefaultRangeApplied = true;
      const f = document.getElementById('cal-range-from');
      const t = document.getElementById('cal-range-to');
      if (!f || !t || f.value || t.value) return;   // already set → respect it
      const now = new Date();
      const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
      f.value = _CAL_DEFAULT_FROM;
      t.value = today;
      window._calRangeMode = true;
    }

    function calApplyDateRange() {
      const from = (document.getElementById('cal-range-from') || {}).value;
      const to = (document.getElementById('cal-range-to') || {}).value;
      if (!from && !to) { calendarRender(); return; }
      // If only one side is set, auto-fill the other
      if (from && !to) {
        const d = document.getElementById('cal-range-to');
        if (d) d.value = from;
      }
      if (!from && to) {
        const d = document.getElementById('cal-range-from');
        if (d) d.value = to;
      }
      window._calRangeMode = true;
      calendarRender();
    }

    function calClearDateRange() {
      const f = document.getElementById('cal-range-from');
      const t = document.getElementById('cal-range-to');
      if (f) f.value = '';
      if (t) t.value = '';
      window._calRangeMode = false;
      calendarRender();
    }


    window.calSelectedDateFilter = null;

    function calSelectDate(dateStr) {
      // Set date input in Orders tab as backup
      const dateInput = document.getElementById('ord-date');
      if (dateInput) {
        dateInput.value = dateStr;
        ordersRender();
      }

      // Toggle filter
      if (window.calSelectedDateFilter === dateStr) {
        window.calSelectedDateFilter = null;
      } else {
        window.calSelectedDateFilter = dateStr;
      }

      updateCalSelectedDateBadge();
      window.calPointsCurrentPage = 1;
      renderPointsPerTradeTable();

      // Redraw equity curve for selected day (or all if deselected)
      const allTrades = window.currentCalendarTrades || [];
      const filteredForCurve = window.calSelectedDateFilter
        ? allTrades.filter(t => (t.exit_date || t.entry_date) === window.calSelectedDateFilter)
        : allTrades;
      drawEquityCurveChart('cal-equity-curve-container', filteredForCurve);

      // Scroll smoothly to Point Per Trade Details card
      const container = document.getElementById('cal-points-per-trade-tbody');
      if (container) {
        const card = container.closest('.card');
        if (card) {
          card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }
    }

    function updateCalSelectedDateBadge() {
      const badge = document.getElementById('cal-selected-date-badge');
      if (!badge) return;
      
      let html = '';
      
      if (window.calSelectedDateFilter) {
        html += `<span style="background:#1f6feb20; border:1px solid #1f6feb80; border-radius:4px; color:#58a6ff; padding:2px 6px; font-size:10.5px; font-weight:normal; line-height:1; display:inline-flex; align-items:center; margin-right:4px;">📅 ${window.calSelectedDateFilter} <span style="margin-left:6px; cursor:pointer; font-weight:bold; color:#f85149;" onclick="clearCalSelectedDateFilter(event)">✕</span></span>`;
      }

      if (window.calSelectedPeriodFilter) {
        const _pf = window.calSelectedPeriodFilter;
        const _plbl = _calPeriodLabel(_pf.key, _pf.mode);
        html += `<span style="background:#1f6feb20; border:1px solid #1f6feb80; border-radius:4px; color:#58a6ff; padding:2px 6px; font-size:10.5px; font-weight:normal; line-height:1; display:inline-flex; align-items:center; margin-right:4px;">📅 ${_plbl} <span style="margin-left:6px; cursor:pointer; font-weight:bold; color:#f85149;" onclick="clearCalPeriodFilter(event)">✕</span></span>`;
      }

      if (window.calPnlGraphFilter) {
        html += `<span style="background:#23863620; border:1px solid #23863680; border-radius:4px; color:#3fb950; padding:2px 6px; font-size:10.5px; font-weight:normal; line-height:1; display:inline-flex; align-items:center; margin-right:4px;">📊 ${window.calPnlGraphFilter} <span style="margin-left:6px; cursor:pointer; font-weight:bold; color:#f85149;" onclick="clearCalPnlGraphFilter(event)">✕</span></span>`;
      }

      if (html) {
        badge.innerHTML = html;
        badge.style.display = 'inline-flex';
        badge.style.alignItems = 'center';
        badge.style.background = 'transparent';
        badge.style.border = 'none';
        badge.style.padding = '0';
      } else {
        badge.innerHTML = '';
        badge.style.display = 'none';
      }
    }

    function clearCalSelectedDateFilter(event) {
      if (event) event.stopPropagation();
      window.calSelectedDateFilter = null;
      updateCalSelectedDateBadge();
      window.calPointsCurrentPage = 1;
      renderPointsPerTradeTable();
    }

    function clearCalPnlGraphFilter(event) {
      if (event) event.stopPropagation();
      window.calPnlGraphFilter = null;
      updateCalSelectedDateBadge();
      window.calPointsCurrentPage = 1;
      renderPointsPerTradeTable();
    }

    // ── Total Summary global helpers (accessible from HTML onchange/onclick) ──

    // Aggregation mode for the metric columns (Σ Total / Avg / Min / Max).
    // A single switch replaces the old per-combo columns (Avg Net, Best Net,
    // Max Run-Up, ...) — the base metric is toggled once, the mode picks the
    // aggregation. Metric ids are prefixed 'm_' so they read s.m[base][mode].
    const _CAL_AGG_LBL = { sum: 'Σ', avg: 'Avg', min: 'Min', max: 'Max' };

    function _initCalSumCols() {
      if (!window._calSumAgg) window._calSumAgg = localStorage.getItem('cal_sum_agg') || 'sum';
      if (window._calSumCols) return;
      const saved = JSON.parse(localStorage.getItem('cal_sum_cols') || 'null');
      const defaults = [
        { id: 'label', l: 'Date / Strategy', on: true, fixed: true },
        { id: 'count', l: 'Trades', on: true },
        { id: 'strat_count', l: 'Strategies', on: true },
        { id: 'wl', l: 'W/L', on: true },
        { id: 'winrate', l: 'Win %', on: true },
        // aggregatable base metrics (mode switch applies)
        { id: 'm_points', l: 'Points', on: false },
        { id: 'm_gross', l: 'Gross', on: true },
        { id: 'm_tax', l: 'Tax', on: true },
        { id: 'm_net', l: 'Net', on: true },
        { id: 'm_dur', l: 'Duration', on: false },
        { id: 'm_runup', l: 'Run-Up', on: false },
        { id: 'm_rundown', l: 'Run-Down', on: false },
        { id: 'm_dd', l: 'Drawdown', on: false },
        // optimised "what-if" net — best-fixed & best-aggressive SL/Target
        // (path-aware sim). Off by default; loaded async per range.
        { id: 'm_optfix', l: 'Opt Fixed', on: false, title: 'What-if NET, SAME hold as actual: fixed Target ₹4,000/lot · SL ₹1,000/lot' },
        { id: 'm_optaggr', l: 'Opt Aggr', on: false, title: 'What-if NET, SAME hold as actual: aggressive trail Target ₹6,000/lot · init SL ₹2,500/lot · step ₹100' },
        { id: 'm_optaggeod', l: 'Opt Aggr→EOD', on: false, title: 'What-if NET if ATR SL were REPLACED by the aggressive trail, RIDDEN to 15:15 (init SL ₹1,500/lot, no target cap). Directional — NOT a validated backtest.' },
        { id: 'opt_cov', l: 'Opt Cov', on: false, title: 'How many trades in this row have 1-min bar data (the rest show — and are excluded from the Opt totals)' },
        // fixed-value metrics (mode-independent, single value per group)
        { id: 'exp', l: 'Expectancy', on: false },
        { id: 'maxdd', l: 'Max DD', on: false },
      ];
      const hasNew = defaults.some(d => saved && !(d.id in saved));
      window._calSumCols = (!saved || hasNew)
        ? defaults
        : defaults.map(d => ({ ...d, on: d.fixed ? true : (saved[d.id] ?? d.on) }));
    }

    function _populateCalSumColMenu() {
      _initCalSumCols();
      const menu = document.getElementById('cal-sum-col-menu');
      if (!menu) return;
      menu.innerHTML = window._calSumCols.filter(c => !c.fixed).map(c => `
        <label ${c.title ? `title="${c.title.replace(/"/g, '&quot;')}"` : ''} style="display:flex;align-items:center;gap:8px;padding:5px 12px;cursor:pointer;font-size:11.5px;color:#e6edf3;"
          onmouseenter="this.style.background='#21262d'" onmouseleave="this.style.background=''">
          <input type="checkbox" ${c.on ? 'checked' : ''}
            data-col-id="${c.id}"
            onchange="_calSumColToggle(this)"
            style="accent-color:#1f6feb;width:12px;height:12px;margin:0;">
          ${c.l}
        </label>`).join('');
    }

    function _calSumColToggle(el) {
      _initCalSumCols();
      const colDef = window._calSumCols.find(x => x.id === el.dataset.colId);
      if (colDef) colDef.on = el.checked;
      localStorage.setItem('cal_sum_cols', JSON.stringify(Object.fromEntries(window._calSumCols.map(x => [x.id, x.on]))));
      renderSummaryTable();
    }

    function calSumSeg(el) {
      const parent = document.getElementById('cal-sum-mode');
      if (!parent) return;
      parent.querySelectorAll('span[data-v]').forEach(s => { s.style.background = ''; s.style.color = '#8b949e'; });
      el.style.background = '#1f6feb'; el.style.color = '#fff';
      window._calSumMode = el.dataset.v;
      // Clear strategy filter + compare selection + period/day drill-in when
      // switching modes (period keys differ per mode → stale filter would hide all).
      window.calSumStrategyFilter = null;
      window._calSumSelected = new Set();
      window.calSelectedPeriodFilter = null;
      window.calSelectedDateFilter = null;
      if (typeof updateCalSelectedDateBadge === 'function') updateCalSelectedDateBadge();
      if (typeof _updateCalSumSelectBtn === 'function') _updateCalSumSelectBtn();
      renderSummaryTable();
      if (typeof renderPointsPerTradeTable === 'function') renderPointsPerTradeTable();
      // Equity curve resolution follows this mode (Day/Weekly/Monthly) → redraw.
      if (typeof drawEquityCurveChart === 'function') drawEquityCurveChart('cal-equity-curve-container', window.currentCalendarTrades || []);
    }

    // Aggregation-mode switch (Σ Total / Avg / Min / Max) — one switch drives
    // every metric column, replacing the old per-combo columns. Persisted.
    function calSumAggSeg(el) {
      window._calSumAgg = el.dataset.a;
      localStorage.setItem('cal_sum_agg', el.dataset.a);
      renderSummaryTable();
    }

    // ── Compare combos: multi-select strategies → combined footer total ──────
    // Turn on "Compare", tick any set of strategy rows, and the footer Total
    // recomputes over ONLY that combination (unselected rows dim). Deselect all
    // → back to every strategy. Answers "kaun sa combo mil ke kaam kar raha".
    function calSumToggleSelectMode() {
      window._calSumSelectMode = !window._calSumSelectMode;
      if (!window._calSumSelectMode) window._calSumSelected = new Set();
      // Compare only makes sense per-strategy → auto-switch to Strategy mode.
      if (window._calSumSelectMode && (window._calSumMode || 'day') !== 'strategy') {
        const seg = document.querySelector('#cal-sum-mode span[data-v="strategy"]');
        if (seg) { calSumSeg(seg); }   // note: calSumSeg re-renders + clears selection, so re-open below
        window._calSumSelectMode = true;
        window._calSumSelected = new Set();
      }
      _updateCalSumSelectBtn();
      renderSummaryTable();
      if (typeof renderPointsPerTradeTable === 'function') renderPointsPerTradeTable();
    }

    function _updateCalSumSelectBtn() {
      const b = document.getElementById('cal-sum-select-btn');
      if (!b) return;
      const on = !!window._calSumSelectMode;
      const n = (window._calSumSelected && window._calSumSelected.size) || 0;
      b.style.background = on ? '#1f6feb' : '#21262d';
      b.style.color = on ? '#fff' : '#8b949e';
      b.style.borderColor = on ? '#1f6feb' : '#30363d';
      b.textContent = on ? (n ? `☑ Compare (${n})` : '☑ Pick rows…') : '☑ Compare';
    }

    function _calSumToggleSel(key, ev) {
      if (ev) ev.stopPropagation();
      if (!window._calSumSelected) window._calSumSelected = new Set();
      if (window._calSumSelected.has(key)) window._calSumSelected.delete(key);
      else window._calSumSelected.add(key);
      _updateCalSumSelectBtn();
      renderSummaryTable();
      // Filter the Point-Per-Trade table to the selected combo too.
      renderPointsPerTradeTable();
      // Reflect the combo in the equity curve too.
      const sel = window._calSumSelected;
      const allTrades = window.currentCalendarTrades || [];
      const filtered = (sel && sel.size)
        ? allTrades.filter(t => sel.has(t.strategy || t.strat || 'unknown'))
        : allTrades;
      drawEquityCurveChart('cal-equity-curve-container', filtered);
    }

    function calSumClickRow(key) {
      const mode = window._calSumMode || 'day';
      // In Compare mode, a row click toggles that strategy's selection.
      if (mode === 'strategy' && window._calSumSelectMode) {
        _calSumToggleSel(key, null);
        return;
      }
      if (mode === 'day') {
        calSelectDate(key);
      } else if (mode === 'monthly' || mode === 'weekly') {
        calSelectPeriod(mode, key);
      } else {
        // Strategy mode: filter Points Per Trade table by strategy
        if (window.calSumStrategyFilter === key) {
          window.calSumStrategyFilter = null;
        } else {
          window.calSumStrategyFilter = key;
        }
        renderSummaryTable();
        renderPointsPerTradeTable();
        // Redraw equity curve for filtered trades
        const allTrades = window.currentCalendarTrades || [];
        const filtered = window.calSumStrategyFilter
          ? allTrades.filter(t => (t.strategy || t.strat || 'unknown') === window.calSumStrategyFilter)
          : allTrades;
        drawEquityCurveChart('cal-equity-curve-container', filtered);
      }
    }

    // Click a Week/Month row → filter the Point-Per-Trade table (+ equity) to
    // that period (toggle). Same idea as clicking a Day. Period key + mode go
    // through _calGroupKey so it works identically in live & backtest.
    function calSelectPeriod(mode, key) {
      const cur = window.calSelectedPeriodFilter;
      if (cur && cur.mode === mode && cur.key === key) window.calSelectedPeriodFilter = null;
      else window.calSelectedPeriodFilter = { mode, key };
      updateCalSelectedDateBadge();
      window.calPointsCurrentPage = 1;
      renderSummaryTable();               // reflect active-row highlight
      renderPointsPerTradeTable();
      const all = window.currentCalendarTrades || [];
      const pf = window.calSelectedPeriodFilter;
      const filtered = pf ? all.filter(t => _calGroupKey(t, pf.mode) === pf.key) : all;
      drawEquityCurveChart('cal-equity-curve-container', filtered);
      const c = document.getElementById('cal-points-per-trade-tbody');
      if (c) { const card = c.closest('.card'); if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    }

    function clearCalPeriodFilter(event) {
      if (event) event.stopPropagation();
      window.calSelectedPeriodFilter = null;
      updateCalSelectedDateBadge();
      window.calPointsCurrentPage = 1;
      renderSummaryTable();
      renderPointsPerTradeTable();
      drawEquityCurveChart('cal-equity-curve-container', window.currentCalendarTrades || []);
    }

    // sum/avg/min/max over a value array; null-safe (empty → all null so the
    // renderer shows '—' instead of a misleading 0).
    function _agg4(a) {
      if (!a.length) return { sum: null, avg: null, min: null, max: null, n: 0 };
      const sum = a.reduce((x, y) => x + y, 0);
      return { sum, avg: sum / a.length, min: Math.min.apply(null, a), max: Math.max.apply(null, a), n: a.length };
    }

    // ── Optimised "what-if" P&L (Stats page) ────────────────────────────────
    // Fetches per-trade P&L under the two grid-search-optimised SL/Target
    // profiles (best-fixed + best-aggressive) and merges NET values onto each
    // trade as t._optFixNet / t._optAggrNet, then re-renders the Summary + PPT
    // tables. Display-only; loaded async so it never blocks first paint.
    // Gross ₹ from the sim → implied optimised exit → net via the same charge
    // model, so the columns are directly comparable to the Net column.
    function _optNetFromGross(t, grossOpt) {
      const ep = t.entry_price || 0, qty = t.qty || 0, side = t.entry || 'BUY';
      if (!ep || !qty || grossOpt == null) return grossOpt;
      const xp = side === 'SELL' ? ep - grossOpt / qty : ep + grossOpt / qty;
      const tax = (typeof calcCharges === 'function') ? (calcCharges(ep, xp, qty, side) || 0) : 0;
      return grossOpt - tax;
    }

    async function _loadOptimizedPnl(qStr) {
      try {
        const r = await fetch('/api/orders/optimized-pnl?' + qStr);
        const d = await r.json();
        const map = d.map || {};
        window._optParams = d.params || null;
        (window.currentCalendarTrades || []).forEach(t => {
          const o = map[String(t.id)];
          // No-data (or not-yet-loaded) trades → null so they show '—' and are
          // excluded from opt totals (never echo actual — that made 'no data'
          // look like 'no benefit'). Coverage count tells you how many are real.
          if (!o || !o.covered) {
            t._optFixNet = null; t._optAggrNet = null; t._optAggEodNet = null;
            t._optCov = false; return;
          }
          t._optCov = true;
          t._optFixNet = _optNetFromGross(t, o.fix);
          t._optAggrNet = _optNetFromGross(t, o.aggr);
          t._optAggEodNet = _optNetFromGross(t, o.aggr_eod);
        });
        if (typeof renderSummaryTable === 'function') renderSummaryTable();
        if (typeof renderPointsPerTradeTable === 'function') renderPointsPerTradeTable();
      } catch (e) { /* silent — columns just stay '—' */ }
    }

    function _sumTrades(trades) {
      let count = 0, wins = 0, losses = 0, gross = 0, tax = 0, net = 0;
      let totalPts = 0, best = -Infinity, worst = Infinity;
      let maxRunUp = 0, maxRunDown = 0;
      let totalWinNet = 0, totalLossNet = 0;
      let totalDurMins = 0, durCount = 0;
      const stratSet = new Set();
      // per-trade value arrays feeding the aggregation-mode switch
      // dd = per-trade adverse excursion magnitude (positive) — "kitna against gaya"
      const _arr = { points: [], gross: [], net: [], tax: [], dur: [], runup: [], rundown: [], dd: [], optfix: [], optaggr: [], optaggeod: [] };
      let optCovN = 0;
      trades.forEach(t => {
        // optimised "what-if" net (loaded async); null until /api/optimized-pnl returns
        if (t._optCov) optCovN++;
        if (t._optFixNet != null) _arr.optfix.push(t._optFixNet);
        if (t._optAggrNet != null) _arr.optaggr.push(t._optAggrNet);
        if (t._optAggEodNet != null) _arr.optaggeod.push(t._optAggEodNet);
        const ep = t.entry_price || 0, xp = t.exit_price || 0, qty = t.qty || 0;
        const side = t.entry || 'BUY';
        const pts = side === 'BUY' ? xp - ep : ep - xp;
        const g = t._gross != null ? t._gross : (side === 'BUY' ? (xp - ep) * qty : (ep - xp) * qty);
        const tx = t._tax != null ? t._tax : 0;
        const n = t._net != null ? t._net : g - tx;
        count++; gross += g; tax += tx; net += n; totalPts += pts;
        _arr.points.push(pts); _arr.gross.push(g); _arr.tax.push(tx); _arr.net.push(n);
        if (n > 0) { wins++; totalWinNet += n; }
        else if (n < 0) { losses++; totalLossNet += n; }
        if (n > best) best = n;
        if (n < worst) worst = n;
        // Strategy count
        const strat = t.strategy || t.strat || 'unknown';
        if (strat) stratSet.add(strat);
        // Duration from entry_time/exit_time (HH:MM)
        if (t.entry_time && t.exit_time) {
          const [eh, em] = t.entry_time.split(':').map(Number);
          const [xh, xm] = t.exit_time.split(':').map(Number);
          const dur = (xh * 60 + xm) - (eh * 60 + em);
          if (dur >= 0) { totalDurMins += dur; durCount++; _arr.dur.push(dur); }
        }
        (t.tags || []).forEach(tg => {
          if (tg.startsWith('MAX_LTP:')) {
            const ml = parseFloat(tg.split(':')[1]);
            const ru = side === 'BUY' ? (ml - ep) * qty : (ep - ml) * qty;
            if (ru > maxRunUp) maxRunUp = ru;
            _arr.runup.push(ru);
          }
          if (tg.startsWith('MIN_LTP:')) {
            const ml = parseFloat(tg.split(':')[1]);
            const rd = side === 'BUY' ? (ml - ep) * qty : (ep - ml) * qty;
            if (rd < maxRunDown) maxRunDown = rd;
            _arr.rundown.push(rd);
            _arr.dd.push(Math.max(0, -rd));   // adverse excursion as positive drawdown
          }
        });
      });
      // Real cumulative max-drawdown on the group's net-equity curve (trades
      // time-ordered by exit) — a genuine peak-to-trough, mode-independent.
      let _eq = 0, _peak = 0, _mdd = 0;
      trades.slice().sort((a, b) => {
        const ka = (a.exit_date || '') + ' ' + (a.exit_time || '');
        const kb = (b.exit_date || '') + ' ' + (b.exit_time || '');
        return ka < kb ? -1 : ka > kb ? 1 : 0;
      }).forEach(t => {
        const ep = t.entry_price || 0, xp = t.exit_price || 0, qty = t.qty || 0;
        const side = t.entry || 'BUY';
        const g = t._gross != null ? t._gross : (side === 'BUY' ? (xp - ep) * qty : (ep - xp) * qty);
        const n = t._net != null ? t._net : g - (t._tax != null ? t._tax : 0);
        _eq += n; if (_eq > _peak) _peak = _eq;
        const d = _peak - _eq; if (d > _mdd) _mdd = d;
      });
      const avgWin = wins > 0 ? totalWinNet / wins : 0;
      const avgLoss = losses > 0 ? Math.abs(totalLossNet / losses) : 0;
      const exp = count > 0 ? ((wins / count) * avgWin) - ((losses / count) * avgLoss) : 0;
      const avgDurMins = durCount > 0 ? Math.round(totalDurMins / durCount) : 0;
      return {
        count, wins, losses, gross, tax, net, totalPts,
        avgPts: count > 0 ? totalPts / count : 0, avgGross: count > 0 ? gross / count : 0, avgNet: count > 0 ? net / count : 0,
        best: best === Infinity ? 0 : best, worst: worst === Infinity ? 0 : worst,
        avgWin, avgLoss, maxRunUp, maxRunDown, exp,
        stratCount: stratSet.size, avgDurMins, maxDD: _mdd, optCovN,
        // per-metric {sum,avg,min,max} — the aggregation switch reads m[base][mode]
        m: {
          points: _agg4(_arr.points), gross: _agg4(_arr.gross), net: _agg4(_arr.net),
          tax: _agg4(_arr.tax), dur: _agg4(_arr.dur), runup: _agg4(_arr.runup),
          rundown: _agg4(_arr.rundown), dd: _agg4(_arr.dd),
          optfix: _agg4(_arr.optfix), optaggr: _agg4(_arr.optaggr), optaggeod: _agg4(_arr.optaggeod)
        }
      };
    }

    // Helper: format minutes as Xh Ym or Ym
    function _fmtDur(mins) {
      if (!mins) return '—';
      const h = Math.floor(mins / 60), m = mins % 60;
      return h > 0 ? `${h}h ${m}m` : `${m}m`;
    }

    function _sumRow(label, s, clickKey, isBold) {
      _initCalSumCols();
      const cols = window._calSumCols.filter(c => c.on);
      const b = isBold ? 'font-weight:700;' : '';
      const bg = isBold ? 'background:#161b22;' : '';
      const mode = window._calSumMode || 'day';
      // Compare (multi-select) state for this row
      const selMode = !!window._calSumSelectMode && mode === 'strategy' && !isBold && !!clickKey;
      const selSet = window._calSumSelected || new Set();
      const isSel = selMode && selSet.has(clickKey);
      const anySel = selMode && selSet.size > 0;
      const dim = (anySel && !isSel) ? 'opacity:.32;' : '';
      const _pfsel = window.calSelectedPeriodFilter;
      const isActive = !isBold && (
        (window.calSumStrategyFilter === clickKey && mode === 'strategy') ||
        (!!_pfsel && (mode === 'monthly' || mode === 'weekly') && _pfsel.key === clickKey)
      );
      const activeBg = (isSel || isActive) ? 'background:#1f6feb22;border-left:2px solid #1f6feb;' : '';
      const netC = s.net >= 0 ? '#3fb950' : '#f85149';
      const grossC = s.gross >= 0 ? '#3fb950' : '#f85149';
      const N = n => Math.round(n).toLocaleString('en-IN');
      const wr = s.count > 0 ? Math.round((s.wins / s.count) * 100) : 0;
      const clickable = !isBold && clickKey;
      const cursor = clickable ? 'cursor:pointer;' : '';
      const doHover = clickable && !selMode;   // skip hover restyle in select mode (would clobber dim/selected)
      const hoverIn = doHover ? `this.style.background='${isActive ? '#1f6feb33' : '#161b22'}'` : '';
      const hoverOut = doHover ? `this.style.background='${isActive ? '#1f6feb22' : 'transparent'}'` : '';
      const onclick = clickable ? `onclick="calSumClickRow('${clickKey.replace(/'/g, "\\'")}')"` : '';

      return `<tr style="border-bottom:1px solid #21262d;${bg}${b}${cursor}${activeBg}${dim}" ${onclick}
        ${hoverIn ? `onmouseover="${hoverIn}" onmouseout="${hoverOut}"` : ''}>` +
        cols.map(c => {
          // aggregatable metric column → s.m[base][currentMode], null-safe
          if (c.id.startsWith('m_')) {
            const agg = window._calSumAgg || 'sum';
            const base = c.id.slice(2);
            const o = s.m && s.m[base];
            const v = o ? o[agg] : null;
            if (v == null) return `<td style="padding:8px;text-align:right;color:#6e7681;${b}">—</td>`;
            if (base === 'dur') return `<td style="padding:8px;text-align:center;color:#8b949e;${b}">${_fmtDur(Math.round(v))}</td>`;
            if (base === 'points') return `<td style="padding:8px;text-align:right;color:${v >= 0 ? '#3fb950' : '#f85149'};${b}">${(v >= 0 ? '+' : '') + v.toFixed(1)}</td>`;
            if (base === 'dd') return `<td style="padding:8px;text-align:right;color:#f0883e;${b}">${v > 0 ? '-' + N(v) : '0'}</td>`;
            let col = v >= 0 ? '#3fb950' : '#f85149';   // gross / net
            if (base === 'tax') col = '#8b949e';
            else if (base === 'runup') col = '#3fb950';
            else if (base === 'rundown') col = '#f0883e';
            const pfx = (base === 'runup' && v > 0) ? '+' : '';
            return `<td style="padding:8px;text-align:right;color:${col};${b}">${pfx}${N(v)}</td>`;
          }
          switch (c.id) {
            case 'label': {
              const cb = selMode
                ? `<input type="checkbox" ${isSel ? 'checked' : ''} onclick="_calSumToggleSel('${clickKey.replace(/'/g, "\\'")}', event)" style="accent-color:#1f6feb;width:12px;height:12px;margin:0 7px 0 0;vertical-align:middle;cursor:pointer;">`
                : '';
              return `<td style="padding:8px;color:${(isSel || isActive) ? '#58a6ff' : '#adbac7'};${b}">${cb}${label}</td>`;
            }
            case 'count': return `<td style="padding:8px;text-align:center;color:#8b949e;${b}">${s.count}</td>`;
            case 'strat_count': return `<td style="padding:8px;text-align:center;${b}">${isBold ? (s.stratCount||'—') : (s.stratCount > 1 ? `<span style="background:#1f6feb33;color:#58a6ff;border:1px solid #1f6feb55;border-radius:10px;padding:1px 7px;font-size:10.5px;">${s.stratCount}</span>` : `<span style="color:#6e7681;font-size:10.5px;">${s.stratCount}</span>`)}</td>`;
            case 'wl': return `<td style="padding:8px;text-align:center;color:#8b949e;${b}"><span style="color:#3fb950;font-weight:600">${s.wins}</span> / <span style="color:#f85149;font-weight:600">${s.losses}</span></td>`;
            case 'winrate': return `<td style="padding:8px;text-align:center;color:#8b949e;${b}">${wr}%</td>`;
            case 'opt_cov': return `<td style="padding:8px;text-align:center;color:#8b949e;${b}">${s.optCovN != null ? s.optCovN + '/' + s.count : '—'}</td>`;
            case 'exp': return `<td style="padding:8px;text-align:right;color:${s.exp >= 0 ? '#3fb950' : '#f85149'};${b}">${N(s.exp)}</td>`;
            case 'maxdd': return `<td style="padding:8px;text-align:right;color:#f0883e;${b}">${s.maxDD > 0 ? '-' + N(s.maxDD) : '—'}</td>`;
            default: return '<td></td>';
          }
        }).join('') + '</tr>';
    }

    // ── Custom instant hover-tooltip (native title= is slow/easy-to-miss) ──
