// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // Any element with a data-tip="..." attribute shows a styled box on hover.
    function _initKhTooltip() {
      if (window._khTipReady) return;
      window._khTipReady = true;
      const tip = document.createElement('div');
      tip.id = 'khTooltip';
      tip.style.cssText = 'position:fixed;z-index:99999;max-width:340px;padding:8px 11px;'
        + 'background:#1c2333;border:1px solid #30363d;border-radius:7px;color:#e6edf3;'
        + 'font-size:11.5px;line-height:1.5;box-shadow:0 6px 22px rgba(0,0,0,.55);'
        + 'pointer-events:none;display:none;white-space:normal;';
      document.body.appendChild(tip);
      const show = (el) => {
        const t = el.getAttribute('data-tip');
        if (!t) return;
        tip.textContent = t;
        tip.style.display = 'block';
      };
      const place = (e) => {
        if (tip.style.display !== 'block') return;
        let x = e.clientX + 14, y = e.clientY + 16;
        const w = tip.offsetWidth, h = tip.offsetHeight;
        if (x + w > window.innerWidth - 8) x = e.clientX - w - 14;
        if (y + h > window.innerHeight - 8) y = e.clientY - h - 16;
        tip.style.left = Math.max(6, x) + 'px';
        tip.style.top = Math.max(6, y) + 'px';
      };
      document.addEventListener('mouseover', (e) => {
        const el = e.target.closest && e.target.closest('[data-tip]');
        if (el) show(el);
      });
      document.addEventListener('mousemove', place);
      document.addEventListener('mouseout', (e) => {
        const el = e.target.closest && e.target.closest('[data-tip]');
        if (el) tip.style.display = 'none';
      });
    }

    // param-stability (days-since-core-params-last-changed) — loaded once; the Strategy
    // summary table shows a "·Nd" chip + change-history hover per strategy (like registry).
    window._paramStab = window._paramStab || {};
    (function _loadParamStab() {
      try {
        fetch('/api/param-stability', { cache: 'no-store' }).then(r => r.json()).then(d => {
          if (d && !d.error && typeof d === 'object') {
            window._paramStab = d;
            if ((window.currentCalendarTrades || []).length && typeof renderSummaryTable === 'function') {
              try { renderSummaryTable(); } catch (e) {}
            }
          }
        }).catch(() => {});
      } catch (e) {}
    })();

    function renderSummaryTable() {
      _initKhTooltip();
      _initCalSumCols();
      const cols = window._calSumCols.filter(c => c.on);
      const mode = window._calSumMode || 'day';
      const thead = document.getElementById('cal-sum-thead');
      const tbody = document.getElementById('cal-day-totals-tbody');
      const tfoot = document.getElementById('cal-day-totals-tfoot');
      const trades = window.currentCalendarTrades || [];
      if (!tbody) return;
    if (typeof _updateCalSumSelectBtn === 'function') _updateCalSumSelectBtn();
    // Reflect the active aggregation mode on the Σ/Avg/Min/Max switch.
    const _aggEl = document.getElementById('cal-sum-agg');
    if (_aggEl) _aggEl.querySelectorAll('span[data-a]').forEach(sp => {
      const onNow = sp.dataset.a === (window._calSumAgg || 'sum');
      sp.style.background = onNow ? '#1f6feb' : ''; sp.style.color = onNow ? '#fff' : '#8b949e';
    });
    if (thead) {
      const labelHeader = mode === 'monthly' ? 'Month' : mode === 'weekly' ? 'Week' : mode === 'day' ? 'Date' : 'Strategy';
      const aggPfx = _CAL_AGG_LBL[window._calSumAgg || 'sum'] || 'Σ';
      const sort = window._calSumSort || { col: 'label', dir: 1 };
      const rightAligned = new Set(['m_points', 'm_gross', 'm_net', 'm_tax', 'm_runup', 'm_rundown', 'm_dd', 'm_optfix', 'm_optaggr', 'm_optaggeod', 'maxdd', 'exp']);
      thead.innerHTML = cols.map(c => {
        const a = c.id === 'label' ? 'left' : (c.id === 'm_dur' ? 'center' : (rightAligned.has(c.id) ? 'right' : 'center'));
        const isSort = sort.col === c.id;
        const arrow = isSort ? (sort.dir === 1 ? ' ▲' : ' ▼') : '';
        const hdr = c.id === 'label' ? labelHeader : (c.id.startsWith('m_') ? `${aggPfx} ${c.l}` : c.l);
        const titleAttr = c.title ? ` data-tip="${c.title.replace(/"/g, '&quot;')}"` : '';
        return `<th${titleAttr} style="padding:6px 8px;font-weight:500;text-align:${a};color:${isSort ? '#58a6ff' : '#8b949e'};cursor:${c.title ? 'help' : 'pointer'};user-select:none;white-space:nowrap;"
            onclick="calSumSortBy('${c.id}')"
            onmouseover="this.style.color='#e6edf3'" onmouseout="this.style.color='${isSort ? '#58a6ff' : '#8b949e'}'"
          >${hdr}${arrow}</th>`;
      }).join('');
    }

    // Build grouped data (Monthly / Weekly / Day / Strategy — same for live & backtest)
    const groups = {};
    trades.forEach(t => {
      const key = _calGroupKey(t, mode);
      if (!groups[key]) groups[key] = [];
      groups[key].push(t);
    });

    // Compute stats for each group
    const groupStats = {};
    Object.keys(groups).forEach(k => { groupStats[k] = _sumTrades(groups[k]); });

    // Sort rows
    const sort = window._calSumSort || { col: 'label', dir: 1 };
    const sortVal = (s, col) => {
      if (col && col.startsWith('m_')) {
        const o = s.m && s.m[col.slice(2)];
        const v = o ? o[window._calSumAgg || 'sum'] : null;
        return v == null ? -Infinity : v;
      }
      switch (col) {
        case 'label': return col; // use key directly
        case 'count': return s.count;
        case 'strat_count': return s.stratCount;
        case 'wl': return s.wins;
        case 'winrate': return s.count > 0 ? s.wins / s.count : 0;
        case 'exp': return s.exp;
        case 'maxdd': return s.maxDD;
        default: return 0;
      }
    };

    // date-like modes (day/weekly/monthly) sort newest-first; strategy A→Z
    const _dateMode = (mode === 'day' || mode === 'weekly' || mode === 'monthly');
    const keys = Object.keys(groups).sort((a, b) => {
      if (sort.col === 'label') {
        return _dateMode ? b.localeCompare(a) * sort.dir : a.localeCompare(b) * sort.dir;
      }
      return (sortVal(groupStats[a], sort.col) - sortVal(groupStats[b], sort.col)) * sort.dir;
    });

    let bodyHtml = '';
    if (!keys.length) {
      bodyHtml = `<tr><td colspan="${cols.length}" style="text-align:center;color:#6e7681;padding:12px;">Is month me koi trading day nahi hai</td></tr>`;
      if (tfoot) tfoot.innerHTML = '';
    } else {
      keys.forEach(key => {
        // Strategy mode me `key` = raw config-key (order_store se) — user ko
        // registry ka naam dikhna chahiye. clickKey RAW hi rehta hai (filter/
        // Compare-select usi se match karte hain), sirf label badalta hai.
        // Monthly/Weekly = pretty period label; ye aggregation-only rows hain
        // (clickKey null → non-clickable; day/strategy hi date/strat filter karte).
        const _lbl = (mode === 'strategy') ? regFull(key)
          : (mode === 'monthly' || mode === 'weekly') ? _calPeriodLabel(key, mode)
            : key;
        // every mode clickable: day→date filter, week/month→period filter,
        // strategy→strategy filter (calSumClickRow routes by mode).
        bodyHtml += _sumRow(_lbl, groupStats[key], key, false);
      });
      if (tfoot) {
        const sel = window._calSumSelected;
        const comboMode = window._calSumSelectMode && sel && sel.size && mode === 'strategy';
        const footTrades = comboMode
          ? trades.filter(t => sel.has(t.strategy || t.strat || 'unknown'))
          : trades;
        const all = _sumTrades(footTrades);
        const footLabel = comboMode ? `🎯 Selected Combo (${sel.size})` : 'Total / Avg';
        const footRow = _sumRow(footLabel, all, null, true);
        const footBg = comboMode ? '#1f6feb1a' : '#161b22';
        const footBorder = comboMode ? '#1f6feb' : '#30363d';
        tfoot.innerHTML = footRow.replace('<tr style="', `<tr style="font-weight:700;background:${footBg};border-top:2px solid ${footBorder};`);
      }
    }
    tbody.innerHTML = bodyHtml;
    }

    function calSumSortBy(colId) {
      const prev = window._calSumSort || { col: 'label', dir: 1 };
      window._calSumSort = {
        col: colId,
        dir: prev.col === colId ? -prev.dir : 1
      };
      renderSummaryTable();
    }


    async function calendarRender(keepState = false) {
      // Data-load indicator (stats2 header): bade backtest run / mode-switch pe
      // fetch + heavy render me app freeze lagta tha; spinner batata hai "load ho
      // raha hai". Element sirf /stats2 pe hai → guard. Show ab (await fetch se
      // pehle → browser paint kar leta hai), hide render ke baad.
      const _clgEl = document.getElementById('cal-loading');
      if (_clgEl) _clgEl.style.display = 'inline-flex';
      // Clear day filter on fresh month/filter render
      if (!keepState) {
        window.calSelectedDateFilter = null;
      }
      updateCalSelectedDateBadge();
      _ensureDefaultDateRange();   // first load → 21 Jun 2026 to today
      if (!window._calViewsLoaded) { window._calViewsLoaded = true; if (typeof loadCalViews === 'function') loadCalViews(); }

      const monthLabel = document.getElementById('cal-month-label');
      if (monthLabel) {
        monthLabel.textContent = `${CAL_MONTH_NAMES[calMonth]} ${calYear}`;
      }

      // Get active filters
      const q = new URLSearchParams();
      const fromDate = (document.getElementById('cal-range-from') || {}).value;
      const toDate = (document.getElementById('cal-range-to') || {}).value;
      if (fromDate && toDate) {
        q.set('from_date', fromDate);
        q.set('to_date', toDate);
        const monthLabel = document.getElementById('cal-month-label');
        if (monthLabel) monthLabel.textContent = `${fromDate}  →  ${toDate}`;
      } else if (window.calBtMode) {
        // Backtest default (koi range/month click nahi) = POORA run — top summary +
        // equity poore run ke (heatmap ke jaisa). Month cell click range set karke filter karta hai.
        const monthLabel = document.getElementById('cal-month-label');
        if (monthLabel) monthLabel.textContent = 'Full run';
      } else {
        q.set('year', calYear.toString());
        q.set('month', (calMonth + 1).toString());
      }

      // Backtest view uses a parallel endpoint (runs/<slug>/results.js) with the
      // SAME {summary, trades, filters} shape — only the source switches.
      const btMode = !!window.calBtMode;
      let src = '', mode = '', broker = '';
      const strat = (document.getElementById('cal-strat') || {}).value || '';
      let d = { summary: {}, filters: {} };

      if (btMode) {
        // Backtest view (portfolio) active → combine its runs (comma-joined
        // slugs). Else the single selected run.
        if (window.calActiveView && window.calActiveView.kind === 'bt') {
          const sl = (window.calActiveView.strategies || []).join(',');
          if (sl) q.set('slug', sl);
        } else if (strat === '__ALL__') {
          // "All runs" → combine every available run
          const sl = (window._calBtRuns || []).map(r => r.slug).join(',');
          if (sl) q.set('slug', sl);
        } else if (strat) {
          q.set('slug', strat);
        }
        q.set('pass', _calSegVal('cal-bt-pass') || 'bs');
        q.set('period', _calSegVal('cal-bt-period') || 'full');
        try {
          const qs = q.toString();
          // Backtest data is IMMUTABLE → cache each response for this session;
          // re-selecting a run / pass / period / range is then instant (no
          // refetch). Shallow-clone on hit so downstream d.trades/d.summary
          // reassignment (view filter) can't corrupt the cached entry.
          window._btRespCache = window._btRespCache || new Map();
          if (window._btRespCache.has(qs)) {
            d = Object.assign({}, window._btRespCache.get(qs));
          } else {
            const r = await fetch('/api/backtest/calendar-summary?' + qs);
            d = await r.json();
            window._btRespCache.set(qs, d);
            if (window._btRespCache.size > 24) {   // bound memory
              window._btRespCache.delete(window._btRespCache.keys().next().value);
            }
          }
        } catch (e) { console.error("Backtest calendar load failed:", e); }
      } else {
        src = _calSegVal('cal-src'); if (src && src !== 'hedge') q.set('source', src);
        mode = _calSegVal('cal-mode'); if (mode) q.set('mode', mode);
        if (strat) q.set('strategy', strat);
        broker = (document.getElementById('cal-broker') || {}).value || ''; if (broker) q.set('broker', broker);
        if (src === 'hedge') {
          // For hedge in calendar, we intercept at the backend to filter it
          // efficiently (it processes the calendar summary across all days).
          q.set('source', 'hedge');
        }
        try {
          const r = await fetch('/api/orders/calendar-summary?' + q.toString());
          d = await r.json();
        } catch (e) { console.error("Calendar load failed:", e); }
        // Populate filter dropdowns (strategy & broker) — live/paper only.
        // Strategy = UNIFIED list (all serials, task 04): built from registry+runs,
        // + this response's order_store buckets (manual/untagged). Runs loaded so
        // research/bt-only serials also appear (consistent across modes).
        if (window._calEnsureRuns) { try { await window._calEnsureRuns(); } catch (e) {} }
        if (window._calFillStrat) window._calFillStrat((d.filters || {}).strategy || []);
        else _ordFillSelect('cal-strat', (d.filters || {}).strategy || [], strat, 'All strategies');
        _ordFillSelect('cal-broker', (d.filters || {}).broker || [], broker, 'All brokers');
      }

      // ── Active saved-view filter (multi-strategy group) ──────────────────
      // A saved view = a named set of strategies. When active, filter the whole
      // tab to that set: narrow trades + recompute the per-day summary so the
      // calendar grid, top gain, equity, points and Total Summary all reflect
      // ONLY the view's strategies' COMBINED result. Live/paper only (backtest =
      // one run at a time). Server already returned all trades (strat cleared on
      // apply), so we filter client-side and re-bucket by entry date.
      if (window.calActiveView && !btMode) {
        // Match by registry-resolved identity (regId), not raw string — view me
        // saved key (config_key) aur order_store ki raw row alag case/alias ho
        // sakte hain (rsi_v1_PAPER vs rsi_v1_paper, ema920 = ema_v1). Server-side
        // single-strategy filter bhi ab yahi resolve-identity use karta hai.
        const _vnorm = k => ((window.regId ? String(regId(k)) : String(k == null ? '' : k))).toLowerCase();
        const _vset = new Set((window.calActiveView.strategies || []).map(_vnorm));
        d.trades = (d.trades || []).filter(t => _vset.has(_vnorm(t.strategy || t.strat || 'unknown')));
        const _vsum = {};
        d.trades.forEach(t => {
          // bucket by EXIT date — same as the server calendar-summary route (TRAP #141:
          // positional/overnight P&L belongs to the day it CLOSES). Using entry_date here
          // put a carried position's P&L on the wrong day → view-on vs view-off mismatch.
          const dt = t.exit_date || t.entry_date;
          if (!dt) return;
          const b = _vsum[dt] || (_vsum[dt] = { pnl: 0, count: 0 });
          b.pnl += (t.pnl || 0); b.count++;
        });
        Object.keys(_vsum).forEach(k => { _vsum[k].pnl = Math.round(_vsum[k].pnl * 100) / 100; });
        d.summary = _vsum;
      }

      // Populate exit reason dropdown from the trades — normalize raw reasons to short labels
      const exitReasonSel = document.getElementById('cal-exit-reason');
      if (exitReasonSel) {
        const curPrefix = exitReasonSel.dataset.prefix || '';
        // Map prefix → short label (keep in sync with _exitReasonBadge)
        const _exitPrefixMap = [
          ['SL_HIT', '🛑 Stop-Loss'],
          ['TP_HIT', '🎯 Target'],
          ['EXPIRY_ITM_SQUAREOFF', '📅 Expiry ITM'],
          ['EXPIRY_EOD_SQUAREOFF', '📅 Expiry EOD (2:55)'],
          ['EOD_315_SQUAREOFF', '⏰ 3:15 EOD'],
          ['KILL_FLOOR', '🔒 Kill-Floor'],
          ['TRAILING_PROFIT_LOCK', '🔒 Trailing Lock'],
          ['DEFAULT_TSL_TARGET', '🎯 Aggr-Trail Target'],
          ['DEFAULT_TSL_SL', '🛡️ Aggr-Trail SL'],
          ['RMS_MAXLOSS', '⚠️ RMS Daily Max-Loss'],
          ['RMS_PROFIT_TARGET', '✅ RMS Daily Target'],
          ['NO_PRICE_EMERGENCY_EXIT', '🚨 No-Price Emergency'],
          ['ATR_TRAILING', '📉 ATR Trailing'],
          ['RSI_MIDLINE_EXIT', '↩️ RSI Midline'],
          ['ORB_OVN_NEXTDAY', '🌅 Next-Day Exit (9:20)'],
          ['IDX_TRAIL', '📉 Index Trail SL'],
          ['TRAIL_SL', '📉 Trail SL'],
          ['TARGET', '🎯 Target'],
          ['GLOBAL_CAP', '🚫 Max Trades/Day'],
          ['SQUAREOFF_315', '⏰ 3:15 EOD (Webhook)'],
          ['REVERSAL', '🔄 Reversal'],
          ['TV_EXIT', '📡 TV Exit Signal'],
          ['MANUAL_CLOSE', '✋ Manual Close'],
          ['EXTERNALLY_CLOSED', '🌐 Closed at Broker'],
          ['MANUAL_EXIT_BROKER', '🌐 Closed at Broker'],
        ];
        function _rawToPrefix(raw) {
          if (!raw) return raw;
          for (const [pfx] of _exitPrefixMap) { if (raw.startsWith(pfx)) return pfx; }
          return raw; // unknown → use as-is
        }
        function _prefixToLabel(pfx) {
          for (const [p, l] of _exitPrefixMap) { if (p === pfx) return l; }
          return pfx;
        }
        // Collect unique prefixes from this month's trades
        const seenPrefixes = [...new Set((d.trades || []).map(t => _rawToPrefix(t.exit_reason || '')).filter(Boolean))].sort();
        exitReasonSel.innerHTML = '<option value="">All Exit Reasons</option>' +
          seenPrefixes.map(pfx => `<option value="${pfx}"${pfx === curPrefix ? ' selected' : ''}>${_prefixToLabel(pfx)}</option>`).join('');
        exitReasonSel.dataset.prefix = exitReasonSel.value;
        // Update live filter
        window.calExitReasonFilter = exitReasonSel.value;
      }

      const summary = d.summary || {};

      // ── Calendar tile value basis (Gross / Net / Tax) — day-tile ka number ab
      // equity ⚙ "Value" toggle (window._eqOpts.value) ko follow karta hai, default
      // NET. summary[date].pnl = authoritative NET; per-day tax d.trades se sum hota
      // hai (wahi calcCharges formula jo Point-Per-Trade table use karti) → tiles har
      // jagah consistent. gross = net + tax, tax = sirf charges.
      const _calTileMode = (window._eqOpts && window._eqOpts.value) || 'net';
      const _calDayTax = {};
      (d.trades || []).forEach(t => {
        const dk = t.exit_date || t.entry_date;
        if (!dk) return;
        let tax = 0;
        if (btMode) {
          const g = (t.gross != null) ? t.gross : (t.pnl || 0);
          const n = (t.pnl != null) ? t.pnl : (g - (t.fee || 0));
          tax = g - n;
        } else if (typeof calcCharges === 'function') {
          tax = calcCharges(t.entry_price || 0, t.exit_price || 0, t.qty || 0, t.entry || 'BUY') || 0;
        }
        _calDayTax[dk] = (_calDayTax[dk] || 0) + tax;
      });
      function _calTileVal(dateStr, pnl) {
        // pnl = summary[date].pnl. SEMANTICS DIFFER BY MODE:
        //  • Backtest (bs pass): pnl is already NET → gross = net + tax.
        //  • Live/Paper: pnl is GROSS (order_store _net_rows / _net_rows_chrono emit
        //    raw (exit-entry)*qty, no charges) → net = gross - tax. (Was treating live
        //    pnl as net too, so "Net" tile showed gross and "Gross" tile showed gross+tax.)
        const tax = _calDayTax[dateStr] || 0;
        if (btMode) {
          if (_calTileMode === 'gross') return pnl + tax;
          if (_calTileMode === 'tax') return tax;
          return pnl;                       // net (bt pnl already net)
        }
        if (_calTileMode === 'gross') return pnl;       // live pnl IS gross
        if (_calTileMode === 'tax') return tax;
        return pnl - tax;                   // net = gross - charges
      }
      // repaint-in-place ke liye cache (eqSetValue toggle → koi re-fetch nahi)
      window._calTileCtx = { summary: summary, tax: _calDayTax };

      // Compute monthly stats for summary card
      let grossPnL = 0;
      let totalTrades = 0;
      let winDays = 0;
      let tradingDays = 0;

      Object.keys(summary).forEach(dateStr => {
        const day = summary[dateStr];
        grossPnL += day.pnl || 0;
        totalTrades += day.count || 0;
        tradingDays++;
        if ((day.pnl || 0) > 0) winDays++;
      });

      const winrate = tradingDays > 0 ? Math.round((winDays / tradingDays) * 100) : 0;

      const grossEl = document.getElementById('cal-stat-gross');
      if (grossEl) {
        grossEl.textContent = `${grossPnL >= 0 ? '+' : ''}₹${Math.round(grossPnL).toLocaleString('en-IN')}`;
        grossEl.style.color = grossPnL >= 0 ? '#3fb950' : '#f85149';
      }

      const wrEl = document.getElementById('cal-stat-winrate');
      if (wrEl) {
        wrEl.textContent = `${winrate}%`;
        wrEl.style.color = winrate >= 50 ? '#3fb950' : '#8b949e';
      }

      const cntEl = document.getElementById('cal-stat-count');
      if (cntEl) {
        cntEl.textContent = totalTrades.toString();
      }

      // Profit Factor / Expectancy / Sharpe + grouped list + toggleable table
      if (btMode) {
        // Backtest: fill pills from the run's OWN report card (full-run metrics);
        // grouped list + closed table from this run's trades (so nothing stale).
        _renderBtMetrics(d.metrics || {}, d.meta || {});
        window._statsLastTrades = d.trades || [];
        if (typeof renderStatsGroupedList === 'function') renderStatsGroupedList(d.trades || []);
        if (typeof renderStatsClosedTable === 'function') renderStatsClosedTable(d.trades || []);
      } else if (window.calActiveView) {
        // Saved-view active → pills from the filtered (combined) trades.
        _renderViewMetrics(d.trades || [], window.calActiveView);
        window._statsLastTrades = d.trades || [];
        if (typeof renderStatsGroupedList === 'function') renderStatsGroupedList(d.trades || []);
        if (typeof renderStatsClosedTable === 'function') renderStatsClosedTable(d.trades || []);
      } else {
        const h = document.getElementById('cal-metrics-heading');
        if (h) h.textContent = '📐 Metrics (this month, filtered)';
        const mFrom = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-01`;
        const mTo = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-${String(new Date(calYear, calMonth + 1, 0).getDate()).padStart(2, '0')}`;
        const sfilt = {};
        if (src) sfilt.source = src;
        if (mode) sfilt.mode = mode;
        if (strat) sfilt.strategy = strat;
        if (broker) sfilt.broker = broker;
        statsMetricsRender(mFrom, mTo, sfilt);
      }

      // Render Grid
      const grid = document.getElementById('cal-grid');
      if (!grid) return;
      grid.innerHTML = '';

      const firstDay = new Date(calYear, calMonth, 1).getDay(); // 0 = Sun, 1 = Mon
      const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
      const today = new Date();

      // Mon index format: Mon = 0, Sun = 6
      const toMonIndex = dow => (dow === 0 ? 6 : dow - 1);

      // Narrow/mobile: drop Sat/Sun (5-col Mon–Fri) so tiles don't squeeze —
      // UNLESS this month has weekend trading activity (e.g. a Feb budget special
      // session), in which case keep the full 7-col grid. Desktop always 7-col.
      const _narrow = window.matchMedia('(max-width: 760px)').matches;
      let _wkndAct = false;
      for (let _d = 1; _d <= daysInMonth; _d++) {
        const _dw = new Date(calYear, calMonth, _d).getDay();
        if (_dw === 0 || _dw === 6) {
          const _ds = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-${String(_d).padStart(2, '0')}`;
          if (summary[_ds] && summary[_ds].count > 0) { _wkndAct = true; break; }
        }
      }
      const hideWeekends = _narrow && !_wkndAct;
      const _wk = document.querySelector('.cal-weekdays');
      if (hideWeekends) {
        grid.style.gridTemplateColumns = 'repeat(5, 1fr)';
        if (_wk) { _wk.style.gridTemplateColumns = 'repeat(5, 1fr)'; _wk.classList.add('cal-hide-wknd'); }
      } else {
        grid.style.gridTemplateColumns = '';
        if (_wk) { _wk.style.gridTemplateColumns = ''; _wk.classList.remove('cal-hide-wknd'); }
      }
      // In a 5-col Mon–Fri grid the first day's column = its weekday index
      // (Mon=0..Fri=4); a month starting on a weekend begins at Monday (col 0).
      const startOffset = hideWeekends
        ? ((firstDay === 0 || firstDay === 6) ? 0 : firstDay - 1)
        : toMonIndex(firstDay);

      // re-render once when crossing the mobile breakpoint (rotate/resize)
      if (!window._calWkndMQ) {
        window._calWkndMQ = window.matchMedia('(max-width: 760px)');
        window._calWkndMQ.addEventListener('change', function () {
          if (typeof calendarRender === 'function') calendarRender(true);
        });
      }

      // Add empty cells for offset
      for (let i = 0; i < startOffset; i++) {
        const emptyCell = document.createElement('div');
        emptyCell.className = 'cal-day-cell empty';
        grid.appendChild(emptyCell);
      }

      // Add month days
      for (let dayNum = 1; dayNum <= daysInMonth; dayNum++) {
        const cellDate = new Date(calYear, calMonth, dayNum);
        const dateStr = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-${String(dayNum).padStart(2, '0')}`;
        const dayData = summary[dateStr] || null;
        const isToday = cellDate.toDateString() === today.toDateString();
        const dow = cellDate.getDay();
        const isWeekend = dow === 0 || dow === 6;
        if (hideWeekends && isWeekend) continue;   // narrow: no Sat/Sun tiles
        const holidayName = CAL_MARKET_HOLIDAYS[dateStr];

        const cell = document.createElement('div');
        cell.className = 'cal-day-cell';
        cell.dataset.date = dateStr;                 // bs-shadow overlay hook
        if (isToday) cell.classList.add('today');
        if (holidayName) cell.classList.add('holiday');

        // Color based on the selected value basis (net/gross/tax)
        const _tv = dayData ? _calTileVal(dateStr, dayData.pnl || 0) : 0;
        if (dayData && _tv !== 0) {
          cell.classList.add(_tv > 0 ? 'profit-pos' : 'profit-neg');
        }

        // Cell click action
        cell.addEventListener('click', () => calSelectDate(dateStr));

        // 1. Day number (Top Left)
        const numDiv = document.createElement('div');
        numDiv.className = 'cal-day-num';
        numDiv.textContent = dayNum.toString();
        cell.appendChild(numDiv);

        // 2. Holiday Label if any
        if (holidayName) {
          const holDiv = document.createElement('div');
          holDiv.className = 'cal-holiday-lbl';
          holDiv.textContent = holidayName;
          holDiv.title = holidayName;
          cell.appendChild(holDiv);
        }

        // 3. P&L and Trade Count (Center/Bottom)
        if (dayData) {
          const pnlDiv = document.createElement('div');
          pnlDiv.className = `cal-day-pnl ${_tv >= 0 ? 'pos' : 'neg'}`;
          pnlDiv.textContent = `${_tv >= 0 ? '+' : ''}₹${Math.round(_tv).toLocaleString('en-IN')}`;
          cell.appendChild(pnlDiv);

          const countDiv = document.createElement('div');
          countDiv.className = 'cal-day-count';
          countDiv.textContent = `${dayData.count} trade${dayData.count > 1 ? 's' : ''}`;
          cell.appendChild(countDiv);
        }

        grid.appendChild(cell);
      }

      // Real vs BS premium overlay (bs/compare mode) — re-decorate after each render
      if (!btMode && typeof bsRefresh === 'function') { bsRefresh(); }

      // Save trades in window object for resize events
      window.currentCalendarTrades = d.trades || [];
      window.currentCalendarTrades.forEach(t => {
        if (btMode) {
          // Backtest pnl is already NET (real Zerodha charges baked in by the BS
          // pass); use the run's own gross/fee/pnl so the tables match the grid
          // exactly — no client-side charge re-estimate.
          t._gross = (t.gross != null) ? t.gross : (t.pnl || 0);
          t._net = (t.pnl != null) ? t.pnl : (t._gross - (t.fee || 0));
          // Tax = gross − net (always). Some runs record fee=0 or omit it even
          // though gross≠net (vrp_condor: fee 0 but real charge = gross−net;
          // meanrev: no gross/fee → gross falls back to pnl → tax 0, honest "no
          // charge modelled"). Deriving from gross−net shows the real charge
          // wherever gross is known and never breaks grid==table (net == pnl).
          t._tax = t._gross - t._net;
        } else {
          const ep = t.entry_price || 0, xp = t.exit_price || 0, qt = t.qty || 0;
          t._gross = ep && xp && qt ? (t.entry === 'BUY' ? xp - ep : ep - xp) * qt : (t.pnl || 0);
          t._tax = ep && xp && qt ? (calcCharges(ep, xp, qt, t.entry) || 0) : 0;
          t._net = t._gross - t._tax;
        }
      });

      // Backtest heatmap = current run's own trades (live path fetches its own endpoint)
      if (btMode && typeof window._s2Heat === 'function') window._s2Heat();

      // Optimised "what-if" numbers (Opt Fixed / Opt Aggr columns) — async,
      // same range/filter params as calendar-summary; re-renders when it lands.
      // Live/paper only — backtest pnl is already the deployable number.
      if (!btMode) _loadOptimizedPnl(q.toString());

      // Draw Equity Curve Chart
      drawEquityCurveChart('cal-equity-curve-container', window.currentCalendarTrades);

      // Group trades by date and compute stats
      const dayStats = {};
      const trades = d.trades || [];
      trades.forEach(t => {
        const dateStr = t.exit_date || t.entry_date;
        if (!dateStr) return;
        if (!dayStats[dateStr]) {
          dayStats[dateStr] = { count: 0, wins: 0, losses: 0, tax: 0, gross: 0, net: 0 };
        }

        const stat = dayStats[dateStr];
        stat.count++;

        const ep = t.entry_price || 0;
        const xp = t.exit_price || 0;
        const qty = t.qty || 0;
        const entrySide = t.entry || 'BUY';

        // Calculate gross
        let gross = t.pnl;
        if (gross == null) {
          gross = (xp - ep) * qty;
          if (entrySide === 'SELL') gross = (ep - xp) * qty;
        }

        // Calculate charges
        let tax = 0;
        if (typeof calcCharges === 'function') {
          tax = calcCharges(ep, xp, qty, entrySide) || 0;
        }

        const net = gross - tax;

        stat.gross += gross;
        stat.tax += tax;
        stat.net += net;

        if (net > 0) {
          stat.wins++;
        } else if (net < 0) {
          stat.losses++;
        }
      });

      // Render Day Totals table
      _initCalSumCols();
      _populateCalSumColMenu();
      renderSummaryTable();

      // Reset pagination page to 1 on fresh load
      if (!keepState) {
        window.calPointsCurrentPage = 1;
      }
      // Render Point Per Trade table with pagination and grouping
      renderPointsPerTradeTable();

      if (_clgEl) _clgEl.style.display = 'none';   // data + render done → spinner off
    }


    window._calPointsSortCol = localStorage.getItem('cal_points_sort_col') || '';
    window._calPointsSortDir = localStorage.getItem('cal_points_sort_dir') || 'desc';

    function toggleCalPointsSort(colId) {
      if (window._calPointsSortCol === colId) {
        window._calPointsSortDir = window._calPointsSortDir === 'desc' ? 'asc' : 'desc';
      } else {
        window._calPointsSortCol = colId;
        window._calPointsSortDir = 'desc';
      }
      localStorage.setItem('cal_points_sort_col', window._calPointsSortCol);
      localStorage.setItem('cal_points_sort_dir', window._calPointsSortDir);
      renderPointsPerTradeTable();
    }

    function setCalExitReasonFilter(reason) {
      window.calSelectedExitReasonFilter = reason;
      updateCalSelectedDateBadge();
      renderPointsPerTradeTable();
    }

    function renderPointsPerTradeTable() {
      const cols = (window._calPointsCols || []).filter(c => c.on);
      const pointsBody = document.getElementById('cal-points-per-trade-tbody');
      if (!pointsBody) return;

      if (!window.currentCalendarTrades) {
        pointsBody.innerHTML = `<tr><td colspan="${cols.length}" style="text-align:center;color:#6e7681;padding:12px;">No data</td></tr>`;
        return;
      }

      let tradesList = [...window.currentCalendarTrades];

      const searchInput = document.getElementById('cal-symbol-search');
      if (searchInput && searchInput.value) {
        const query = searchInput.value.toUpperCase();
        tradesList = tradesList.filter(t => (t.sym || t.symbol || '').toUpperCase().includes(query));
      }
      const tagInput = document.getElementById('cal-tag-search');
      if (tagInput && tagInput.value) {
        const queryTags = tagInput.value.toUpperCase().split(',').map(s => s.trim()).filter(s => s);
        if (queryTags.length > 0) {
          tradesList = tradesList.filter(t => {
            const tradeTags = (t.tags || []).map(tg => String(tg).toUpperCase());
            return queryTags.every(qt => tradeTags.some(tt => tt.includes(qt)));
          });
        }
      }

      if (window.calSelectedDateFilter) {
        tradesList = tradesList.filter(t => (t.exit_date || t.entry_date) === window.calSelectedDateFilter);
      }

      // Week/Month row click → filter to that period (via _calGroupKey)
      if (window.calSelectedPeriodFilter && typeof _calGroupKey === 'function') {
        const _pf = window.calSelectedPeriodFilter;
        tradesList = tradesList.filter(t => _calGroupKey(t, _pf.mode) === _pf.key);
      }

      if (window.calExitReasonFilter) {
        tradesList = tradesList.filter(t => (t.exit_reason || '').startsWith(window.calExitReasonFilter));
      }

      if (window.calSumStrategyFilter) {
        tradesList = tradesList.filter(t => (t.strategy || t.strat || 'unknown') === window.calSumStrategyFilter);
      }

      // Compare mode: filter to the selected strategy combo
      const _combo = window._calSumSelected;
      if (window._calSumSelectMode && _combo && _combo.size) {
        tradesList = tradesList.filter(t => _combo.has(t.strategy || t.strat || 'unknown'));
      }

      const graphTrades = [...tradesList]; // unfiltered by pnl graph click

      if (window.calPnlGraphFilter && window.calPnlGraphMode) {
        tradesList = tradesList.filter(t => {
          const sym = t.sym || t.symbol || 'UNKNOWN';
          if (window.calPnlGraphMode === 'instrument') {
            let base = sym;
            if (sym.includes('-')) {
              base = sym.split('-')[0];
            } else {
              const match = sym.match(/^[a-z&]+/i);
              if (match) base = match[0].toUpperCase();
            }
            return base === window.calPnlGraphFilter;
          } else {
            return sym === window.calPnlGraphFilter;
          }
        });
      }

      if (typeof _sortData === 'function' && window._calPointsSortCol) {
        _sortData(tradesList, window._calPointsSortCol, window._calPointsSortDir);
      } else {
        tradesList.sort((a, b) => {
          const da = a.entry_date + ' ' + (a.entry_time || '00:00');
          const db = b.entry_date + ' ' + (b.entry_time || '00:00');
          return db.localeCompare(da);
        });
      }
      const sortedTrades = tradesList;

      const _chartList = sortedTrades.map(t => ({
        sym: t.sym || t.symbol,
        side: t.entry,
        entry: t.entry_price,
        exit: t.exit_price,
        et: t.entry_time,
        xt: t.exit_time,
        qty: t.qty,
        date: t.exit_date || t.entry_date,
        strategy: t.strategy || ''
      }));
      localStorage.setItem('chartTradeList', JSON.stringify(_chartList));
      if (typeof window.renderPnlGraph === 'function') window.renderPnlGraph(graphTrades);

      // Render table headers with sorting
      const theadRow = document.getElementById('cal-points-cols-thead');
      if (theadRow) {
        theadRow.innerHTML = cols.map(c => {
          let align = 'left';
          if (c.a === 'center') align = 'center';
          if (c.a === 'right') align = 'right';

          let sortIndicator = '';
          if (window._calPointsSortCol === c.id) {
            sortIndicator = window._calPointsSortDir === 'desc' ? ' ▼' : ' ▲';
          }
          const onclickAttr = c.id !== 'actions' ? ` onclick="toggleCalPointsSort('${c.id}')"` : '';
          const titleAttr = c.title ? ` data-tip="${c.title.replace(/"/g, '&quot;')}"` : '';
          _initKhTooltip();
          return `<th style="padding:6px; font-weight:500; text-align:${align}; cursor:${c.title ? 'help' : 'pointer'}; user-select:none;"${onclickAttr}${titleAttr}>${c.l}${sortIndicator}</th>`;
        }).join('');
      }

      if (sortedTrades.length === 0) {
        pointsBody.innerHTML = `<tr><td colspan="${cols.length}" style="text-align:center;color:#6e7681;padding:12px;">Is filter pe koi trade details nahi hain</td></tr>`;
        const wrap = document.getElementById('cal-points-pagination-wrap');
        if (wrap) wrap.innerHTML = '';
        return;
      }

      const isGroupEnabled = localStorage.getItem('cal_group_symbol') === 'true';
      let displayItems = [];

      if (isGroupEnabled) {
        const dateGroups = {};
        sortedTrades.forEach(t => {
          const dStr = t.exit_date || t.entry_date || '—';
          const sStr = t.sym || t.symbol || '—';
          if (!dateGroups[dStr]) dateGroups[dStr] = {};
          if (!dateGroups[dStr][sStr]) dateGroups[dStr][sStr] = [];
          dateGroups[dStr][sStr].push(t);
        });

        const seenGroups = new Set();
        sortedTrades.forEach(t => {
          const dStr = t.exit_date || t.entry_date || '—';
          const sStr = t.sym || t.symbol || '—';
          const grpKey = dStr + '||' + sStr;
          if (seenGroups.has(grpKey)) return;
          seenGroups.add(grpKey);

          const groupTrades = dateGroups[dStr][sStr];
          if (groupTrades.length > 1) {
            displayItems.push({ type: 'group', date: dStr, symbol: sStr, trades: groupTrades });
          } else {
            displayItems.push({ type: 'single', date: dStr, trade: groupTrades[0] });
          }
        });
      } else {
        sortedTrades.forEach(t => {
          displayItems.push({ type: 'single', date: t.exit_date || t.entry_date || '—', trade: t });
        });
      }

      let pointsHtml = '';
      let currentGroupDate = '';
      let currentMonth = '';

      let _tot = { g: 0, tx: 0, n: 0, inv: 0, pts: 0, of: 0, oa: 0, oe: 0, ocov: 0 };
      let _dayTot = { g: 0, tx: 0, n: 0, inv: 0, pts: 0, count: 0, of: 0, oa: 0, oe: 0, ocov: 0 };
      let _monTot = { g: 0, tx: 0, n: 0, inv: 0, pts: 0, count: 0, of: 0, oa: 0, oe: 0, ocov: 0 };
      // accumulate optimised what-if — ONLY covered (simulate-able) trades; no-data
      // legs are excluded (never echoed as net). ocov = how many were covered.
      const _accOpt = (bucket, t) => {
        if (!t._optCov) return;
        bucket.ocov++;
        bucket.of += (t._optFixNet || 0);
        bucket.oa += (t._optAggrNet || 0);
        bucket.oe += (t._optAggEodNet || 0);
      };

      const renderDayTotalRow = () => {
        if (_dayTot.count === 0) return '';
        const _dTotRetPct = _dayTot.inv > 0 ? ((_dayTot.n / _dayTot.inv) * 100).toFixed(2) + '%' : '—';
        let row = '<tr style="border-top:1px dashed #30363d; background:#161b22; font-weight:600;">';
        cols.forEach((c, idx) => {
          let val = '';
          let colorStyle = '';
          if (idx === 0) {
            val = 'Day Total';
            colorStyle = 'color:#8b949e; text-align:right; font-style:italic;';
          } else {
            switch (c.id) {
              case 'points':
                val = (_dayTot.pts >= 0 ? '+' : '') + _dayTot.pts.toFixed(2);
                colorStyle = 'color:' + (_dayTot.pts >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              case 'gross':
                val = Math.round(_dayTot.g);
                colorStyle = 'color:' + (_dayTot.g >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              case 'tax':
                val = '−' + Math.round(_dayTot.tx);
                colorStyle = 'color:#8b949e;';
                break;
              case 'net':
                val = (Math.round(_dayTot.n) > 0 ? '+' : '') + Math.round(_dayTot.n);
                colorStyle = 'color:' + (_dayTot.n >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              case 'opt_fixed':
              case 'opt_aggr':
              case 'opt_aggr_eod': {
                if (!_dayTot.ocov) { val = '—'; colorStyle = 'color:#6e7681;'; break; }
                const ov = c.id === 'opt_fixed' ? _dayTot.of : (c.id === 'opt_aggr' ? _dayTot.oa : _dayTot.oe);
                val = (Math.round(ov) > 0 ? '+' : '') + Math.round(ov);
                colorStyle = 'color:' + (ov >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              }
              case 'ret_pct':
                val = _dTotRetPct;
                colorStyle = 'color:' + (_dayTot.n >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
            }
          }
          let align = 'left';
          if (c.a === 'center') align = 'center';
          if (c.a === 'right') align = 'right';
          row += `<td class="${['gross','net','opt_fixed','opt_aggr','opt_aggr_eod'].includes(c.id)?'pm-pnl':''}" style="padding:6px; text-align:${idx===0?'right':align}; ${colorStyle}">${val}</td>`;
        });
        row += '</tr>';
        return row;
      };

      const _monthLabel = (ym) => {
        try {
          const p = ym.split('-');
          return new Date(p[0], p[1] - 1, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
        } catch (e) { return ym; }
      };

      const renderMonthTotalRow = () => {
        if (_monTot.count === 0) return '';
        const _mRetPct = _monTot.inv > 0 ? ((_monTot.n / _monTot.inv) * 100).toFixed(2) + '%' : '—';
        let row = '<tr style="border-top:2px solid #3d444d; background:#1c2333; font-weight:700;">';
        cols.forEach((c, idx) => {
          let val = '';
          let colorStyle = '';
          if (idx === 0) {
            val = '🗓️ ' + _monthLabel(currentMonth) + ' Total';
            colorStyle = 'color:#c9a227; text-align:right;';
          } else {
            switch (c.id) {
              case 'points':
                val = (_monTot.pts >= 0 ? '+' : '') + _monTot.pts.toFixed(2);
                colorStyle = 'color:' + (_monTot.pts >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              case 'gross':
                val = Math.round(_monTot.g);
                colorStyle = 'color:' + (_monTot.g >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              case 'tax':
                val = '−' + Math.round(_monTot.tx);
                colorStyle = 'color:#8b949e;';
                break;
              case 'net':
                val = (Math.round(_monTot.n) > 0 ? '+' : '') + Math.round(_monTot.n);
                colorStyle = 'color:' + (_monTot.n >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              case 'opt_fixed':
              case 'opt_aggr':
              case 'opt_aggr_eod': {
                if (!_monTot.ocov) { val = '—'; colorStyle = 'color:#6e7681;'; break; }
                const ov = c.id === 'opt_fixed' ? _monTot.of : (c.id === 'opt_aggr' ? _monTot.oa : _monTot.oe);
                val = (Math.round(ov) > 0 ? '+' : '') + Math.round(ov);
                colorStyle = 'color:' + (ov >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              }
              case 'ret_pct':
                val = _mRetPct;
                colorStyle = 'color:' + (_monTot.n >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
            }
          }
          let align = 'left';
          if (c.a === 'center') align = 'center';
          if (c.a === 'right') align = 'right';
          row += `<td class="${['gross','net','opt_fixed','opt_aggr','opt_aggr_eod'].includes(c.id)?'pm-pnl':''}" style="padding:7px 6px; text-align:${idx === 0 ? 'right' : align}; ${colorStyle}">${val}</td>`;
        });
        row += '</tr>';
        return row;
      };

      displayItems.forEach((item, itemIdx) => {
        const tradeDate = item.date;
        if (tradeDate !== currentGroupDate) {
          if (currentGroupDate !== '') {
            pointsHtml += renderDayTotalRow();
          }
          const newMonth = (tradeDate || '').slice(0, 7);
          if (currentMonth !== '' && newMonth !== currentMonth) {
            pointsHtml += renderMonthTotalRow();
            _monTot = { g: 0, tx: 0, n: 0, inv: 0, pts: 0, count: 0, of: 0, oa: 0, oe: 0, ocov: 0 };
          }
          currentMonth = newMonth;
          currentGroupDate = tradeDate;
          _dayTot = { g: 0, tx: 0, n: 0, inv: 0, pts: 0, count: 0, of: 0, oa: 0, oe: 0, ocov: 0 };
          let dateText = tradeDate;
          try {
            const parts = tradeDate.split('-');
            if (parts.length === 3) {
              const dObj = new Date(parts[0], parts[1] - 1, parts[2]);
              dateText = dObj.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
            }
          } catch (e) { }

          pointsHtml += `
        <tr style="background:#1c2128; font-weight:600; border-bottom:1px solid #30363d; border-top:1px solid #30363d;">
          <td colspan="${cols.length}" style="padding:8px; color:#58a6ff; font-size:11.5px; font-weight:600;">📅 ${dateText}</td>
        </tr>
      `;
        }

        if (item.type === 'single') {
          const t = item.trade;
          const ep = t.entry_price || 0, xp = t.exit_price || 0, qt = t.qty || 0;
          const p = t.entry === 'BUY' ? (xp - ep) : (ep - xp);
          _tot.g += t._gross || 0; _tot.tx += t._tax || 0; _tot.n += t._net || 0; _tot.inv += qt * ep;
          _tot.pts += p;
          _dayTot.g += t._gross || 0; _dayTot.tx += t._tax || 0; _dayTot.n += t._net || 0; _dayTot.inv += qt * ep;
          _dayTot.pts += p; _dayTot.count++;
          _monTot.g += t._gross || 0; _monTot.tx += t._tax || 0; _monTot.n += t._net || 0; _monTot.inv += qt * ep;
          _monTot.pts += p; _monTot.count++;
          _accOpt(_tot, t); _accOpt(_dayTot, t); _accOpt(_monTot, t);
          pointsHtml += renderSingleTradeRow(item.trade, cols, sortedTrades);
        } else {
          const trades = item.trades;
          trades.forEach(t => {
            const ep = t.entry_price || 0, xp = t.exit_price || 0, qt = t.qty || 0;
            const p = t.entry === 'BUY' ? (xp - ep) : (ep - xp);
            _tot.g += t._gross || 0; _tot.tx += t._tax || 0; _tot.n += t._net || 0; _tot.inv += qt * ep;
            _tot.pts += p;
            _dayTot.g += t._gross || 0; _dayTot.tx += t._tax || 0; _dayTot.n += t._net || 0; _dayTot.inv += qt * ep;
            _dayTot.pts += p; _dayTot.count++;
            _monTot.g += t._gross || 0; _monTot.tx += t._tax || 0; _monTot.n += t._net || 0; _monTot.inv += qt * ep;
            _monTot.pts += p; _monTot.count++;
            _accOpt(_tot, t); _accOpt(_dayTot, t); _accOpt(_monTot, t);
          });
          pointsHtml += renderGroupedRow(item, cols, sortedTrades, itemIdx);
        }
      });

      if (currentGroupDate !== '') {
        pointsHtml += renderDayTotalRow();
      }
      if (_monTot.count > 0) {
        pointsHtml += renderMonthTotalRow();
      }

      const _totRetPct = _tot.inv > 0 ? ((_tot.n / _tot.inv) * 100).toFixed(2) + '%' : '—';
      // Grand TOTAL — frozen at the bottom. sticky must live on the <td>s (a sticky
      // <tr> is ignored by browsers), so each cell carries position:sticky;bottom:0.
      const _stick = 'position:sticky;bottom:0;z-index:10;background:#0f1620;box-shadow:0 -2px 0 #30363d;';
      pointsHtml += '<tr style="font-weight:800;">';

      cols.forEach((c, idx) => {
        let val = '';
        let colorStyle = '';

        if (idx === 0) {
          val = 'TOTAL';
          colorStyle = 'color:#c9d1d9;';
        } else {
          switch (c.id) {
            case 'points':
              val = (_tot.pts >= 0 ? '+' : '') + _tot.pts.toFixed(2);
              colorStyle = 'color:' + (_tot.pts >= 0 ? '#3fb950' : '#f85149') + ';';
              break;
            case 'gross':
              val = Math.round(_tot.g);
              colorStyle = 'color:' + (_tot.g >= 0 ? '#3fb950' : '#f85149') + ';';
              break;
            case 'tax':
              val = '−' + Math.round(_tot.tx);
              colorStyle = 'color:#f85149;';
              break;
            case 'net':
              val = (Math.round(_tot.n) > 0 ? '+' : '') + Math.round(_tot.n);
              colorStyle = 'color:' + (_tot.n >= 0 ? '#3fb950' : '#f85149') + ';';
              break;
            case 'opt_fixed':
            case 'opt_aggr':
            case 'opt_aggr_eod': {
              if (!_tot.ocov) { val = '—'; colorStyle = 'color:#6e7681;'; break; }
              const ov = c.id === 'opt_fixed' ? _tot.of : (c.id === 'opt_aggr' ? _tot.oa : _tot.oe);
              val = (Math.round(ov) > 0 ? '+' : '') + Math.round(ov);
              colorStyle = 'color:' + (ov >= 0 ? '#3fb950' : '#f85149') + ';';
              break;
            }
            case 'ret_pct':
              val = _totRetPct;
              colorStyle = 'color:' + (_tot.n >= 0 ? '#3fb950' : '#f85149') + ';';
              break;
          }
        }

        let align = 'left';
        if (c.a === 'center') align = 'center';
        if (c.a === 'right') align = 'right';
        pointsHtml += `<td style="${_stick}padding:8px 6px;text-align:${align};${colorStyle}">${val}</td>`;
      });
      pointsHtml += '</tr>';

      pointsBody.innerHTML = pointsHtml;

      const wrap = document.getElementById('cal-points-pagination-wrap');
      if (wrap) wrap.innerHTML = '';
    }

    function renderSingleTradeRow(t, cols, sortedTrades, isChild = false, parentId = '') {
      const g = t._gross || 0, tx = t._tax || 0, n = t._net || 0;
      const nc = n > 0 ? '#3fb950' : (n < 0 ? '#f85149' : '#e6edf3');
      const gc = g > 0 ? '#3fb950' : (g < 0 ? '#f85149' : '#e6edf3');

      const inv = (t.qty || 0) * (t.entry_price || 0);
      const retPct = inv > 0 ? ((n / inv) * 100).toFixed(2) + '%' : '—';
      const rc = n > 0 ? '#3fb950' : (n < 0 ? '#f85149' : '#8b949e');
      const pts = t.entry === 'BUY' ? (t.exit_price || 0) - (t.entry_price || 0) : (t.entry_price || 0) - (t.exit_price || 0);
      const ptsC = pts > 0 ? '#3fb950' : (pts < 0 ? '#f85149' : '#8b949e');

      let note = '';
      let sl = '', tp = '';
      let max_pnl = '—', min_pnl = '—';
      if (t.tags) {
        let max_ltp = null, min_ltp = null;
        t.tags.forEach(tg => {
          if (tg.startsWith('NOTE:')) note = tg.substring(5);
          if (tg.startsWith('SL_VAL:')) sl = tg.split(':')[1];
          if (tg.startsWith('TP_VAL:')) tp = tg.split(':')[1];
          if (tg.startsWith('MAX_LTP:')) max_ltp = parseFloat(tg.split(':')[1]);
          if (tg.startsWith('MIN_LTP:')) min_ltp = parseFloat(tg.split(':')[1]);
        });
        if (max_ltp !== null && t.entry_price > 0) {
          let pnl = (max_ltp - t.entry_price) * (t.qty || 0);
          if (t.entry === 'SELL') pnl = (t.entry_price - min_ltp) * (t.qty || 0);
          if (pnl > 0) max_pnl = `<span style="color:#3fb950">${Math.round(pnl).toLocaleString('en-IN')}</span>`;
        }
        if (min_ltp !== null && t.entry_price > 0) {
          let pnl = (min_ltp - t.entry_price) * (t.qty || 0);
          if (t.entry === 'SELL') pnl = (t.entry_price - max_ltp) * (t.qty || 0);
          if (pnl < 0) min_pnl = `<span style="color:#f85149">${Math.round(pnl).toLocaleString('en-IN')}</span>`;
        }
      }

      let encNote = encodeURIComponent(note);
      let imgs = _imgTagsOf(t);
      let encImgs = encodeURIComponent(JSON.stringify(imgs));
      const isNoteHidden = window._hiddenNotes.has(t.id) || localStorage.getItem('global_notes_show') !== 'true';
      let dispNote = '';
      if (note || (imgs && imgs.length)) {
        dispNote = `<div id="note-wrapper-${t.id}" style="${isNoteHidden ? 'display:none;' : ''}">`
          + (note ? `<div style="color:#d29922;font-size:10px;margin-top:4px;white-space:normal;line-height:1.3;max-width:300px" title="${note.replace(/"/g, '&quot;')}">${note.replace(/</g, '&lt;').replace(/\n/g, '<br>')}</div>` : '')
          + _noteThumbs(t.id, imgs)
          + `</div>`;
      }

      const _idx = sortedTrades.indexOf(t);
      const childStyle = isChild ? 'background: #161b22; display: none;' : '';
      const childClass = isChild ? `class="${parentId}-child"` : '';

      let rowHtml = `<tr ${childClass} style="border-bottom:1px solid #21262d; ${childStyle}">` + cols.map(c => {
        let v = '';
        let colorStyle = '';

        switch (c.id) {
          case 'date':
            v = `<span style="white-space:nowrap;">${t.entry_date || t.exit_date || '—'}</span>`;
            colorStyle = 'color:#6e7681;';
            break;
          case 'symbol':
            const isNoteColOn = cols.some(x => x.id === 'note');
            const prefix = isChild ? `<span style="color:#8b949e; margin-right:8px; font-weight:normal;">↳</span>` : '';
            v = `${prefix}<b>${t.sym || t.symbol || '—'}</b>` + (isNoteColOn ? '' : dispNote);
            colorStyle = 'color:#adbac7;';
            break;
          case 'dte': v = (typeof _dteBadge === 'function') ? _dteBadge(t) : (t.dte != null ? t.dte + 'd' : '—'); break;
          case 'tags': v = _ordTags(t); break;
          case 'manual_tags':
            const customTags = (t.tags || []).filter(tg => windowCustomTags && windowCustomTags.includes(tg));
            const manualTagsStr = customTags.map(tg => `<span style="background:#a371f720; border:1px solid #a371f780; color:#d2a8ff; padding:2px 6px; border-radius:4px; font-size:10px; margin-right:4px; display:inline-block; margin-bottom:2px;">${tg}</span>`).join('');
            v = manualTagsStr + `<span onclick="openTagAssignModal(${t.id}, '${encodeURIComponent(JSON.stringify(t.tags || []))}')" style="cursor:pointer; color:#58a6ff; font-size:12px; margin-left:4px; display:inline-block;" title="Assign Manual Tags">+🏷️</span>`;
            break;
          case 'side':
            v = t.entry; colorStyle = 'color:' + (t.entry === 'BUY' ? '#3fb950' : '#f85149') + ';font-weight:600;'; break;
          case 'entry_px': v = Number(t.entry_price || 0).toFixed(2); colorStyle = 'color:#8b949e;'; break;
          case 'exit_px': v = Number(t.exit_price || 0).toFixed(2); colorStyle = 'color:#8b949e;'; break;
          case 'entry_time': v = t.entry_time || '—'; colorStyle = 'color:#6e7681;'; break;
          case 'exit_time': v = t.exit_time || '—'; colorStyle = 'color:#6e7681;'; break;
          case 'exit_reason': v = `<span onclick="event.stopPropagation(); if(typeof setCalExitReasonFilter === 'function') setCalExitReasonFilter('${(t.exit_reason || '').replace(/'/g, "\\'")}')" style="cursor:pointer" title="Click to filter by ${t.exit_reason || ''}">${_exitReasonBadge(t.exit_reason)}</span>`; break;
          case 'duration': v = `<span style="white-space:nowrap;">${_durFmt(t.entry_time, t.exit_time)}</span>`; colorStyle = 'color:#8b949e;'; break;
          case 'qty': v = t.qty ?? '—'; break;
          case 'points': v = (pts >= 0 ? '+' : '') + pts.toFixed(2); colorStyle = 'color:' + ptsC + ';'; break;
          case 'gross': v = Math.round(g); colorStyle = 'color:' + gc + ';'; break;
          case 'tax': v = '−' + Math.round(tx); colorStyle = 'color:#8b949e;'; break;
          case 'net': v = (n > 0 ? '+' : '') + Math.round(n); colorStyle = 'color:' + nc + ';font-weight:600;'; break;
          case 'margin': {
            if (t.margin == null) {
              const _mt = String(t.entry).toUpperCase() === 'SELL'
                ? 'SELL SPAN margin background me compute ho raha hai — thodi der me refresh karo (expired contract pe available nahi)'
                : 'No margin data';
              v = '<span style="color:#6e7681" title="' + _mt + '">—</span>'; break;
            }
            const _est = t.margin_est ? '~' : '';
            v = '<span title="' + (t.margin_est ? 'SELL SPAN margin (Dhan calc — ~estimate of what the broker blocks)' : 'BUY debit = premium × qty (exact capital paid)') + '">'
              + _est + '₹' + Math.round(t.margin).toLocaleString('en-IN') + '</span>';
            colorStyle = 'color:#8b949e;';
            break;
          }
          case 'opt_fixed':
          case 'opt_aggr':
          case 'opt_aggr_eod': {
            const ov = c.id === 'opt_fixed' ? t._optFixNet : (c.id === 'opt_aggr' ? t._optAggrNet : t._optAggEodNet);
            if (ov == null) { v = '<span style="color:#6e7681" title="No 1-min bar data">—</span>'; break; }
            const oc = ov > 0 ? '#3fb950' : (ov < 0 ? '#f85149' : '#8b949e');
            v = (ov > 0 ? '+' : '') + Math.round(ov).toLocaleString('en-IN');
            colorStyle = 'color:' + oc + ';';
            break;
          }
          case 'ret_pct': v = retPct; colorStyle = 'color:' + rc + ';'; break;
          case 'run_up': v = max_pnl; break;
          case 'run_down': v = min_pnl; break;
          case 'cumulative': v = t._cumulative != null ? Math.round(t._cumulative) : '—'; colorStyle = 'color:#e6edf3;'; break;
          case 'chart':
            v = `<button onclick="openTradeChart('${(t.sym || t.symbol || '').replace(/'/g, '')}','${t.entry || ''}',${t.entry_price || 0},${t.exit_price || 0},'${t.entry_time || ''}','${t.exit_time || ''}',${t.qty || 0},'${t.exit_date || t.entry_date || ''}',null,null,${_idx},'${sl}','${tp}','${(t.strategy || '').replace(/'/g, '')}')" title="Premium chart" style="padding:3px 9px;font-size:13px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#58a6ff;cursor:pointer">📈</button>`;
            break;
          case 'note':
            v = `<div style="display:flex; align-items:center; gap:6px;">
               <button onclick="openNoteModal(${t.id}, '${encNote}', '${encImgs}')" style="padding:3px 8px;font-size:11px;background:#21262d;border:1px solid #30363d;border-radius:4px;color:#adbac7;cursor:pointer">📝 Note</button>
               ${dispNote}
             </div>`;
            break;
          case 'actions':
            v = `
          <div class="dropdown">
            <span class="dropdown-trigger" onclick="toggleCalDropdown(event, ${t.id})">⋮</span>
            <div id="cal-dropdown-${t.id}" class="dropdown-content">
              <a href="javascript:void(0)" onclick="openNoteModal(${t.id}, '${encNote}', '${encImgs}')">📝 Edit Note</a>
              <a href="javascript:void(0)" onclick="openTradeChart('${t.sym || t.symbol}','${t.entry}',${t.entry_price},${t.exit_price},'${t.entry_time}','${t.exit_time}',${t.qty},'${t.exit_date || t.entry_date || ''}',null,null,${_idx},'${sl}','${tp}','${(t.strategy || '').replace(/'/g, '')}')">📈 Chart</a>
              <a href="javascript:void(0)" onclick="toggleNoteDesc(${t.id})">≡ƒæü∩╕Å Toggle Note</a>
            </div>
          </div>`;
            break;
          default: v = t[c.id] ?? '—'; break;
        }

        let align = 'left';
        if (c.a === 'center') align = 'center';
        if (c.a === 'right') align = 'right';

        let style = `padding:8px; text-align:${align}; ${colorStyle}`;
        if (c.id === 'exit_time') style += ' white-space:nowrap; padding-left:16px;';
        if (c.id === 'qty') style += ' color:#e6edf3;';

        return `<td style="${style}">${v}</td>`;
      }).join('') + `</tr>`;

      return rowHtml;
    }

    function renderGroupedRow(item, cols, sortedTrades, itemIdx) {
      const parentId = `cal-grp-${itemIdx}`;
      const trades = item.trades;
      const count = trades.length;

      let totalQty = 0;
      let totalPnl = 0;
      let weightedEntrySum = 0;
      let weightedExitSum = 0;
      let totalPoints = 0;
      let totalGross = 0;
      let totalTax = 0;
      let totalNet = 0;
      let totalOptFix = 0, totalOptAggr = 0, totalOptAggEod = 0, optCovN = 0;

      let allBuy = true;
      let allSell = true;
      let notesArr = [];

      trades.forEach(t => {
        const qty = t.qty || 0;
        totalQty += qty;
        totalPnl += t.pnl || 0;
        totalGross += t._gross || 0;
        totalTax += t._tax || 0;
        totalNet += t._net || 0;
        // optimised what-if: sum only the trades we could simulate (covered);
        // no-data legs are excluded (not echoed as net) — optCovN tracks how many
        if (t._optCov) {
          optCovN++;
          totalOptFix += (t._optFixNet || 0);
          totalOptAggr += (t._optAggrNet || 0);
          totalOptAggEod += (t._optAggEodNet || 0);
        }
        weightedEntrySum += (t.entry_price || 0) * qty;
        weightedExitSum += (t.exit_price || 0) * qty;

        const pts = t.entry === 'BUY'
          ? (t.exit_price || 0) - (t.entry_price || 0)
          : (t.entry_price || 0) - (t.exit_price || 0);
        totalPoints += pts;

        if (t.entry === 'BUY') allSell = false;
        if (t.entry === 'SELL') allBuy = false;

        let note = '';
        if (t.tags) {
          t.tags.forEach(tg => {
            if (tg.startsWith('NOTE:')) note = tg.substring(5);
          });
        }
        if (note) notesArr.push(note);
      });

      const avgEntry = totalQty > 0 ? weightedEntrySum / totalQty : 0;
      const avgExit = totalQty > 0 ? weightedExitSum / totalQty : 0;

      const sideText = allBuy ? 'BUY' : (allSell ? 'SELL' : 'BUY/SELL');
      const sideColor = allBuy ? '#3fb950' : (allSell ? '#f85149' : '#8b949e');

      const uniqueNotes = [...new Set(notesArr)];
      const combinedNote = uniqueNotes.join('; ');

      const latestT = trades[0];
      let sl = '', tp = '';
      if (latestT.tags) {
        latestT.tags.forEach(tg => {
          if (tg.startsWith('SL_VAL:')) sl = tg.split(':')[1];
          if (tg.startsWith('TP_VAL:')) tp = tg.split(':')[1];
        });
      }
      const _idx = sortedTrades.indexOf(latestT);

      let encNote = encodeURIComponent(combinedNote);
      let imgs = _imgTagsOf(latestT);
      let encImgs = encodeURIComponent(JSON.stringify(imgs));

      const isNoteHidden = window._hiddenNotes.has(latestT.id) || localStorage.getItem('global_notes_show') !== 'true';
      let dispNote = '';
      if (combinedNote || (imgs && imgs.length)) {
        dispNote = `<div id="note-wrapper-group-${parentId}" style="${isNoteHidden ? 'display:none;' : ''}">`
          + (combinedNote ? `<div style="color:#d29922;font-size:10px;margin-top:4px;white-space:normal;line-height:1.3;max-width:300px" title="${combinedNote.replace(/"/g, '&quot;')}">${combinedNote.replace(/</g, '&lt;').replace(/\n/g, '<br>')}</div>` : '')
          + _noteThumbs(latestT.id, imgs)
          + `</div>`;
      }

      const g = totalGross, tx = totalTax, n = totalNet;
      const nc = n > 0 ? '#3fb950' : (n < 0 ? '#f85149' : '#e6edf3');
      const gc = g > 0 ? '#3fb950' : (g < 0 ? '#f85149' : '#e6edf3');

      let rowHtml = `<tr onclick="toggleCalGroupRows('${parentId}')" style="cursor:pointer; border-bottom:1px solid #30363d; background:#1f6feb0f;" onmouseover="this.style.background='#1f6feb1c'" onmouseout="this.style.background='#1f6feb0f'">` + cols.map(c => {
        let v = '';
        let colorStyle = '';

        switch (c.id) {
          case 'date': v = `<span style="white-space:nowrap;">${latestT.entry_date || latestT.exit_date || '—'}</span>`; colorStyle = 'color:#6e7681;'; break;
          case 'symbol':
            const isNoteColOn = cols.some(x => x.id === 'note');
            v = `<span id="${parentId}-arrow" style="margin-right:8px; cursor:pointer; color:#58a6ff; font-family:monospace; font-weight:bold;">Γû╢</span><b>${item.symbol}</b>` + (isNoteColOn ? '' : dispNote);
            colorStyle = 'color:#adbac7;';
            break;
          case 'tags': v = _ordTags(latestT); break;
          case 'manual_tags':
            const customTags2 = (latestT.tags || []).filter(tg => windowCustomTags && windowCustomTags.includes(tg));
            const manualTagsStr2 = customTags2.map(tg => `<span style="background:#a371f720; border:1px solid #a371f780; color:#d2a8ff; padding:2px 6px; border-radius:4px; font-size:10px; margin-right:4px; display:inline-block; margin-bottom:2px;">${tg}</span>`).join('');
            v = manualTagsStr2 + `<span onclick="openTagAssignModal(${latestT.id}, '${encodeURIComponent(JSON.stringify(latestT.tags || []))}')" style="cursor:pointer; color:#58a6ff; font-size:12px; margin-left:4px; display:inline-block;" title="Assign Manual Tags">+🏷️</span>`;
            break;
          case 'side': v = `<span style="color:${sideColor}; font-weight:600;">${sideText}</span>`; break;
          case 'qty': v = totalQty; break;
          case 'entry_px': v = avgEntry.toFixed(2); colorStyle = 'color:#8b949e;'; break;
          case 'exit_px': v = avgExit.toFixed(2); colorStyle = 'color:#8b949e;'; break;
          case 'points':
            const ptsC = totalPoints > 0 ? '#3fb950' : (totalPoints < 0 ? '#f85149' : '#8b949e');
            v = `<span style="color:${ptsC}; font-weight:600;">${totalPoints >= 0 ? '+' : ''}${totalPoints.toFixed(2)}</span>`;
            break;
          case 'gross': v = Math.round(g); colorStyle = 'color:' + gc + ';'; break;
          case 'tax': v = '−' + Math.round(tx); colorStyle = 'color:#8b949e;'; break;
          case 'net': v = (n > 0 ? '+' : '') + Math.round(n); colorStyle = 'color:' + nc + ';font-weight:600;'; break;
          case 'margin': {
            if (t.margin == null) {
              const _mt = String(t.entry).toUpperCase() === 'SELL'
                ? 'SELL SPAN margin background me compute ho raha hai — thodi der me refresh karo (expired contract pe available nahi)'
                : 'No margin data';
              v = '<span style="color:#6e7681" title="' + _mt + '">—</span>'; break;
            }
            const _est = t.margin_est ? '~' : '';
            v = '<span title="' + (t.margin_est ? 'SELL SPAN margin (Dhan calc — ~estimate of what the broker blocks)' : 'BUY debit = premium × qty (exact capital paid)') + '">'
              + _est + '₹' + Math.round(t.margin).toLocaleString('en-IN') + '</span>';
            colorStyle = 'color:#8b949e;';
            break;
          }
          case 'opt_fixed':
          case 'opt_aggr':
          case 'opt_aggr_eod': {
            if (!optCovN) { v = '<span style="color:#6e7681" title="No bar data in this group">—</span>'; break; }
            const ov = c.id === 'opt_fixed' ? totalOptFix : (c.id === 'opt_aggr' ? totalOptAggr : totalOptAggEod);
            const oc = ov > 0 ? '#3fb950' : (ov < 0 ? '#f85149' : '#8b949e');
            const mark = optCovN < count ? `<span title="${optCovN}/${count} legs have bar data" style="color:#6e7681">·</span>` : '';
            v = mark + (ov > 0 ? '+' : '') + Math.round(ov).toLocaleString('en-IN');
            colorStyle = 'color:' + oc + ';';
            break;
          }
          case 'chart':
            v = `<button onclick="event.stopPropagation(); openTradeChart('${(latestT.sym || latestT.symbol || '').replace(/'/g, '')}','${latestT.entry || ''}',${latestT.entry_price || 0},${latestT.exit_price || 0},'${latestT.entry_time || ''}','${latestT.exit_time || ''}',${latestT.qty || 0},'${latestT.exit_date || latestT.entry_date || ''}',null,null,${_idx},'${sl}','${tp}','${(latestT.strategy || '').replace(/'/g, '')}')" title="Premium chart for latest trade" style="padding:3px 9px;font-size:13px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#58a6ff;cursor:pointer">📈</button>`;
            break;
          case 'note':
            v = `<div style="display:flex; align-items:center; gap:6px;">
               <button onclick="event.stopPropagation(); openNoteModal(${latestT.id}, '${encNote}', '${encImgs}')" style="padding:3px 8px;font-size:11px;background:#21262d;border:1px solid #30363d;border-radius:4px;color:#adbac7;cursor:pointer">📝 Note</button>
               ${dispNote}
             </div>`;
            break;
          case 'actions':
            v = `
          <div class="dropdown" onclick="event.stopPropagation();">
            <span class="dropdown-trigger" onclick="toggleCalDropdown(event, ${latestT.id})">⋮</span>
            <div id="cal-dropdown-${latestT.id}" class="dropdown-content">
              <a href="javascript:void(0)" onclick="openNoteModal(${latestT.id}, '${encNote}', '${encImgs}')">📝 Edit Note</a>
              <a href="javascript:void(0)" onclick="openTradeChart('${latestT.sym || latestT.symbol}','${latestT.entry}',${latestT.entry_price},${latestT.exit_price},'${latestT.entry_time}','${latestT.exit_time}',${latestT.qty},'${latestT.exit_date || latestT.entry_date || ''}',null,null,${_idx},'${sl}','${tp}','${(latestT.strategy || '').replace(/'/g, '')}')">📈 Chart</a>
              <a href="javascript:void(0)" onclick="toggleNoteDesc(${latestT.id})">≡ƒæü∩╕Å Toggle Note</a>
            </div>
          </div>`;
            break;
          case 'exit_time': v = `<span style="font-style:italic; font-size:10.5px; color:#8b949e;">${count} trades</span>`; break;
          default: v = '—'; break;
        }

        let align = 'left';
        if (c.a === 'center') align = 'center';
        if (c.a === 'right') align = 'right';

        let style = `padding:8px; text-align:${align}; ${colorStyle}`;
        if (c.id === 'exit_time') style += ' white-space:nowrap; padding-left:16px;';
        if (c.id === 'qty') style += ' color:#e6edf3;';

        return `<td style="${style}">${v}</td>`;
      }).join('') + `</tr>`;

      trades.forEach(childT => {
        rowHtml += renderSingleTradeRow(childT, cols, sortedTrades, true, parentId);
      });

      return rowHtml;
    }

    function toggleCalGroupRows(parentId) {
      const children = document.querySelectorAll('.' + parentId + '-child');
      const arrow = document.getElementById(parentId + '-arrow');
      children.forEach(child => {
        if (child.style.display === 'none' || child.style.display === '') {
          child.style.display = 'table-row';
        } else {
          child.style.display = 'none';
        }
      });
      if (arrow) {
        if (arrow.textContent === 'Γû╢') {
          arrow.textContent = 'Γû╝';
        } else {
          arrow.textContent = 'Γû╢';
        }
      }
    }

    function toggleCalGroupSymbol(checked) {
      localStorage.setItem('cal_group_symbol', checked ? 'true' : 'false');
      saveUiConfigToBackend('cal_group_symbol', checked ? 'true' : 'false');
      renderPointsPerTradeTable();
    }

    function initCalGroupSymbolToggle() {
      const isGrouped = localStorage.getItem('cal_group_symbol') === 'true';
      const toggleInput = document.getElementById('cal-group-symbol-toggle');
      if (toggleInput) {
        toggleInput.checked = isGrouped;
      }
    }

    function renderPointsPagination(totalPages) {
      const wrap = document.getElementById('cal-points-pagination-wrap');
      if (!wrap) return;
      wrap.innerHTML = '';

      if (totalPages <= 1) return;

      const curr = window.calPointsCurrentPage;
      const createBtn = (lbl, page, active = false, disabled = false) => {
        const btn = document.createElement('button');
        btn.className = 'btn btn-gray';
        btn.style.padding = '4px 10px';
        btn.style.fontSize = '11px';
        btn.style.cursor = disabled ? 'default' : 'pointer';
        btn.textContent = lbl;

        if (active) {
          btn.style.backgroundColor = '#1f6feb';
          btn.style.borderColor = '#1f6feb';
          btn.style.color = '#fff';
        }

        if (disabled) {
          btn.style.opacity = '0.4';
          btn.disabled = true;
        } else {
          btn.onclick = () => {
            window.calPointsCurrentPage = page;
            renderPointsPerTradeTable();
          };
        }
        return btn;
      };

      wrap.appendChild(createBtn('Prev', curr - 1, false, curr === 1));

      const maxVisible = 5;
      let start = Math.max(1, curr - Math.floor(maxVisible / 2));
      let end = Math.min(totalPages, start + maxVisible - 1);
      if (end - start + 1 < maxVisible) {
        start = Math.max(1, end - maxVisible + 1);
      }

      if (start > 1) {
        wrap.appendChild(createBtn('1', 1));
        if (start > 2) {
          const el = document.createElement('span');
          el.style.color = '#8b949e';
          el.style.fontSize = '11px';
          el.textContent = '...';
          wrap.appendChild(el);
        }
      }

      for (let i = start; i <= end; i++) {
        wrap.appendChild(createBtn(i.toString(), i, i === curr));
      }

      if (end < totalPages) {
        if (end < totalPages - 1) {
          const el = document.createElement('span');
          el.style.color = '#8b949e';
          el.style.fontSize = '11px';
          el.textContent = '...';
          wrap.appendChild(el);
        }
        wrap.appendChild(createBtn(totalPages.toString(), totalPages));
      }

      wrap.appendChild(createBtn('Next', curr + 1, false, curr === totalPages));
    }

    // ---- Per-strategy equity overlay (window._eqPerStrategy) --------------------
    // One cumulative line per strategy instead of a single combined line — so it's
    // clear at a glance which strategy is contributing and which is dragging.
    function _eqEsc(s) { return (s == null ? '' : String(s)).replace(/[&<>"]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m])); }
    function _perStratColor(sid, i) {
      if (typeof window._stratHue === 'function') {
        try { const h = window._stratHue(sid); if (h != null && !isNaN(h)) return 'hsl(' + h + ' 68% 60%)'; } catch (e) {}
      }
      const PAL = ['#3fb950', '#e3b341', '#58a6ff', '#bc8cff', '#ff7b72', '#7ee787', '#f0883e', '#79c0ff', '#d2a8ff', '#ffa657', '#56d364', '#ff9bce'];
      return PAL[i % PAL.length];
    }
    function _perStratLabel(sid) {
      try { if (typeof window.regFull === 'function') { const l = window.regFull(sid); if (l) return l; } } catch (e) {}
      try { if (typeof window.regLabel === 'function') { const l = window.regLabel(sid); if (l) return l; } } catch (e) {}
      return sid || 'unknown';
    }
    function _drawPerStrategyEquity(container, trades) {
      const byS = {};
      trades.forEach(t => { const k = t.strategy || t.strat || 'unknown'; (byS[k] = byS[k] || []).push(t); });
      let sids = Object.keys(byS);
      if (!sids.length) { container.innerHTML = `<div style="color:#6e7681;font-size:12px;text-align:center;padding:60px 0;">No trades to display equity curve</div>`; return; }

      const mode = window._calSumMode || 'day';
      const useBuckets = (mode === 'day' || mode === 'weekly' || mode === 'monthly') && typeof _calGroupKey === 'function';
      let bucketKeys = null;
      if (useBuckets) {
        const ks = new Set();
        trades.forEach(t => { const k = _calGroupKey(t, mode); if (k !== '—') ks.add(k); });
        bucketKeys = [...ks].sort((a, b) => a.localeCompare(b));
      }

      let axisLabels = [];       // [0]='Start', then one per x point
      const series = {};         // sid -> [cumulative...] aligned to axisLabels
      if (bucketKeys && bucketKeys.length >= 2) {
        // multi-day/week/month: cumulative per bucket, carry forward
        axisLabels = ['Start'].concat(bucketKeys.map(k => _calPeriodLabel(k, mode)));
        sids.forEach(sid => {
          const perB = {};
          byS[sid].forEach(t => { const k = _calGroupKey(t, mode); if (k !== '—') perB[k] = (perB[k] || 0) + _eqValOf(t); });
          let cum = 0; const arr = [0];
          bucketKeys.forEach(k => { cum += (perB[k] || 0); arr.push(cum); });
          series[sid] = arr;
        });
      } else {
        // single day (or per-trade mode): global chronological trade axis, each strategy
        // carries forward and only steps on its own trades → intraday contribution.
        const all = [...trades].sort((a, b) => {
          const da = (a.entry_date || '') + ' ' + (a.entry_time || '00:00');
          const db = (b.entry_date || '') + ' ' + (b.entry_time || '00:00');
          return da.localeCompare(db);
        });
        axisLabels = ['Start'].concat(all.map((t, i) => (t.entry_time || String(i + 1))));
        const cum = {}; sids.forEach(s => { cum[s] = 0; series[s] = [0]; });
        all.forEach(t => {
          const owner = t.strategy || t.strat || 'unknown';
          cum[owner] = (cum[owner] || 0) + _eqValOf(t);
          sids.forEach(s => series[s].push(cum[s]));
        });
      }

      const M = axisLabels.length;
      sids = sids.sort((a, b) => (series[b][M - 1]) - (series[a][M - 1]));   // best contribution first

      const width = container.clientWidth || 600;
      const height = Math.max(240, container.clientHeight || 240);
      const PL = 60, PR = 18, PT = 18, PB = 28;
      const cw = width - PL - PR, chh = height - PT - PB;
      let lo = 0, hi = 0;
      sids.forEach(s => series[s].forEach(v => { lo = Math.min(lo, v); hi = Math.max(hi, v); }));
      const dd = (hi - lo) || 2000; lo -= dd * 0.1; hi += dd * 0.1;
      const gx = i => PL + (i / (M - 1)) * cw;
      const gy = v => PT + chh - ((v - lo) / (hi - lo)) * chh;

      let svg = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" style="overflow:visible;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">`;
      for (let i = 0; i <= 5; i++) {
        const v = lo + (i / 5) * (hi - lo), y = gy(v);
        svg += `<line x1="${PL}" y1="${y}" x2="${width - PR}" y2="${y}" stroke="#30363d" stroke-width="1" stroke-dasharray="${Math.abs(v) < 1 ? '0' : '3,3'}"/>`;
        svg += `<text x="${PL - 10}" y="${y + 4}" fill="#8b949e" font-size="10" text-anchor="end">₹${Math.round(v).toLocaleString('en-IN')}</text>`;
      }
      if (lo < 0 && hi > 0) { const zy = gy(0); svg += `<line x1="${PL}" y1="${zy}" x2="${width - PR}" y2="${zy}" stroke="#8b949e" stroke-width="1.2" stroke-dasharray="4,4" opacity="0.6"/>`; }
      const nlab = Math.min(6, M - 1);
      for (let j = 0; nlab > 0 && j <= nlab; j++) {
        const i = Math.round(1 + (j / nlab) * (M - 2)); if (i < 1 || i >= M) continue;
        svg += `<text x="${gx(i)}" y="${height - 8}" fill="#6e7681" font-size="9" text-anchor="middle">${_eqEsc(axisLabels[i])}</text>`;
      }
      // interactive: each line + a wide transparent hit-path for easy hover. No always-on
      // legend box (it covered the chart) — hover a line to identify it + read its value.
      window._eqPsX = Array.from({ length: M }, (_, i) => gx(i));
      window._eqPsLines = sids.map((sid, si) => ({ label: _perStratLabel(sid), color: _perStratColor(sid, si), fin: series[sid][M - 1], vals: series[sid] }));
      sids.forEach((sid, si) => {
        const col = _perStratColor(sid, si);
        let ln = '';
        series[sid].forEach((v, i) => { const x = gx(i), y = gy(v); ln += (i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1) + ' '; });
        const fin = series[sid][M - 1];
        svg += `<path class="eqline" data-i="${si}" d="${ln}" fill="none" stroke="${col}" stroke-width="1.9" opacity="0.9" style="transition:opacity .08s,stroke-width .08s"/>`;
        svg += `<path d="${ln}" fill="none" stroke="transparent" stroke-width="14" style="cursor:pointer" onmousemove="_eqPsHover(event,${si})" onmouseleave="_eqPsOut(event)"/>`;
        svg += `<circle cx="${gx(M - 1)}" cy="${gy(fin)}" r="3" fill="${col}"/>`;
      });
      svg += `</svg>`;

      const tip = `<div class="eqps-tip" style="position:absolute;display:none;background:rgba(13,17,23,0.95);border:1px solid #30363d;border-radius:6px;padding:5px 9px;font-size:11.5px;color:#e6edf3;pointer-events:none;white-space:nowrap;z-index:20;box-shadow:0 4px 14px rgba(0,0,0,.5);"></div>`;
      container.style.position = container.style.position || 'relative';
      container.innerHTML = svg + tip;
    }
    // hover a per-strategy line → highlight it (dim the rest) + tooltip (value at point + net)
    window._eqPsHover = function (ev, i) {
      const svg = ev.target.ownerSVGElement; if (!svg) return;
      const cont = svg.parentElement;
      cont.querySelectorAll('path.eqline').forEach(p => {
        const on = (+p.getAttribute('data-i') === i);
        p.setAttribute('opacity', on ? '1' : '0.15');
        p.setAttribute('stroke-width', on ? '3' : '1.5');
      });
      const info = (window._eqPsLines || [])[i]; if (!info) return;
      const r = cont.getBoundingClientRect();
      const cx = ev.clientX - r.left;
      let bi = 0, bd = 1e9; (window._eqPsX || []).forEach((xx, k) => { const dd = Math.abs(xx - cx); if (dd < bd) { bd = dd; bi = k; } });
      const val = (info.vals && info.vals[bi] != null) ? info.vals[bi] : info.fin;
      const money = v => (v < 0 ? '-' : '+') + '₹' + Math.round(Math.abs(v)).toLocaleString('en-IN');
      const tip = cont.querySelector('.eqps-tip');
      if (tip) {
        tip.innerHTML = `<span style="display:inline-block;width:11px;height:3px;border-radius:2px;background:${info.color};margin-right:6px;vertical-align:middle"></span><b>${_eqEsc(info.label)}</b>`
          + `<div style="margin-top:2px;color:#8b949e">point <b style="color:${val >= 0 ? '#3fb950' : '#f85149'}">${money(val)}</b> · net <b style="color:${info.fin >= 0 ? '#3fb950' : '#f85149'}">${money(info.fin)}</b></div>`;
        let x = cx + 14, y = (ev.clientY - r.top) + 12;
        if (x > r.width - 200) x = cx - 196;
        tip.style.left = x + 'px'; tip.style.top = y + 'px'; tip.style.display = 'block';
      }
    };
    window._eqPsOut = function (ev) {
      const svg = ev.target.ownerSVGElement; if (!svg) return;
      const cont = svg.parentElement;
      cont.querySelectorAll('path.eqline').forEach(p => { p.setAttribute('opacity', '0.9'); p.setAttribute('stroke-width', '1.9'); });
      const tip = cont.querySelector('.eqps-tip'); if (tip) tip.style.display = 'none';
    };

    // toggle Combined ⟷ Per-strategy (persisted); redraws respecting the selected-day filter
    window.toggleEqPerStrategy = function (v) {
      window._eqPerStrategy = (v === undefined) ? (window._eqPerStrategy ? 0 : 1) : (v ? 1 : 0);
      try { localStorage.setItem('eq_per_strategy', window._eqPerStrategy ? '1' : '0'); } catch (e) {}
      document.querySelectorAll('.eq-ps-btn').forEach(b => b.classList.toggle('on', !!window._eqPerStrategy));
      const all = window.currentCalendarTrades || [];
      const tr = window.calSelectedDateFilter ? all.filter(t => (t.exit_date || t.entry_date) === window.calSelectedDateFilter) : all;
      drawEquityCurveChart('cal-equity-curve-container', tr);
    };
    try { window._eqPerStrategy = (localStorage.getItem('eq_per_strategy') === '1') ? 1 : 0; } catch (e) { window._eqPerStrategy = 0; }
    (function _eqPsBtnSync() {
      const sync = () => { if (window._eqPerStrategy) document.querySelectorAll('.eq-ps-btn').forEach(b => b.classList.add('on')); };
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync); else sync();
    })();

    // ---- Equity chart display options (⚙ gear popup) ---------------------------
    // value: gross|net|tax (which P&L basis to plot); bars/markers/labels: overlays.
    window._eqOpts = (function () {
      const d = { value: 'net', bars: false, markers: false, labels: false };
      try { return Object.assign(d, JSON.parse(localStorage.getItem('eq_chart_opts') || '{}')); } catch (e) { return d; }
    })();
    function _eqOptsSave() { try { localStorage.setItem('eq_chart_opts', JSON.stringify(window._eqOpts)); } catch (e) {} }
    // pick the selected value basis off a trade's pre-computed _gross/_net/_tax (set in calendarRender)
    function _eqValOf(t) { const v = t['_' + ((window._eqOpts && window._eqOpts.value) || 'gross')]; return (v != null) ? v : (t.pnl || 0); }
    function _eqRedraw() {
      const all = window.currentCalendarTrades || [];
      const tr = window.calSelectedDateFilter ? all.filter(t => (t.exit_date || t.entry_date) === window.calSelectedDateFilter) : all;
      drawEquityCurveChart('cal-equity-curve-container', tr);
    }
    // Repaint calendar day-tiles for the current value basis (net/gross/tax) using
    // the cached summary + per-day tax — no API re-fetch. Called on the ⚙ toggle.
    window._calRepaintTileValues = function () {
      const ctx = window._calTileCtx; if (!ctx) return;
      const mode = (window._eqOpts && window._eqOpts.value) || 'net';
      document.querySelectorAll('.cal-day-cell[data-date]').forEach(cell => {
        const dateStr = cell.dataset.date;
        const sd = ctx.summary[dateStr]; if (!sd) return;
        const net = sd.pnl || 0, tax = (ctx.tax && ctx.tax[dateStr]) || 0;
        const v = mode === 'gross' ? net + tax : (mode === 'tax' ? tax : net);
        const pnlDiv = cell.querySelector('.cal-day-pnl');
        if (pnlDiv) {
          pnlDiv.className = `cal-day-pnl ${v >= 0 ? 'pos' : 'neg'}`;
          pnlDiv.textContent = `${v >= 0 ? '+' : ''}₹${Math.round(v).toLocaleString('en-IN')}`;
        }
        cell.classList.remove('profit-pos', 'profit-neg');
        if (v !== 0) cell.classList.add(v > 0 ? 'profit-pos' : 'profit-neg');
      });
    };
    window.eqSetValue = function (v) {
      window._eqOpts.value = v; _eqOptsSave();
      document.querySelectorAll('.eq-val-seg').forEach(s => s.classList.toggle('on', s.getAttribute('data-v') === v));
      window._calRepaintTileValues();   // calendar tiles bhi follow karein
      _eqRedraw();
    };
    window.eqToggleOpt = function (k, el) { window._eqOpts[k] = !!(el && el.checked); _eqOptsSave(); _eqRedraw(); };
    function _eqGearSync(pop) {
      const o = window._eqOpts;
      pop.querySelectorAll('.eq-val-seg').forEach(s => s.classList.toggle('on', s.getAttribute('data-v') === o.value));
      ['bars', 'markers', 'labels'].forEach(k => { const cb = pop.querySelector('[data-k="' + k + '"]'); if (cb) cb.checked = !!o[k]; });
    }
    window.eqGearToggle = function (ev) {
      if (ev) ev.stopPropagation();
      document.querySelectorAll('.eq-gear-pop').forEach(p => {
        const open = p.style.display === 'block';
        p.style.display = open ? 'none' : 'block';
        if (!open) _eqGearSync(p);
      });
    };
    document.addEventListener('click', function (e) {
      document.querySelectorAll('.eq-gear-pop').forEach(p => {
        if (p.style.display === 'block' && !p.contains(e.target) && !(e.target.closest && e.target.closest('.eq-gear-btn'))) p.style.display = 'none';
      });
    });

    function drawEquityCurveChart(containerId, trades) {
      const container = document.getElementById(containerId);
      if (!container) return;
      container.innerHTML = '';

      if (!trades || !trades.length) {
        container.innerHTML = `<div style="color:#6e7681;font-size:12px;text-align:center;padding:60px 0;">No trades to display equity curve</div>`;
        return;
      }

      // Per-strategy overlay branch (Real-only; Compare/BS ignored here to avoid clutter).
      if (window._eqPerStrategy) { _drawPerStrategyEquity(container, trades); return; }

      // Calculate cumulative PnL — resolution follows the Total Summary mode:
      // Day → per-day, Weekly → per-week, Monthly → per-month; Strategy/other →
      // per-trade (chronological). Same aggregation as the summary table.
      // COMPARE mode (Real ⟷ BS): also build a parallel BS cumulative on the SAME
      // buckets (task 01). BS can only price NIFTY/BankNifty index legs, so both
      // lines use ONLY those legs → apples-to-apples (fair, same legs).
      const _eqCmp = (typeof window.bsCompareActive === 'function') && window.bsCompareActive();
      const _rbOf = (t) => (_eqCmp && window.bsTradeRealBs)
        ? window.bsTradeRealBs(t, 'net')
        : { real: _eqValOf(t), bs: 0, ok: false };
      const _eqTrades = _eqCmp ? trades.filter(t => _rbOf(t).ok) : trades;

      let cumPnL = 0, cumBs = 0;
      const data = [{ xLabel: 'Start', pnl: 0, cumulative: 0, cumulativeBs: (_eqCmp ? 0 : null), symbol: '', date: '', time: '' }];
      const _eqMode = window._calSumMode || 'day';

      if ((_eqMode === 'day' || _eqMode === 'weekly' || _eqMode === 'monthly')
        && typeof _calGroupKey === 'function') {
        const buckets = {}, bucketsBs = {};
        _eqTrades.forEach(t => {
          const k = _calGroupKey(t, _eqMode);
          if (k === '—') return;
          const rb = _rbOf(t);
          buckets[k] = (buckets[k] || 0) + rb.real;
          if (_eqCmp) bucketsBs[k] = (bucketsBs[k] || 0) + rb.bs;
        });
        Object.keys(buckets).sort((a, b) => a.localeCompare(b)).forEach(k => {
          cumPnL += buckets[k];
          if (_eqCmp) cumBs += (bucketsBs[k] || 0);
          data.push({
            xLabel: _calPeriodLabel(k, _eqMode),
            pnl: buckets[k], cumulative: cumPnL,
            cumulativeBs: (_eqCmp ? cumBs : null),
            symbol: '', date: k, time: ''
          });
        });
      } else {
        const sorted = [..._eqTrades].sort((a, b) => {
          const da = a.entry_date + ' ' + (a.entry_time || '00:00');
          const db = b.entry_date + ' ' + (b.entry_time || '00:00');
          return da.localeCompare(db);
        });
        sorted.forEach((t, i) => {
          const rb = _rbOf(t);
          cumPnL += rb.real;
          if (_eqCmp) cumBs += rb.bs;
          data.push({
            xLabel: `${i + 1}`,
            pnl: rb.real, cumulative: cumPnL,
            cumulativeBs: (_eqCmp ? cumBs : null),
            symbol: t.sym || t.symbol,
            date: t.exit_date || t.entry_date || '',
            time: t.exit_time || t.entry_time || ''
          });
        });
      }

      // compare mode but no BS-priceable legs in view → nothing to compare
      if (_eqCmp && data.length <= 1) {
        container.innerHTML = `<div style="color:#6e7681;font-size:12px;text-align:center;padding:60px 0;">Compare: is filter me koi NIFTY/BankNifty (BS-priceable) leg nahi</div>`;
        return;
      }

      const width = container.clientWidth || 600;
      const height = Math.max(240, container.clientHeight || 240);   // card fill (heatmap S/M/L pe balanced split)
      const paddingLeft = 60;
      const paddingRight = 20;
      const paddingTop = 20;
      const paddingBottom = 30;

      const chartWidth = width - paddingLeft - paddingRight;
      const chartHeight = height - paddingTop - paddingBottom;

      // Find min & max cumulative PnL (include BS series in compare mode so both
      // lines fit the same y-scale)
      const _yVals = data.map(d => d.cumulative);
      if (_eqCmp) data.forEach(d => { if (d.cumulativeBs != null) _yVals.push(d.cumulativeBs); });
      let minVal = Math.min(..._yVals);
      let maxVal = Math.max(..._yVals);

      // Add padding/buffer
      const diff = maxVal - minVal;
      if (diff === 0) {
        minVal -= 1000;
        maxVal += 1000;
      } else {
        minVal -= diff * 0.1;
        maxVal += diff * 0.1;
      }

      // X scale helper: maps index to X coordinate
      const getX = (index) => paddingLeft + (index / (data.length - 1)) * chartWidth;

      // Y scale helper: maps cumulative PnL value to Y coordinate
      const getY = (val) => paddingTop + chartHeight - ((val - minVal) / (maxVal - minVal)) * chartHeight;

      // Build SVG string
      let svg = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" style="overflow:visible; font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">`;

      // Definitions (gradiants and clip paths)
      svg += `
    <defs>
      <linearGradient id="area-grad-green" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#3fb950" stop-opacity="0.3"/>
        <stop offset="100%" stop-color="#3fb950" stop-opacity="0.0"/>
      </linearGradient>
      <linearGradient id="area-grad-red" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#f85149" stop-opacity="0.3"/>
        <stop offset="100%" stop-color="#f85149" stop-opacity="0.0"/>
      </linearGradient>
    </defs>
  `;

      // Select gradient based on final net P&L
      const finalPnL = data[data.length - 1].cumulative;
      const fillGrad = finalPnL >= 0 ? 'url(#area-grad-green)' : 'url(#area-grad-red)';
      const strokeColor = finalPnL >= 0 ? '#3fb950' : '#f85149';

      // Draw horizontal grid lines (e.g. 5 lines)
      const steps = 5;
      for (let i = 0; i <= steps; i++) {
        const val = minVal + (i / steps) * (maxVal - minVal);
        const y = getY(val);
        svg += `<line x1="${paddingLeft}" y1="${y}" x2="${width - paddingRight}" y2="${y}" stroke="#30363d" stroke-dasharray="${val === 0 ? '0' : '3,3'}" stroke-width="${val === 0 ? '1.5' : '1'}"/>`;
        // Y-axis label
        svg += `<text x="${paddingLeft - 10}" y="${y + 4}" fill="#8b949e" font-size="10" text-anchor="end">₹${Math.round(val).toLocaleString('en-IN')}</text>`;
      }

      // Draw Zero Line separately if it is within range but not hit by step loop
      if (minVal < 0 && maxVal > 0) {
        const zeroY = getY(0);
        svg += `<line x1="${paddingLeft}" y1="${zeroY}" x2="${width - paddingRight}" y2="${zeroY}" stroke="#8b949e" stroke-width="1.2" stroke-dasharray="4,4" opacity="0.6"/>`;
      }

      // Build Line Path and Area Path
      let linePath = '';
      let areaPath = `M ${paddingLeft} ${getY(0)} `; // Start at zero line

      data.forEach((d, i) => {
        const x = getX(i);
        const y = getY(d.cumulative);

        if (i === 0) {
          linePath += `M ${x} ${y} `;
          areaPath += `L ${x} ${y} `;
        } else {
          linePath += `L ${x} ${y} `;
          areaPath += `L ${x} ${y} `;
        }
      });

      // Close the area path to the bottom or zero line?
      areaPath += `L ${getX(data.length - 1)} ${paddingTop + chartHeight} L ${paddingLeft} ${paddingTop + chartHeight} Z`;

      // Draw the area fill
      svg += `<path d="${areaPath}" fill="${fillGrad}" />`;

      // Daily / per-bucket P&L bars (⚙ option) — each point's OWN value behind the
      // cumulative line, on its own scale (per-bucket ≪ cumulative), anchored at ₹0.
      if (window._eqOpts && window._eqOpts.bars) {
        let maxBar = 0;
        for (let i = 1; i < data.length; i++) maxBar = Math.max(maxBar, Math.abs(data[i].pnl || 0));
        if (maxBar > 0) {
          const zeroY = getY(0);
          const barMax = chartHeight * 0.32;
          const bw = Math.max(2, Math.min(16, (chartWidth / Math.max(1, data.length - 1)) * 0.5));
          for (let i = 1; i < data.length; i++) {
            const v = data[i].pnl || 0; if (!v) continue;
            const h = (Math.abs(v) / maxBar) * barMax;
            const x = getX(i) - bw / 2;
            const y = v >= 0 ? zeroY - h : zeroY;
            const col = v >= 0 ? '#3fb95055' : '#f8514955';
            svg += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${col}" rx="1"/>`;
          }
        }
      }

      // Draw the stroke line
      svg += `<path d="${linePath}" fill="none" stroke="${strokeColor}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />`;

      // COMPARE mode: BS line (dashed purple) over the same buckets + a legend.
      if (_eqCmp) {
        let bsPath = '';
        data.forEach((d, i) => {
          const x = getX(i);
          const y = getY(d.cumulativeBs != null ? d.cumulativeBs : 0);
          bsPath += (i === 0 ? 'M' : 'L') + ` ${x} ${y} `;
        });
        svg += `<path d="${bsPath}" fill="none" stroke="#a371f7" stroke-width="2" stroke-dasharray="5,4" stroke-linecap="round" stroke-linejoin="round" />`;
        // legend (top-left of plot)
        const lx = paddingLeft + 6, ly = paddingTop + 4;
        svg += `<g font-size="10">
          <line x1="${lx}" y1="${ly}" x2="${lx + 18}" y2="${ly}" stroke="${strokeColor}" stroke-width="2.5"/>
          <text x="${lx + 23}" y="${ly + 3}" fill="#8b949e">Real</text>
          <line x1="${lx + 62}" y1="${ly}" x2="${lx + 80}" y2="${ly}" stroke="#a371f7" stroke-width="2" stroke-dasharray="5,4"/>
          <text x="${lx + 85}" y="${ly + 3}" fill="#8b949e">BS</text>
        </g>`;
      }

      // Draw X-axis labels (e.g. 6 labels max)
      const xLabelsCount = Math.min(data.length, 6);
      for (let i = 0; i < xLabelsCount; i++) {
        const idx = Math.round((i / (xLabelsCount - 1)) * (data.length - 1));
        if (idx >= 0 && idx < data.length) {
          const x = getX(idx);
          const label = data[idx].xLabel === 'Start' ? 'Start' : `T#${data[idx].xLabel}`;
          svg += `<text x="${x}" y="${paddingTop + chartHeight + 16}" fill="#8b949e" font-size="9" text-anchor="middle">${label}</text>`;
        }
      }

      // Point markers + value labels (⚙ options) — markers on every point; labels
      // show cumulative value, thinned when many points so they don't overlap.
      if (window._eqOpts && (window._eqOpts.markers || window._eqOpts.labels)) {
        const every = data.length > 16 ? Math.ceil((data.length - 1) / 14) : 1;
        for (let i = 1; i < data.length; i++) {
          const x = getX(i), y = getY(data[i].cumulative);
          if (window._eqOpts.markers) svg += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.6" fill="${strokeColor}"/>`;
          if (window._eqOpts.labels && (i % every === 0 || i === data.length - 1)) {
            const cv = data[i].cumulative;
            svg += `<text x="${x.toFixed(1)}" y="${(y - 6).toFixed(1)}" fill="#8b949e" font-size="8.5" text-anchor="middle">${(cv < 0 ? '-' : '') + '₹' + Math.round(Math.abs(cv)).toLocaleString('en-IN')}</text>`;
          }
        }
      }

      // Add hover interactive elements
      svg += `
    <g id="equity-hover-group" style="display:none;">
      <line id="equity-hover-line" x1="0" y1="${paddingTop}" x2="0" y2="${paddingTop + chartHeight}" stroke="#8b949e" stroke-width="1" stroke-dasharray="2,2"/>
      <circle id="equity-hover-circle" r="5" fill="${strokeColor}" stroke="#ffffff" stroke-width="1.5"/>
    </g>
  `;

      svg += `</svg>`;

      // Append SVG to container
      container.innerHTML = svg;

      // Create HTML Tooltip element in container (absolutely positioned)
      const tooltip = document.createElement('div');
      tooltip.id = 'equity-tooltip';
      tooltip.style.cssText = `
    position: absolute;
    display: none;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 8px 10px;
    color: #e6edf3;
    font-size: 11px;
    pointer-events: none;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
    z-index: 100;
    line-height: 1.4;
    white-space: nowrap;
  `;
      container.appendChild(tooltip);

      // Mouse interaction
      const svgEl = container.querySelector('svg');
      const hoverGroup = svgEl.getElementById('equity-hover-group');
      const hoverLine = svgEl.getElementById('equity-hover-line');
      const hoverCircle = svgEl.getElementById('equity-hover-circle');

      svgEl.addEventListener('mousemove', (e) => {
        const rect = svgEl.getBoundingClientRect();
        const mouseX = (e.clientX - rect.left) * (width / rect.width); // Adjust for viewBox scaling

        let idx = Math.round(((mouseX - paddingLeft) / chartWidth) * (data.length - 1));
        if (idx < 0) idx = 0;
        if (idx >= data.length) idx = data.length - 1;

        const pt = data[idx];
        const x = getX(idx);
        const y = getY(pt.cumulative);

        // Scale back coordinates to actual screen pixels for absolute tooltip positioning
        const scaleX = rect.width / width;
        const scaleY = rect.height / height;

        // Update SVG elements
        hoverGroup.style.display = 'block';
        hoverLine.setAttribute('x1', x);
        hoverLine.setAttribute('x2', x);
        hoverCircle.setAttribute('cx', x);
        hoverCircle.setAttribute('cy', y);
        hoverCircle.setAttribute('fill', pt.cumulative >= 0 ? '#3fb950' : '#f85149');

        // Update HTML Tooltip content and position
        let tooltipHtml = '';
        if (pt.xLabel === 'Start') {
          tooltipHtml = `<b>Start of Month</b><br>Cumulative P&L: ₹0`;
        } else {
          const pnlColor = pt.pnl >= 0 ? '#3fb950' : '#f85149';
          const cumColor = pt.cumulative >= 0 ? '#3fb950' : '#f85149';
          tooltipHtml = `
        <b>Trade #${pt.xLabel} Details</b><br>
        Date: ${pt.date} ${pt.time}<br>
        Symbol: <b>${pt.symbol}</b><br>
        Trade P&L: <span style="color:${pnlColor}; font-weight:600">${pt.pnl >= 0 ? '+' : ''}₹${Math.round(pt.pnl).toLocaleString('en-IN')}</span><br>
        Cumulative: <span style="color:${cumColor}; font-weight:600">${pt.cumulative >= 0 ? '+' : ''}₹${Math.round(pt.cumulative).toLocaleString('en-IN')}</span>`;
          if (_eqCmp && pt.cumulativeBs != null) {
            const dCmp = pt.cumulative - pt.cumulativeBs;   // Real − BS
            tooltipHtml += `<br>BS (model): <span style="color:#a371f7; font-weight:600">${pt.cumulativeBs >= 0 ? '+' : ''}₹${Math.round(pt.cumulativeBs).toLocaleString('en-IN')}</span>`
              + `<br>Real − BS: <span style="color:${dCmp >= 0 ? '#3fb950' : '#f85149'}; font-weight:600">${dCmp >= 0 ? '+' : ''}₹${Math.round(dCmp).toLocaleString('en-IN')}</span>`;
          }
        }

        tooltip.innerHTML = tooltipHtml;
        tooltip.style.display = 'block';

        const tooltipWidth = tooltip.clientWidth || 150;
        let tooltipX = (x * scaleX) + 15;
        if (tooltipX + tooltipWidth > rect.width) {
          tooltipX = (x * scaleX) - tooltipWidth - 15;
        }
        tooltip.style.left = `${tooltipX}px`;
        tooltip.style.top = `${(y * scaleY) - 20}px`;
      });

      svgEl.addEventListener('mouseleave', () => {
        hoverGroup.style.display = 'none';
        tooltip.style.display = 'none';
      });
    }

    // Window resize handler for Stats tab
    window.addEventListener('resize', () => {
      if (document.getElementById('tab-calendar').classList.contains('active') && window.currentCalendarTrades) {
        drawEquityCurveChart('cal-equity-curve-container', window.currentCalendarTrades);
      }
    });


    // init
    pineLoadLatest();

    // "🚀 Deploy" from the Backtest Results page's Saved Results list lands
    // here as /?deploy=<strategy>&symbol=<sym> — auto-open the Run modal for
    // that strategy so the user can go straight to Paper/Live without hunting
    // for it in the strategy grid.
    (function _autoOpenDeployFromQuery() {
      const params = new URLSearchParams(window.location.search);
      const deploy = params.get('deploy');
      if (!deploy) return;
      const base = deploy.startsWith('vwap') ? 'vwap_ema_failure' : deploy;
      setTimeout(() => runModalOpen(deploy, base, '', `strategies/${base}.py`), 300);
      history.replaceState(null, '', window.location.pathname);
    })();

  

    // preload pine sub-tab data (hidden until opened) — moved from the top of this
    // block when it was split into modules (hoisting doesn't cross files).
    pineLoadLatest(); pineLoadHistory();
