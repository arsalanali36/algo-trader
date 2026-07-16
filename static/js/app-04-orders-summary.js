// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    function getStratColor(type) {
      if (type === 'ema') return '#1f6feb';
      if (type === 'rsi') return '#d29922';
      if (type === 'range') return '#3fb950';
      return '#8b949e';
    }

    // CONTROL TAB
    function renderControlTab() {
      const container = document.getElementById('control-grid');
      let html = '';
      // webhook strategies aren't process-based (no separate script — they run
      // inside this dashboard process, controlled by the 🔗 Webhook tab's own
      // active/paper-live toggle) and "_risk"/"webhooks" are config-only keys, not
      // strategies at all — none of these belong in the Paper/Live/Stop grid.
      const _skipTypes = new Set(['webhook', '_risk', 'webhooks']);
      Object.keys(GLOBAL_CONFIG).forEach(key => {
        const type = key.split('_')[0];
        if (_skipTypes.has(type) || key === '_risk' || key === 'webhooks') return;
        if (regHidden(key)) return;   // dead/garbage ids — registry _meta.hidden
        const color = getStratColor(type);
        const pid = RUNNING_PIDS[key];
        const mode = pid ? `Running (PID ${pid})` : 'Stopped';
        html += `
      <div class="strat-card" style="border-color:${color}">
        <div class="strat-title">
          <span class="dot ${pid ? 'on' : ''}"></span>
          <span style="color:${color}">${key.toUpperCase()}</span>
        </div>
        <div class="strat-mode">${mode}</div>
        <div class="btn-row">
          <button class="btn btn-green" onclick="startBot('${key}','paper')">▶ Paper</button>
          <button class="btn btn-blue" onclick="startBot('${key}','live')">💰 Live</button>
          <button class="btn btn-red" onclick="stopBot('${key}')">⏹ Stop</button>
        </div>
      </div>
    `;
      });
      container.innerHTML = html;
    }

    // P&L TAB
    function renderPnlTab() {
      // no-op — P&L rendering lives in ordersRender() (Orders & P&L tab)
    }

    // ── Consolidated strategy summary table (task 66) ──
    // Replaces the old #ord-summary tiles. Lives in the Peak P&L card's
    // "Summary" view; each row = one strategy (or MANUAL/WEBHOOK) with a P&L
    // magnitude bar (green right / red left of centre — like the lab's freq col).
    function _summaryModeBadge(mode) {
      const base = 'font-size:9px;font-weight:bold;padding:1.5px 5px;border-radius:3px;text-transform:uppercase;';
      if (mode === 'live') return `<span style="background:#238636;color:#fff;${base}">Live</span>`;
      if (mode === 'paper') return `<span style="background:#d29922;color:#0d1117;${base}">Paper</span>`;
      if (mode === 'stopped') return `<span style="background:#30363d;color:#8b949e;${base}">Stopped</span>`;
      return '<span style="color:#6e7681;font-size:10px">—</span>';
    }
    function renderStratSummaryTable(rows) {
      const el = document.getElementById('peak-summary-body');
      if (!el) return;
      if (!rows || !rows.length) {
        el.innerHTML = '<div style="color:#6e7681;font-size:12px;padding:16px">Is din koi trade nahi</div>';
        return;
      }
      const sel = window._peakStrat || '__all';
      const tot = { net: 0, gross: 0, n: 0, w: 0, opn: 0, ru: 0, rd: 0, tax: 0 };
      rows.forEach(r => { tot.net += r.net; tot.gross += r.gross; tot.n += r.n; tot.w += r.w; tot.opn += r.opn; tot.ru += (r.ru || 0); tot.rd += (r.rd || 0); tot.tax += (r.tax || 0); });
      const netCell = net => `<span style="color:${net >= 0 ? '#3fb950' : '#f85149'};font-weight:600">${net >= 0 ? '+' : ''}${Math.round(net).toLocaleString('en-IN')}</span>`;
      // task 80 — open positions ka LIVE unrealized (₹), _patchPeakRunCells se update hota hai
      const runCell = key => `<span class="peak-run-cell" data-pk="${key}" style="color:#6e7681;font-weight:600">—</span>`;
      const taxCell = tx => `<span style="color:#8b949e">${tx ? '-' + Math.round(tx).toLocaleString('en-IN') : '0'}</span>`;
      // Run-Up (favourable) / Run-Down (adverse) ₹ pair — replaces the old faint bar
      const rudCell = (ru, rd) => `<span style="color:#3fb950;font-weight:600">+${Math.round(ru || 0).toLocaleString('en-IN')}</span>`
        + `<span style="color:#6e7681"> / </span>`
        + `<span style="color:#f85149;font-weight:600">${Math.round(rd || 0).toLocaleString('en-IN')}</span>`;
      let h = `<table style="width:100%;border-collapse:collapse;font-size:12px">
        <thead><tr style="color:#8b949e;text-align:left;border-bottom:1px solid #30363d;font-size:11px">
          <th style="padding:6px 8px;font-weight:500">Strategy</th>
          <th style="padding:6px 8px;font-weight:500">Mode</th>
          <th style="padding:6px 8px;font-weight:500;text-align:right">Net</th>
          <th style="padding:6px 8px;font-weight:500;text-align:right" title="Open positions ka live unrealized P&amp;L (₹) — LTP feed se">Running</th>
          <th style="padding:6px 8px;font-weight:500;text-align:right">Gross</th>
          <th style="padding:6px 8px;font-weight:500;text-align:right" title="Completed trades ka total tax &amp; charges">Tax</th>
          <th style="padding:6px 8px;font-weight:500;text-align:center">Trades</th>
          <th style="padding:6px 8px;font-weight:500;text-align:center">W/L</th>
          <th style="padding:6px 8px;font-weight:500;text-align:center">Open</th>
          <th style="padding:6px 8px;font-weight:500;text-align:right">Run-Up / Run-Down</th>
        </tr></thead><tbody>`;
      rows.forEach(r => {
        const isSel = (r.key === sel);
        const rowStyle = 'border-bottom:1px solid #21262d;cursor:pointer'
          + (isSel ? ';background:#132030;box-shadow:inset 3px 0 0 #1f6feb' : '');
        const codeHtml = r.code && r.code !== r.key
          ? `<span style="color:#6e7681;font-variant-numeric:tabular-nums;margin-right:7px;font-size:10.5px">${r.code}</span>` : '';
        h += `<tr style="${rowStyle}" onclick="peakPickStrat('${r.key}', ${r.isSource ? 'true' : 'false'})"
            onmouseover="if('${r.key}'!=='${sel}')this.style.background='#1c2230'" onmouseout="if('${r.key}'!=='${sel}')this.style.background=''"
            title="Click → is strategy ka MTM graph + Completed Trades filter">
          <td style="padding:7px 8px">${codeHtml}<span style="color:#adbac7">${r.name}</span></td>
          <td style="padding:7px 8px">${_summaryModeBadge(r.mode)}</td>
          <td style="padding:7px 8px;text-align:right">${netCell(r.net)}</td>
          <td style="padding:7px 8px;text-align:right">${runCell(r.key)}</td>
          <td style="padding:7px 8px;text-align:right;color:#8b949e">${Math.round(r.gross).toLocaleString('en-IN')}</td>
          <td style="padding:7px 8px;text-align:right">${taxCell(r.tax)}</td>
          <td style="padding:7px 8px;text-align:center;color:#adbac7">${r.n}</td>
          <td style="padding:7px 8px;text-align:center;color:#8b949e">${r.w}W ${r.n - r.w}L</td>
          <td style="padding:7px 8px;text-align:center;color:#8b949e">${r.opn}</td>
          <td style="padding:7px 8px;text-align:right">${rudCell(r.ru, r.rd)}</td></tr>`;
      });
      h += `<tr style="border-top:2px solid #30363d;font-weight:600">
          <td style="padding:8px" colspan="2"><span style="color:#8b949e;font-size:11px">TOTAL</span></td>
          <td style="padding:8px;text-align:right">${netCell(tot.net)}</td>
          <td style="padding:8px;text-align:right">${runCell('__tot')}</td>
          <td style="padding:8px;text-align:right;color:#8b949e">${Math.round(tot.gross).toLocaleString('en-IN')}</td>
          <td style="padding:8px;text-align:right">${taxCell(tot.tax)}</td>
          <td style="padding:8px;text-align:center;color:#adbac7">${tot.n}</td>
          <td style="padding:8px;text-align:center;color:#8b949e">${tot.w}W ${tot.n - tot.w}L</td>
          <td style="padding:8px;text-align:center;color:#8b949e">${tot.opn}</td>
          <td style="padding:8px;text-align:right">${rudCell(tot.ru, tot.rd)}</td></tr>`;
      h += '</tbody></table>';
      el.innerHTML = h;
      _patchPeakRunCells();   // task 80 — fill Running from whatever LTPs are already cached
    }

    // task 80 — Summary "Running" column: per-strategy open-position unrealized ₹,
    // computed from the SAME _ltpLive feed that patches the Open Positions table.
    // Called on summary render + every _patchLtpCells tick.
    function _patchPeakRunCells() {
      const map = window._peakOpenMap || {};
      if (typeof _ltpLive !== 'object') return;
      let grand = 0, grandAny = false;
      document.querySelectorAll('.peak-run-cell').forEach(el => {
        const k = el.getAttribute('data-pk');
        if (k === '__tot') return;
        const rows = map[k] || [];
        if (!rows.length) { el.textContent = '—'; el.style.color = '#6e7681'; return; }
        let tot = 0, any = false;
        rows.forEach(o => {
          const raw = _ltpLive[o.sym];
          const ltp = typeof raw === 'number' ? raw : (raw && raw.ltp);
          if (ltp == null || !o.entry) return;
          any = true;
          const pts = o.side === 'BUY' ? (ltp - o.entry) : (o.entry - ltp);
          tot += pts * o.qty;
        });
        if (!any) { el.textContent = '⏳'; el.style.color = '#6e7681'; return; }
        grand += tot; grandAny = true;
        el.textContent = (tot >= 0 ? '+' : '') + Math.round(tot).toLocaleString('en-IN');
        el.style.color = tot >= 0 ? '#3fb950' : '#f85149';
      });
      const tEl = document.querySelector('.peak-run-cell[data-pk="__tot"]');
      if (tEl) {
        if (grandAny) {
          tEl.textContent = (grand >= 0 ? '+' : '') + Math.round(grand).toLocaleString('en-IN');
          tEl.style.color = grand >= 0 ? '#3fb950' : '#f85149';
        } else { tEl.textContent = '—'; tEl.style.color = '#6e7681'; }
      }
    }
    // ── Today's Peak card state (tasks 73/74) ──
    window._peakStrat = '__all';                                   // selected strategy for the Graph view
    window._peakPMode = localStorage.getItem('peak_pmode') || 'cur';   // realised | unreal | cur
    window._marginMode = localStorage.getItem('peak_margin_mode') || 'all'; // all | paper | live

    function togglePeakView(v) {
      const bodies = { graph: 'peak-graph-body', summary: 'peak-summary-body', margin: 'peak-margin-body' };
      const tabs = { graph: 'peak-tab-graph', summary: 'peak-tab-summary', margin: 'peak-tab-margin' };
      if (!['graph', 'summary', 'margin'].includes(v)) v = 'graph';
      const on = 'font-size:11px;padding:4px 12px;cursor:pointer;background:#1f6feb;color:#fff';
      const off = 'font-size:11px;padding:4px 12px;cursor:pointer;background:#21262d;color:#8b949e';
      Object.keys(bodies).forEach(k => {
        const b = document.getElementById(bodies[k]), t = document.getElementById(tabs[k]);
        if (b) b.style.display = (k === v) ? 'block' : 'none';
        if (t) t.style.cssText = (k === v) ? on : off;
      });
      try { localStorage.setItem('peak_view', v); } catch (e) { }
      if (v === 'graph') loadPeakGraph();
      if (v === 'margin') loadMarginGraph();
    }
    // restore last-used view on load (elements are static in the DOM)
    // Deferred to DOMContentLoaded since the split: togglePeakView -> loadPeakGraph
    // -> regLabel, which lives in app-05, i.e. a file that hasn't loaded when this
    // line runs. The try/catch would have swallowed the ReferenceError whole and
    // the view just wouldn't restore, with nothing in the console to say why.
    document.addEventListener('DOMContentLoaded', () => {
      try { const _pv = localStorage.getItem('peak_view'); if (_pv && _pv !== 'graph') togglePeakView(_pv); } catch (e) { }
    });
    // sync sub-switch highlights to stored prefs (no data load — each view's own loader runs)
    try {
      const _on = 'font-size:11px;padding:3px 10px;cursor:pointer;background:#1f6feb;color:#fff';
      const _off = 'font-size:11px;padding:3px 10px;cursor:pointer;background:#21262d;color:#8b949e';
      ['real', 'unreal', 'cur'].forEach(x => { const e = document.getElementById('peak-pm-' + x); if (e) e.style.cssText = (x === window._peakPMode) ? _on : _off; });
      ['all', 'paper', 'live'].forEach(x => { const e = document.getElementById('peak-mm-' + x); if (e) e.style.cssText = (x === window._marginMode) ? _on : _off; });
    } catch (e) { }

    // Realised / Unrealised / Current switch (Graph view) — task 73
    function setPeakPMode(m) {
      window._peakPMode = m;
      try { localStorage.setItem('peak_pmode', m); } catch (e) { }
      const on = 'font-size:11px;padding:3px 10px;cursor:pointer;background:#1f6feb;color:#fff';
      const off = 'font-size:11px;padding:3px 10px;cursor:pointer;background:#21262d;color:#8b949e';
      ['real', 'unreal', 'cur'].forEach(x => { const e = document.getElementById('peak-pm-' + x); if (e) e.style.cssText = (x === m) ? on : off; });
      loadPeakGraph();
    }

    // Click a Summary row → filter Completed Trades to that strategy (client-side,
    // so the Summary table itself stays full) + show its MTM graph. Re-click the
    // same row (or "clear") → back to All. isSource=true for MANUAL/WEBHOOK rows.
    function peakPickStrat(key, isSource) {
      window._peakStrat = (window._peakStrat === key) ? '__all' : key;   // toggle back to All on re-click
      const picked = window._peakStrat;
      const nm = isSource ? (key.charAt(0) + key.slice(1).toLowerCase()) : (regLabel(key) || key);
      const sc = document.getElementById('peak-scope');
      if (sc) sc.textContent = picked === '__all' ? '' : '— ' + nm;
      renderCachedOrders();    // re-filter Completed Trades + re-highlight summary row
      togglePeakView('graph'); // loadPeakGraph runs inside
    }

    function peakClearStrat() {
      window._peakStrat = '__all';
      const sc = document.getElementById('peak-scope'); if (sc) sc.textContent = '';
      renderCachedOrders();
      if ((localStorage.getItem('peak_view') || 'graph') === 'graph') loadPeakGraph();
    }

    // Does a completed/open trade belong to the currently-picked strategy scope?
    function _peakTradeMatch(t) {
      const pk = window._peakStrat || '__all';
      if (pk === '__all') return true;
      const src = String(t.source || 'STRATEGY').toUpperCase();
      if (pk === 'MANUAL' || pk === 'WEBHOOK') return src === pk;
      return (t.strategy || 'STRATEGY') === pk && src !== 'MANUAL' && src !== 'WEBHOOK';
    }

    function peakRefresh() {
      const v = localStorage.getItem('peak_view') || 'graph';
      if (v === 'margin') loadMarginGraph(); else loadPeakGraph();
    }

    // ── Margin Utilization graph (task 74) ──
    function setMarginMode(m) {
      window._marginMode = m;
      try { localStorage.setItem('peak_margin_mode', m); } catch (e) { }
      const on = 'font-size:11px;padding:3px 10px;cursor:pointer;background:#1f6feb;color:#fff';
      const off = 'font-size:11px;padding:3px 10px;cursor:pointer;background:#21262d;color:#8b949e';
      ['all', 'paper', 'live'].forEach(x => { const e = document.getElementById('peak-mm-' + x); if (e) e.style.cssText = (x === m) ? on : off; });
      loadMarginGraph();
    }

    async function loadMarginGraph() {
      const container = document.getElementById('peak-margin-graph');
      if (!container) return;
      const selDate = (document.getElementById('ord-date') || {}).value || '';
      let resp = {};
      try {
        const qs = new URLSearchParams();
        if (selDate) qs.set('date', selDate);
        qs.set('mode', window._marginMode || 'all');
        const r = await fetch('/api/margin-history?' + qs.toString());
        resp = await r.json();
      } catch (e) {
        container.innerHTML = '<div style="color:#f85149;font-size:12px;padding:20px">Load failed</div>';
        return;
      }
      const T = resp.times || [], buy = resp.buy || [], sell = resp.sell || [];
      const pk = document.getElementById('peak-margin-peak');
      if (pk) pk.textContent = T.length ? ('Peak margin: ₹' + Math.round(resp.peak || 0).toLocaleString('en-IN') + '  (' + (window._marginMode || 'all') + ')') : '';
      if (!T.length || Math.max(0, ...buy, ...sell) <= 0) {
        container.innerHTML = '<div style="color:#8b949e;font-size:12px;padding:20px">Is din koi margin use nahi hua (' + (window._marginMode || 'all') + ').</div>';
        return;
      }
      const W = container.clientWidth || 700, H = 200;
      const PAD = { t: 14, r: 70, b: 26, l: 64 }, gW = W - PAD.l - PAD.r, gH = H - PAD.t - PAD.b;
      const tot = T.map((_, i) => (buy[i] || 0) + (sell[i] || 0));
      const maxV = Math.max(1, ...tot);
      const px = i => PAD.l + (T.length === 1 ? 0 : i / (T.length - 1) * gW);
      const py = v => PAD.t + gH - (v / maxV) * gH;
      const toMin = s => { const p = String(s).split(':'); return (+p[0] || 0) * 60 + (+p[1] || 0); };
      // x by real time so widths reflect how long margin was held
      const t0 = toMin(T[0]), t1 = toMin(T[T.length - 1]) || t0 + 1;
      const xAt = i => PAD.l + ((toMin(T[i]) - t0) / ((t1 - t0) || 1)) * gW;
      const areaPath = (topArr, botArr) => {
        let p = `M${xAt(0).toFixed(1)},${py(botArr[0]).toFixed(1)}`;
        for (let i = 0; i < T.length; i++) p += ` L${xAt(i).toFixed(1)},${py(topArr[i]).toFixed(1)}`;
        for (let i = T.length - 1; i >= 0; i--) p += ` L${xAt(i).toFixed(1)},${py(botArr[i]).toFixed(1)}`;
        return p + ' Z';
      };
      const sellTop = sell.map(v => v || 0);
      const buyTop = T.map((_, i) => (sell[i] || 0) + (buy[i] || 0));
      const zeros = T.map(() => 0);
      let yl = '';
      for (let s = 0; s <= 4; s++) {
        const v = maxV * s / 4, y = py(v);
        yl += `<line x1="${PAD.l}" y1="${y.toFixed(1)}" x2="${W - PAD.r}" y2="${y.toFixed(1)}" stroke="#161b22"/>`;
        yl += `<text x="${PAD.l - 5}" y="${(y + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="#8b949e">₹${(v / 100000).toFixed(1)}L</text>`;
      }
      let xl = '';
      const step = Math.max(1, Math.floor(T.length / 6));
      for (let i = 0; i < T.length; i += step) xl += `<text x="${xAt(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" font-size="10" fill="#6e7681">${T[i]}</text>`;
      const lastTot = tot[tot.length - 1];
      container.innerHTML = `
  <svg width="${W}" height="${H}" style="overflow:visible;display:block">
    ${yl}${xl}
    <path d="${areaPath(sellTop, zeros)}" fill="#d2992255" stroke="#d29922" stroke-width="1"/>
    <path d="${areaPath(buyTop, sellTop)}" fill="#1f6feb55" stroke="#1f6feb" stroke-width="1"/>
    <text x="${(W - PAD.r + 6).toFixed(1)}" y="${(py(lastTot) + 4).toFixed(1)}" font-size="11" fill="#adbac7" font-weight="600">₹${Math.round(lastTot).toLocaleString('en-IN')}</text>
  </svg>`;
    }

