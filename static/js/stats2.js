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
    if (mode === 'bt') {
      if (srcWrap) srcWrap.style.display = 'none'; if (srcLbl) srcLbl.style.display = 'none';
      calSetView('bt', document.querySelector('#cal-view span[data-v="bt"]'));   // shows bt controls + loads runs + renders
    } else {
      if (srcWrap) srcWrap.style.display = ''; if (srcLbl) srcLbl.style.display = '';
      _setSeg('cal-mode', mode === 'paper' ? 'paper' : mode === 'live' ? 'live' : '');
      calSetView('live', document.querySelector('#cal-view span[data-v="live"]'));  // resets + calendarRender (reads cal-mode)
    }
  };
  // visible Source control mirrors the hidden #cal-src
  window.s2Src = function (val, el) {
    var p = document.getElementById('s2-src');
    p.querySelectorAll('span').forEach(function (x) { x.classList.remove('on'); x.style.color = '#8b949e'; });
    el.classList.add('on'); el.style.color = '';
    _setSeg('cal-src', val); calendarRender();
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
      if (pane === 'tbl-perf') _s2Perf();
    }
  };

  // ── ⛶ full-screen the active chart ────────────────────────────────────────
  window.s2Fs = function () {
    var pane = document.querySelector('#chart-eq.on, #chart-gl.on, #chart-dist.on, #chart-tc.on');
    if (!pane) return;
    var titleTab = document.querySelector('.s2tabs .s2tab.on');
    document.getElementById('s2fstitle').textContent = titleTab ? titleTab.textContent.trim() : 'Chart';
    var body = document.getElementById('s2fsbody');
    body.innerHTML = '<div style="width:100%;height:100%">' + pane.innerHTML + '</div>';
    document.getElementById('s2fsov').style.display = 'flex';
  };
  window.s2FsClose = function (ev) {
    if (ev && ev.target && ev.target.id !== 's2fsov' && !(ev.target.textContent || '').includes('Close')) return;
    document.getElementById('s2fsov').style.display = 'none';
  };

  // ── inline Trade Chart: reuse /trade-chart in an iframe (override openTradeChart) ──
  var _origOpenTradeChart = window.openTradeChart;
  window.openTradeChart = function (sym, side, entry, exit, et, xt, qty, date, tf, ind, idx, sl, tp, strategy) {
    var params = { sym: sym, side: side, entry: entry, exit: exit, et: et, xt: xt, qty: qty };
    if (date) params.date = date; if (tf) params.tf = tf; if (ind) params.ind = ind;
    if (idx != null) params.idx = idx; if (sl != null && sl !== '') params.sl = sl;
    if (tp != null && tp !== '') params.tp = tp; if (strategy) params.strategy = strategy;
    params.embed = 1;
    var q = new URLSearchParams(params).toString();
    document.getElementById('s2-tc-hint').style.display = 'none';
    document.getElementById('s2-tc-body').innerHTML =
      '<div style="font-size:11px;color:#8b949e;margin-bottom:6px">🕯️ <b style="color:#e6edf3">' + (sym || '') + '</b> · ' + (date || '') + '</div>' +
      '<iframe src="/trade-chart?' + q + '" style="width:100%;height:220px;border:0;border-radius:8px;background:#0d1117"></iframe>';
    // switch chart panel to Trade Chart tab
    document.querySelectorAll('.s2tabs').forEach(function (tb) {
      if (tb.querySelector('[data-p^="chart-"]')) tb.querySelectorAll('.s2tab').forEach(function (x) { x.classList.toggle('on', x.getAttribute('data-p') === 'chart-tc'); });
    });
    document.querySelectorAll('.s2pane').forEach(function (p) { if (p.id.indexOf('chart-') === 0) p.classList.toggle('on', p.id === 'chart-tc'); });
    var gl = document.getElementById('s2-glseg'); if (gl) gl.style.display = 'none';
  };

  // ── NEW panels (Phase-1 sample data; Phase-2 = real backend) ───────────────
  var _S2_MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function _s2Heat() {
    // TODO Phase-2: real monthly returns (order_store / backtest `monthly`). Sample for now.
    var HD = { 2024:[2.8,3.2,2.2,1.0,1.7,-0.1,-1.2,1.3,0.4,0.9,1.6,0.6],
               2025:[1.3,-1.3,-0.2,1.1,-1.5,1.3,1.5,1.3,-0.6,0.7,0.8,-0.9],
               2026:[2.5,1.2,2.8,-0.9,3.2,1.1,-0.5,null,null,null,null,null] };
    var h = '<table style="font-size:10.5px;border-collapse:separate;border-spacing:2px"><thead><tr><th></th>';
    _S2_MON.forEach(function (m) { h += '<th style="text-align:center;padding:3px 4px;color:#8b949e">' + m + '</th>'; });
    h += '<th style="text-align:center;color:#8b949e">Year</th></tr></thead><tbody>';
    Object.keys(HD).forEach(function (y) {
      h += '<tr><td style="color:#8b949e;font-weight:600;padding:3px 6px">' + y + '</td>'; var tot = 0;
      HD[y].forEach(function (v, mi) {
        if (v == null) { h += '<td style="padding:4px 3px;text-align:center;color:#30363d">·</td>'; return; }
        tot += v; var a = Math.min(0.8, 0.12 + Math.abs(v) / 5), bg = v >= 0 ? 'rgba(63,185,80,' + a + ')' : 'rgba(248,81,73,' + a + ')';
        h += '<td onclick="s2HeatClick(this)" title="' + y + ' ' + _S2_MON[mi] + '" style="padding:4px 5px;text-align:center;background:' + bg + ';color:#e6edf3;cursor:pointer;border-radius:3px">' + (v >= 0 ? '+' : '') + v.toFixed(1) + '</td>';
      });
      h += '<td style="padding:4px 6px;text-align:center;font-weight:700;color:' + (tot >= 0 ? '#3fb950' : '#f85149') + '">' + (tot >= 0 ? '+' : '') + tot.toFixed(1) + '</td></tr>';
    });
    document.getElementById('s2-heatbody').innerHTML = h + '</tbody></table>';
  }
  window.s2HeatClick = function (td) {
    var was = td.style.outline;
    document.querySelectorAll('#s2-heatbody td').forEach(function (x) { x.style.outline = ''; });
    var c = document.getElementById('s2-fchip');
    if (was) { c.style.display = 'none'; return; }
    td.style.outline = '2px solid #58a6ff';
    c.innerHTML = '📅 ' + td.getAttribute('title') + ' <span onclick="s2ClearChip(event)" style="cursor:pointer;color:#f85149;font-weight:bold;margin-left:6px">✕</span>';
    c.style.display = 'inline-flex';
    // TODO Phase-2: actually filter the bottom table to this month
  };
  window.s2ClearChip = function (ev) {
    if (ev) ev.stopPropagation();
    document.getElementById('s2-fchip').style.display = 'none';
    document.querySelectorAll('#s2-heatbody td').forEach(function (x) { x.style.outline = ''; });
  };
  function _s2Dist() {
    // TODO Phase-2: real P&L distribution from currentCalendarTrades. Sample for now.
    var b = [{ l: '< −3k', c: 8, n: 1 }, { l: '−3k…−2k', c: 22, n: 1 }, { l: '−2k…−1k', c: 55, n: 1 }, { l: '−1k…0', c: 120, n: 1 },
             { l: '0…+1k', c: 145, n: 0 }, { l: '+1k…+2k', c: 95, n: 0 }, { l: '+2k…+3k', c: 40, n: 0 }, { l: '> +3k', c: 15, n: 0 }];
    var tot = b.reduce(function (a, x) { return a + x.c; }, 0), max = Math.max.apply(null, b.map(function (x) { return x.c; }));
    var W = 360, H = 118, pad = 6, bw = (W - 2 * pad) / b.length, s = '<svg style="width:100%;height:118px" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">';
    b.forEach(function (x, i) { var bh = (x.c / max) * (H - 14), bx = pad + i * bw + 3; s += '<rect x="' + bx + '" y="' + (H - bh) + '" width="' + (bw - 6) + '" height="' + bh + '" fill="' + (x.n ? '#f85149' : '#3fb950') + '" opacity=".85" rx="2"/>'; });
    s += '</svg><div style="overflow-x:auto;margin-top:8px"><table style="width:100%;border-collapse:collapse;font-size:10.5px;text-align:left"><thead><tr><th style="padding:5px 8px;color:#8b949e">P&L bucket</th><th style="padding:5px 8px;color:#8b949e;text-align:right">Trades</th><th style="padding:5px 8px;color:#8b949e;text-align:right">%</th></tr></thead><tbody>';
    b.forEach(function (x) { s += '<tr><td style="padding:5px 8px;color:' + (x.n ? '#f85149' : '#3fb950') + '">' + x.l + '</td><td style="padding:5px 8px;text-align:right">' + x.c + '</td><td style="padding:5px 8px;text-align:right;color:#8b949e">' + (x.c / tot * 100).toFixed(1) + '%</td></tr>'; });
    document.getElementById('s2-dist-body').innerHTML = s + '</tbody></table></div>';
  }
  function _s2Perf() {
    // TODO Phase-2: real performance stats (backtest report / order_store stats). Sample for now.
    var P = [['Total Closed Trades', '1,806'], ['Total Net Profit', '₹6,91,325 (69.1%)', 'p'], ['Start → Finish', '₹10.0L → ₹16.9L'],
      ['Total Paid Fees', '₹1,04,766'], ['Max Drawdown', '−3.1%', 'n'], ['Max Underwater', '235 days'], ['CAGR', '6.4%', 'p'],
      ['Expectancy', '₹383'], ['Avg Win | Loss', '₹2,918 | −₹1,290'], ['Ratio Win/Loss', '2.26'], ['Profit Factor', '1.49'],
      ['Win-rate', '39.8%'], ['Win-rate L | S', '40.5% | 39.1%'], ['Longs | Shorts', '45% | 55%'], ['Avg Holding', '32 bars (2.7h)'],
      ['Sharpe', '1.95'], ['Calmar', '2.06'], ['Sortino', '5.36'], ['Winning Streak', '10'], ['Losing Streak', '7']];
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
