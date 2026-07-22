/* stats2.js — glue for the compact Stats V2 page (/stats2).
 * Reuses app-12/app-13/app-07 calendar+stats logic in a new tabbed layout.
 * Only NEW code lives here: unified switch, tab wiring, fullscreen, inline
 * trade chart, and the heatmap/distribution/performance panels. The original
 * Stats tab (index.html) is untouched. */
(function () {
  'use strict';

  // ── minimal deps not loaded on this page ──────────────────────────────────
  if (typeof window.toast !== 'function') {
    window.toast = function (msg) {
      var t = document.getElementById('_s2toast');
      if (!t) { t = document.createElement('div'); t.id = '_s2toast';
        t.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1c2333;border:1px solid #30363d;border-radius:8px;color:#e6edf3;padding:9px 16px;font-size:12.5px;z-index:9999;box-shadow:0 6px 22px #0009';
        document.body.appendChild(t); }
      t.textContent = msg; t.style.display = 'block';
      clearTimeout(t._h); t._h = setTimeout(function () { t.style.display = 'none'; }, 2600);
    };
  }
  // functions the reused code may call that live in NOT-loaded files → no-op safety
  ['ordersRender', 'toggleAllNotes', 'openTagStoreModal', 'exportCalPointsCsv',
   'exportCalPointsPdf', 'openStatsColModal', 'saveStatsColPrefs', 'renderStatsGroupedList',
   'renderStatsClosedTable'].forEach(function (fn) {
    if (typeof window[fn] !== 'function') window[fn] = function () {};
  });

  // ── segmented-control helper: set .on by data-v WITHOUT firing onclick ─────
  function _setSeg(id, val) {
    var p = document.getElementById(id); if (!p) return;
    p.querySelectorAll('span').forEach(function (x) {
      var on = x.getAttribute('data-v') === val;
      x.classList.toggle('on', on); x.style.color = on ? '#fff' : '#8b949e';
      x.style.background = on ? '#1f6feb' : '';
    });
  }

  // ── ① unified switch: Live/Paper · Paper · Live · Backtest ─────────────────
  // Drives the reused (hidden) #cal-view (live/bt) + #cal-mode (all/paper/live).
  window.s2Switch = function (mode, el) {
    var p = document.getElementById('s2-switch');
    p.querySelectorAll('span').forEach(function (x) { x.classList.remove('on'); x.style.color = '#8b949e'; });
    el.classList.add('on'); el.style.color = '';
    var srcWrap = document.getElementById('s2-src'), srcLbl = document.getElementById('s2-src-lbl');
    var gear = document.getElementById('s2-bt-gear'), strat = document.getElementById('cal-strat');
    if (mode === 'bt') {
      if (srcWrap) srcWrap.style.display = 'none'; if (srcLbl) srcLbl.style.display = 'none';
      if (gear) gear.style.display = '';                       // family/strategy (run) picker
      if (strat) strat.style.maxWidth = '190px';               // compact run selector in backtest
      calSetView('bt', document.querySelector('#cal-view span[data-v="bt"]'));   // shows bt controls + loads runs + renders
    } else {
      if (srcWrap) srcWrap.style.display = ''; if (srcLbl) srcLbl.style.display = '';
      if (gear) gear.style.display = 'none';
      if (strat) strat.style.maxWidth = '260px';
      _setSeg('cal-mode', mode === 'paper' ? 'paper' : mode === 'live' ? 'live' : '');
      calSetView('live', document.querySelector('#cal-view span[data-v="live"]'));  // resets + calendarRender (reads cal-mode)
    }
    _s2Heat();   // heatmap reflects the active mode/filters
  };

  // Multi-select mode: Paper + Live (trade-modes, combine → All) · Backtest (separate data-source, solo).
  window._s2sel = window._s2sel || new Set(['paper', 'live']);
  window.s2Mode = function (m, el) {
    var sel = window._s2sel;
    if (m === 'bt') {
      if (sel.has('bt')) return;                       // already backtest (avoid empty selection)
      sel.clear(); sel.add('bt');
    } else {
      sel.delete('bt');                                // leaving backtest → live data
      if (sel.has(m)) { if (sel.size > 1) sel.delete(m); }   // keep at least one live-mode
      else sel.add(m);
    }
    document.querySelectorAll('#s2-switch span[data-m]').forEach(function (s) {
      var on = sel.has(s.dataset.m);
      s.classList.toggle('on', on);
      s.style.background = on ? '#1f6feb' : '';
      s.style.color = on ? '#fff' : '#8b949e';
    });
    var gear = document.getElementById('s2-bt-gear'), strat = document.getElementById('cal-strat');
    if (sel.has('bt')) {
      if (gear) gear.style.display = '';
      if (strat) strat.style.maxWidth = '190px';
      calSetView('bt', document.querySelector('#cal-view span[data-v="bt"]'));
    } else {
      if (gear) gear.style.display = 'none';
      if (strat) strat.style.maxWidth = '260px';
      var mode = (sel.has('paper') && sel.has('live')) ? '' : sel.has('paper') ? 'paper' : 'live';
      _setSeg('cal-mode', mode);
      calSetView('live', document.querySelector('#cal-view span[data-v="live"]'));
    }
    _s2Heat();
  };
  // visible Source control mirrors the hidden #cal-src
  window.s2Src = function (val, el) {
    var p = document.getElementById('s2-src');
    p.querySelectorAll('span').forEach(function (x) { x.classList.remove('on'); x.style.color = '#8b949e'; });
    el.classList.add('on'); el.style.color = '';
    _setSeg('cal-src', val); calendarRender(); _s2Heat();
  };

  // ── tab wiring (calendar / chart / table groups) ───────────────────────────
  window.s2Tab = function (ev, group) {
    var t = ev.target.closest('.s2tab'); if (!t) return;
    t.parentElement.querySelectorAll('.s2tab').forEach(function (x) { x.classList.remove('on'); });
    t.classList.add('on');
    var pane = t.getAttribute('data-p');
    document.querySelectorAll('.s2pane').forEach(function (pn) {
      if (pn.id.indexOf(group + '-') === 0) pn.classList.toggle('on', pn.id === pane);
    });
    if (group === 'calp') {
      document.getElementById('s2-cal-ctrls').style.display = (pane === 'calp-cal') ? 'flex' : 'none';
    }
    if (group === 'chart') {
      var gl = document.getElementById('s2-glseg');
      if (gl) gl.style.display = (pane === 'chart-gl') ? 'flex' : 'none';
      if (pane === 'chart-gl' && typeof window.renderPnlGraph === 'function') window.renderPnlGraph();
      if (pane === 'chart-dist') _s2Dist();
    }
    if (group === 'tbl') {
      document.getElementById('s2-ts-ctrl').style.display   = (pane === 'tbl-ts')   ? 'flex' : 'none';
      document.getElementById('s2-ppt-ctrl').style.display  = (pane === 'tbl-ppt')  ? 'flex' : 'none';
      document.getElementById('s2-perf-ctrl').style.display = (pane === 'tbl-perf') ? 'flex' : 'none';
      var _bsc = document.getElementById('s2-bsdiv-ctrl');
      if (_bsc) _bsc.style.display = (pane === 'tbl-bsdiv') ? 'flex' : 'none';
      if (pane === 'tbl-perf') _s2Perf();
      if (pane === 'tbl-bsdiv' && typeof bsRenderDivergence === 'function') bsRenderDivergence();
    }
  };

  // ── ⛶ full-screen the active chart ────────────────────────────────────────
  function _s2ReRender(id) {
    if (id === 'chart-eq' && typeof drawEquityCurveChart === 'function') { try { drawEquityCurveChart('cal-equity-curve-container', window.currentCalendarTrades || []); } catch (e) {} }
    else if (id === 'chart-gl' && typeof window.renderPnlGraph === 'function') { try { window.renderPnlGraph(); } catch (e) {} }
    else if (id === 'chart-dist' && typeof _s2Dist === 'function') { try { _s2Dist(); } catch (e) {} }
  }
  window.s2Fs = function () {
    var pane = document.querySelector('#chart-eq.on, #chart-gl.on, #chart-dist.on');
    if (!pane || window._s2fs) return;
    var titleTab = document.querySelector('.s2tabs .s2tab.on');
    document.getElementById('s2fstitle').textContent = titleTab ? titleTab.textContent.trim() : 'Chart';
    var body = document.getElementById('s2fsbody');
    // MOVE the live pane into fullscreen (NOT innerHTML-clone): a Chart.js canvas
    // clones BLANK (gain/loss) and an SVG clones as a FROZEN snapshot (equity
    // "atka"). A placeholder marks the spot so we can put it back on close.
    var ph = document.createComment('s2fs-slot');
    pane.parentNode.insertBefore(ph, pane);
    window._s2fs = { pane: pane, ph: ph };
    var card = document.getElementById('cal-pnl-graph-card');   // gain/loss card has a fixed 230px height
    if (card) { window._s2fs.cardH = card.style.height; card.style.height = '100%'; }
    body.innerHTML = ''; body.appendChild(pane);
    document.getElementById('s2fsov').style.display = 'flex';
    setTimeout(function () { _s2ReRender(pane.id); }, 60);   // re-render live at the new (larger) size
  };
  window.s2FsClose = function (ev) {
    if (ev && ev.target && ev.target.id !== 's2fsov' && !(ev.target.textContent || '').includes('Close')) return;
    document.getElementById('s2fsov').style.display = 'none';
    var f = window._s2fs;
    if (f && f.ph && f.ph.parentNode) {
      var card = document.getElementById('cal-pnl-graph-card');
      if (card && f.cardH != null) card.style.height = f.cardH;
      f.ph.parentNode.insertBefore(f.pane, f.ph);   // put the pane back
      f.ph.parentNode.removeChild(f.ph);
      setTimeout(function () { _s2ReRender(f.pane.id); }, 60);   // re-render at normal size
    }
    window._s2fs = null;
  };

  // Trade Chart opens in a NEW TAB via the original window.openTradeChart (app-13) —
  // no override here (reverted per user request; row 📈 button → new tab).

  // ── NEW panels (REAL data) ─────────────────────────────────────────────────
  var _S2_MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  // heatmap size presets (S/M/L) — user-selectable, localStorage-persisted
  var _HEAT_SZ = {
    s: { tf:'10px', sp:'2px', th:'4px 3px', thf:'10px', lbl:'5px 6px', lblf:'11px', cell:'5px 4px', cf:'10px', dot:'12px' },
    m: { tf:'11px', sp:'3px', th:'5px 4px', thf:'11px', lbl:'7px 8px', lblf:'12px', cell:'7px 6px', cf:'11px', dot:'13px' },
    l: { tf:'13px', sp:'4px', th:'9px 5px', thf:'12px', lbl:'13px 10px', lblf:'14px', cell:'13px 7px', cf:'12.5px', dot:'16px' }
  };
  try { window._heatSize = window._heatSize || localStorage.getItem('heat_size') || 'm'; } catch (e) { window._heatSize = window._heatSize || 'm'; }
  window.s2HeatSize = function (sz, el) {
    window._heatSize = sz;
    try { localStorage.setItem('heat_size', sz); } catch (e) {}
    if (el && el.parentElement) el.parentElement.querySelectorAll('span[data-sz]').forEach(function (s) {
      var on = s.dataset.sz === sz; s.style.background = on ? '#1f6feb' : ''; s.style.color = on ? '#fff' : '#8b949e';
    });
    _s2Heat();
    // heatmap ki nayi height se right column stretch hota hai → equity chart ko nayi
    // height pe redraw karo (warna card ke andar jagah bachti/kam padti)
    setTimeout(function () {
      if (typeof drawEquityCurveChart === 'function') {
        try { drawEquityCurveChart('cal-equity-curve-container', window.currentCalendarTrades || []); } catch (e) {}
      }
    }, 80);
  };
  function _s2inrShort(v) { var s = v < 0 ? '−' : '+'; var a = Math.abs(v); return a >= 1000 ? (s + '₹' + (a / 1000).toFixed(1) + 'k') : (s + '₹' + Math.round(a)); }
  function _s2net(t) { return t._net != null ? t._net : (t.pnl || 0); }

  // 🔥 Heatmap — all-history month-wise NET ₹ (server), live/backtest same filters
  function _s2Heat() {
    // size buttons ka active state current size pe set karo
    document.querySelectorAll('#calp-heat span[data-sz]').forEach(function (s) {
      var on = s.dataset.sz === (window._heatSize || 'm');
      s.style.background = on ? '#1f6feb' : ''; s.style.color = on ? '#fff' : '#8b949e';
    });
    function render(M) {
      var years = Object.keys(M).sort();
      var body = document.getElementById('s2-heatbody'); if (!body) return;
      if (!years.length) { body.innerHTML = '<div style="color:#6e7681;font-size:11px;padding:12px">Is filter pe koi data nahi</div>'; return; }
      var mx = 1; years.forEach(function (y) { for (var m = 1; m <= 12; m++) if (M[y][m] != null) mx = Math.max(mx, Math.abs(M[y][m])); });
      var P = _HEAT_SZ[window._heatSize || 'm'] || _HEAT_SZ.m;
      var h = '<table style="font-size:' + P.tf + ';border-collapse:separate;border-spacing:' + P.sp + ';width:100%"><thead><tr><th></th>';
      _S2_MON.forEach(function (m) { h += '<th style="text-align:center;padding:' + P.th + ';color:#8b949e;font-size:' + P.thf + '">' + m + '</th>'; });
      h += '<th style="text-align:center;color:#8b949e;font-size:' + P.thf + ';padding:' + P.th + '">Year</th></tr></thead><tbody>';
      years.forEach(function (y) {
        h += '<tr><td style="color:#8b949e;font-weight:600;padding:' + P.lbl + ';font-size:' + P.lblf + '">' + y + '</td>'; var tot = 0;
        for (var m = 1; m <= 12; m++) {
          var v = M[y][m];
          if (v == null) { h += '<td style="padding:' + P.cell + ';text-align:center;color:#30363d;font-size:' + P.dot + '">·</td>'; continue; }
          tot += v; var a = Math.min(0.82, 0.12 + Math.abs(v) / mx * 0.7), bg = v >= 0 ? 'rgba(63,185,80,' + a + ')' : 'rgba(248,81,73,' + a + ')';
          h += '<td onclick="s2HeatClick(this)" data-ym="' + y + '-' + String(m).padStart(2, '0') + '" title="' + y + ' ' + _S2_MON[m - 1] + '" style="padding:' + P.cell + ';text-align:center;background:' + bg + ';color:#e6edf3;cursor:pointer;border-radius:4px;white-space:nowrap;font-weight:500;font-size:' + P.cf + '">' + _s2inrShort(v) + '</td>';
        }
        h += '<td style="padding:' + P.lbl + ';text-align:center;font-weight:700;white-space:nowrap;font-size:' + P.lblf + ';color:' + (tot >= 0 ? '#3fb950' : '#f85149') + '">' + _s2inrShort(tot) + '</td></tr>';
      });
      body.innerHTML = h + '</tbody></table>';
    }
    // BACKTEST: heatmap = POORE run ke saare years (date-range se independent) — full-run
    // calendar-summary fetch (bina year/month → route saare trades deta hai).
    if (window.calBtMode) {
      var sl = '';
      if (window.calActiveView && window.calActiveView.kind === 'bt') sl = (window.calActiveView.strategies || []).join(',');
      else {
        var st = (document.getElementById('cal-strat') || {}).value;
        if (st === '__ALL__') sl = (window._calBtRuns || []).map(function (r) { return r.slug; }).join(',');
        else if (st) sl = st;
      }
      var body0 = document.getElementById('s2-heatbody');
      if (!sl) { if (body0) render({}); return; }
      var bq = 'slug=' + encodeURIComponent(sl)
        + '&pass=' + ((typeof _calSegVal === 'function' ? _calSegVal('cal-bt-pass') : '') || 'bs')
        + '&period=' + ((typeof _calSegVal === 'function' ? _calSegVal('cal-bt-period') : '') || 'full');
      window._btHeatCache = window._btHeatCache || {};
      function _buildBt(trades) {
        var Mb = {};
        (trades || []).forEach(function (t) {
          var d = t.entry_date || t.exit_date || (t.entry_dt || t.exit_dt || '').slice(0, 10);
          if (!d || d.length < 7) return;
          var y = d.slice(0, 4), m = parseInt(d.slice(5, 7), 10); if (!m) return;
          var net = (t.pnl != null) ? t.pnl : ((t._net != null) ? t._net : 0);  // bt pnl already NET
          if (!Mb[y]) Mb[y] = {};
          Mb[y][m] = (Mb[y][m] || 0) + net;
        });
        render(Mb);
      }
      if (window._btHeatCache[bq]) { _buildBt(window._btHeatCache[bq]); return; }
      fetch('/api/backtest/calendar-summary?' + bq).then(function (r) { return r.json(); })
        .then(function (j) { window._btHeatCache[bq] = j.trades || []; _buildBt(j.trades || []); })
        .catch(function () { render({}); });
      return;
    }
    // LIVE/PAPER: monthly-returns endpoint (mode/source/strategy filtered)
    var qp = new URLSearchParams();
    var mode = (typeof _calSegVal === 'function') ? _calSegVal('cal-mode') : '';
    var src = (typeof _calSegVal === 'function') ? _calSegVal('cal-src') : '';
    if (mode) qp.set('mode', mode); if (src && src !== 'hedge') qp.set('source', src);
    var strat = (document.getElementById('cal-strat') || {}).value; if (strat) qp.set('strategy', strat);
    fetch('/api/orders/monthly-returns?' + qp.toString()).then(function (r) { return r.json(); })
      .then(function (j) { render(j.months || {}); }).catch(function () {});
  }
  window._s2Heat = _s2Heat;
  function _s2switchBottom(pane) {
    document.querySelectorAll('.s2tabs').forEach(function (tb) { if (tb.querySelector('[data-p^="tbl-"]')) tb.querySelectorAll('.s2tab').forEach(function (x) { x.classList.toggle('on', x.getAttribute('data-p') === pane); }); });
    document.querySelectorAll('.s2pane').forEach(function (p) { if (p.id.indexOf('tbl-') === 0) p.classList.toggle('on', p.id === pane); });
    ['ts', 'ppt', 'perf', 'bsdiv'].forEach(function (k) { var e = document.getElementById('s2-' + k + '-ctrl'); if (e) e.style.display = (pane === 'tbl-' + k) ? 'flex' : 'none'; });
    if (pane === 'tbl-bsdiv' && typeof bsRenderDivergence === 'function') bsRenderDivergence();
  }
  window.s2HeatClick = function (td) {
    var sel = td.getAttribute('data-sel');
    document.querySelectorAll('#s2-heatbody td').forEach(function (x) { x.style.outline = ''; x.removeAttribute('data-sel'); });
    if (sel) { window.s2ClearChip(); return; }
    td.style.outline = '2px solid #58a6ff'; td.setAttribute('data-sel', '1');
    var ym = td.getAttribute('data-ym');   // "2023-11"
    // Us month pe calendar navigate karo → uske trades LOAD (bt me full-run ke months
    // bhi jo visible range me nahi the) → points table + summary us month ke dikhaenge.
    var f = document.getElementById('cal-range-from'), t = document.getElementById('cal-range-to');
    if (f && t && ym && ym.length >= 7) {
      var yy = parseInt(ym.slice(0, 4), 10), mm = parseInt(ym.slice(5, 7), 10);
      var last = new Date(yy, mm, 0).getDate();
      f.value = ym + '-01';
      t.value = ym + '-' + String(last).padStart(2, '0');
      window._calRangeMode = true;
      if (typeof calendarRender === 'function') calendarRender();
    }
    _s2switchBottom('tbl-ppt');
    var c = document.getElementById('s2-fchip');
    c.innerHTML = '📅 ' + td.getAttribute('title') + ' <span onclick="s2ClearChip(event)" style="cursor:pointer;color:#f85149;font-weight:bold;margin-left:6px">✕</span>';
    c.style.display = 'inline-flex';
  };
  window.s2ClearChip = function (ev) {
    if (ev) ev.stopPropagation();
    document.getElementById('s2-fchip').style.display = 'none';
    document.querySelectorAll('#s2-heatbody td').forEach(function (x) { x.style.outline = ''; x.removeAttribute('data-sel'); });
    var f = document.getElementById('cal-range-from'), t = document.getElementById('cal-range-to');
    if (f) f.value = ''; if (t) t.value = '';
    window._calRangeMode = false;
    if (typeof clearCalPeriodFilter === 'function') clearCalPeriodFilter();
    if (typeof calendarRender === 'function') calendarRender();
  };

  // 📊 Distribution — real P&L histogram + table from the current trades
  function _s2Dist() {
    var tr = window.currentCalendarTrades || [];
    var edges = [-3000, -2000, -1000, 0, 1000, 2000, 3000];
    // full bucket-range labels (₹, Indian grouping)
    var labels = [
      '≤ −₹3,000', '−₹3,000 to −₹2,000', '−₹2,000 to −₹1,000', '−₹1,000 to ₹0',
      '₹0 to +₹1,000', '+₹1,000 to +₹2,000', '+₹2,000 to +₹3,000', '> +₹3,000'
    ];
    // short axis captions under each bar
    var axis = ['≤−3k', '−3k↔−2k', '−2k↔−1k', '−1k↔0', '0↔+1k', '+1k↔+2k', '+2k↔+3k', '>+3k'];
    var counts = new Array(8).fill(0);
    tr.forEach(function (t) { var p = _s2net(t), b = 0; while (b < edges.length && p >= edges[b]) b++; counts[b]++; });
    var tot = tr.length || 1, max = Math.max.apply(null, counts) || 1;
    // HTML bar chart (each column: count on top, bar, full-range axis caption below)
    var bars = '<div style="display:flex;align-items:flex-end;gap:3px;height:132px;padding:2px 2px 0">';
    counts.forEach(function (c, i) {
      var bh = Math.round((c / max) * 96);
      bars += '<div title="' + labels[i] + ': ' + c + ' trades" style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%">' +
        '<div style="font-size:9px;color:#8b949e;margin-bottom:2px">' + (c || '') + '</div>' +
        '<div style="width:100%;height:' + bh + 'px;background:' + (i < 4 ? '#f85149' : '#3fb950') + ';opacity:.85;border-radius:3px 3px 0 0"></div>' +
        '<div style="font-size:7.5px;color:#6e7681;margin-top:3px;text-align:center;line-height:1.1;white-space:nowrap">' + axis[i] + '</div>' +
        '</div>';
    });
    bars += '</div>';
    var s = bars + '<div style="overflow-x:auto;margin-top:10px"><table style="width:100%;border-collapse:collapse;font-size:10.5px;text-align:left"><thead><tr><th style="padding:5px 8px;color:#8b949e">P&L bucket (net ₹)</th><th style="padding:5px 8px;color:#8b949e;text-align:right">Trades</th><th style="padding:5px 8px;color:#8b949e;text-align:right">%</th></tr></thead><tbody>';
    counts.forEach(function (c, i) { s += '<tr><td style="padding:5px 8px;color:' + (i < 4 ? '#f85149' : '#3fb950') + '">' + labels[i] + '</td><td style="padding:5px 8px;text-align:right">' + c + '</td><td style="padding:5px 8px;text-align:right;color:#8b949e">' + (c / tot * 100).toFixed(1) + '%</td></tr>'; });
    document.getElementById('s2-dist-body').innerHTML = s + '</tbody></table></div>';
  }

  // 📈 Performance — real stats computed from the current trades
  function _s2Perf() {
    var tr = window.currentCalendarTrades || [];
    var n = tr.length, net = 0, fees = 0, gWin = 0, gLoss = 0, wins = 0, losses = 0, best = -Infinity, worst = Infinity;
    var lN = 0, lW = 0, sN = 0, sW = 0, wStreak = 0, lStreak = 0, curW = 0, curL = 0;
    var byDay = {};
    var ordered = tr.slice().sort(function (a, b) { return ((a.exit_date || '') + (a.exit_time || '')).localeCompare((b.exit_date || '') + (b.exit_time || '')); });
    ordered.forEach(function (t) {
      var p = _s2net(t); net += p; fees += (t._tax || t.fee || 0);
      if (p > 0) { gWin += p; wins++; } else if (p < 0) { gLoss += -p; losses++; }
      if (p > best) best = p; if (p < worst) worst = p;
      var isLong = (t.entry === 'BUY'); if (isLong) { lN++; if (p > 0) lW++; } else { sN++; if (p > 0) sW++; }
      if (p >= 0) { curW++; curL = 0; if (curW > wStreak) wStreak = curW; } else { curL++; curW = 0; if (curL > lStreak) lStreak = curL; }
      var d = t.entry_date || t.exit_date; if (d) byDay[d] = (byDay[d] || 0) + p;
    });
    // equity peak-trough max drawdown (₹)
    var eq = 0, peak = 0, mdd = 0; ordered.forEach(function (t) { eq += _s2net(t); if (eq > peak) peak = eq; var dd = peak - eq; if (dd > mdd) mdd = dd; });
    // daily-net annualised Sharpe / Sortino
    var days = Object.values(byDay), sharpe = null, sortino = null;
    if (days.length > 1) {
      var mean = days.reduce(function (a, b) { return a + b; }, 0) / days.length;
      var sd = Math.sqrt(days.reduce(function (a, b) { return a + (b - mean) * (b - mean); }, 0) / (days.length - 1));
      var dn = days.filter(function (x) { return x < 0; });
      var dsd = dn.length ? Math.sqrt(dn.reduce(function (a, b) { return a + b * b; }, 0) / dn.length) : 0;
      if (sd > 0) sharpe = (mean / sd) * Math.sqrt(252);
      if (dsd > 0) sortino = (mean / dsd) * Math.sqrt(252);
    }
    var pf = gLoss > 0 ? (gWin / gLoss) : (gWin > 0 ? Infinity : 0);
    var avgWin = wins ? gWin / wins : 0, avgLoss = losses ? gLoss / losses : 0;
    var inr = function (v) { return (v < 0 ? '−₹' : '₹') + Math.abs(Math.round(v)).toLocaleString('en-IN'); };
    var P = [
      ['Total Trades', n.toLocaleString('en-IN')],
      ['Net P&L', inr(net), net >= 0 ? 'p' : 'n'],
      ['Total Fees', inr(fees)],
      ['Max Drawdown', '−' + inr(mdd).replace('₹', '₹'), 'n'],
      ['Profit Factor', isFinite(pf) ? pf.toFixed(2) : '∞'],
      ['Win-rate', n ? (wins / n * 100).toFixed(1) + '%' : '—'],
      ['W / L', wins + ' / ' + losses],
      ['Expectancy', inr(n ? net / n : 0)],
      ['Avg Win | Loss', inr(avgWin) + ' | −' + inr(avgLoss).replace('₹', '₹')],
      ['Best | Worst', inr(best === -Infinity ? 0 : best) + ' | ' + inr(worst === Infinity ? 0 : worst)],
      ['Win-rate L | S', (lN ? (lW / lN * 100).toFixed(0) : '—') + '% | ' + (sN ? (sW / sN * 100).toFixed(0) : '—') + '%'],
      ['Longs | Shorts', (n ? (lN / n * 100).toFixed(0) : 0) + '% | ' + (n ? (sN / n * 100).toFixed(0) : 0) + '%'],
      ['Sharpe (daily)', sharpe == null ? '—' : sharpe.toFixed(2)],
      ['Sortino (daily)', sortino == null ? '—' : sortino.toFixed(2)],
      ['Winning Streak', wStreak],
      ['Losing Streak', lStreak]
    ];
    document.getElementById('s2-perf-body').innerHTML = P.map(function (r) {
      var c = r[2] === 'p' ? 'color:#3fb950' : r[2] === 'n' ? 'color:#f85149' : '';
      return '<div class="s2prow"><span class="pk">' + r[0] + '</span><span class="pv" style="' + c + '">' + r[1] + '</span></div>';
    }).join('');
  }

  // ── boot ───────────────────────────────────────────────────────────────────
  function boot() {
    if (typeof regLoad === 'function') { try { regLoad(); } catch (e) {} }
    if (typeof _loadCalPointsColPrefs === 'function') { try { _loadCalPointsColPrefs(); } catch (e) {} }  // init points-table cols
    _s2Heat();               // heatmap is the default calendar view
    // first render (live/paper mode — cal-view=live, cal-mode=All by default)
    if (typeof calendarRender === 'function') calendarRender();
    if (typeof loadCalViews === 'function') { try { loadCalViews(); } catch (e) {} }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
