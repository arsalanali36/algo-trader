/* ==========================================================================
   app-14-bs-shadow.js — Real vs Black-Scholes premium overlay for the Stats tab.

   The daily _ops/bs_shadow.py reprices every REAL completed trade under a
   Black-Scholes model at its OWN entry/exit minute → data/bs_shadow/<date>.json
   → /api/bs-shadow (returns `bykey`: a per-trade BS lookup).

   We attach BS to `window.currentCalendarTrades` — the calendar's OWN
   already-filtered trade list — so EVERY calendar filter (source / mode /
   broker / strategy / saved view / date range) applies to the divergence for
   free, and grouping reuses `_calGroupKey` (Monthly/Weekly/Day/Strategy).
   Display-only; real numbers reproduce the dashboard exactly.
   ========================================================================== */
(function () {
  window.calPremiumMode = window.calPremiumMode || 'real';   // real | bs | compare  (calendar overlay)
  window._bsByKey = window._bsByKey || {};                   // "date|sym|etime|xtime|side" -> {bs_net,bs_gross}
  window._bsDivPeriod = window._bsDivPeriod || 'day';        // day | weekly | monthly | strategy
  window._bsDivBasis = window._bsDivBasis || 'net';          // net | gross

  const inr = (v) => (v >= 0 ? '+' : '') + '₹' + Math.round(v).toLocaleString('en-IN');
  const bsKey = (t) => [t.entry_date || '', t.sym || '', t.entry_time || '',
                        t.exit_time || '', t.entry || ''].join('|');

  // per-trade real (basis) + bs (joined) + whether BS was available
  function _tradeRealBs(t, basis) {
    const real = basis === 'gross' ? (t._gross || 0) : (t._net || 0);
    const hit = window._bsByKey[bsKey(t)];
    const bs = hit ? (basis === 'gross' ? hit.bs_gross : hit.bs_net) : null;
    return { real, bs: bs == null ? 0 : bs, ok: hit != null };
  }

  // ---- calendar cell overlay (Real | BS | Compare) ----
  window.calSetPremium = function (mode, el) {
    window.calPremiumMode = mode;
    const seg = document.getElementById('cal-prem');
    if (seg) seg.querySelectorAll('span[data-v]').forEach(s => {
      const on = s.dataset.v === mode;
      s.classList.toggle('on', on);
      s.style.background = on ? '#6f42c1' : '';
      s.style.color = on ? '#fff' : '#8b949e';
    });
    // index.html has a standalone #bs-div-card (show in bs/compare); stats2 has it as a
    // tab pane (#tbl-bsdiv, no #bs-div-card) so this is a no-op there.
    const card = document.getElementById('bs-div-card');
    if (card) card.style.display = (mode === 'real') ? 'none' : '';
    bsDecorateCalendar();
    bsRenderDivergence();
  };

  window.bsDecorateCalendar = function () {
    document.querySelectorAll('.cal-day-cell .bs-ov').forEach(n => n.remove());
    document.querySelectorAll('.cal-day-cell .cal-day-pnl').forEach(n => { n.style.opacity = ''; });
    const mode = window.calPremiumMode;
    if (mode === 'real') return;
    // sum BS per date over the calendar's own (already-filtered) trades
    const byDate = {};
    (window.currentCalendarTrades || []).forEach(t => {
      const d = t.exit_date || t.entry_date; if (!d) return;
      const rb = _tradeRealBs(t, 'gross');                 // cells are GROSS
      const a = byDate[d] || (byDate[d] = { bs: 0, real: 0, n: 0 });
      a.bs += rb.bs; a.real += rb.real; a.n += 1;
    });
    document.querySelectorAll('.cal-day-cell[data-date]').forEach(cell => {
      const v = byDate[cell.dataset.date];
      if (!v || !v.n) return;
      const realPnl = cell.querySelector('.cal-day-pnl');
      if (mode === 'bs' && realPnl) realPnl.style.opacity = '0.35';
      const bsDiv = document.createElement('div');
      bsDiv.className = 'bs-ov cal-day-pnl';
      bsDiv.style.cssText = 'color:#a371f7;font-size:11px;line-height:1.2;';
      bsDiv.textContent = 'BS ' + inr(v.bs);
      cell.appendChild(bsDiv);
      if (mode === 'compare') {
        const d = v.bs - v.real;
        const dDiv = document.createElement('div');
        dDiv.className = 'bs-ov';
        dDiv.style.cssText = 'font-size:10px;line-height:1.2;color:' + (d >= 0 ? '#d29922' : '#388bfd') + ';';
        dDiv.textContent = 'Δ ' + inr(d);
        cell.appendChild(dDiv);
      }
    });
  };

  // ---- fetch BS lookup for the current calendar month, then re-render ----
  window.bsRefresh = async function () {
    try {
      const y = (typeof calYear !== 'undefined') ? calYear : new Date().getFullYear();
      const m = ((typeof calMonth !== 'undefined') ? calMonth : new Date().getMonth()) + 1;
      const r = await fetch(`/api/bs-shadow?year=${y}&month=${m}`);
      const j = await r.json();
      window._bsByKey = j.bykey || {};
      window._bsMissing = j.missing || [];
    } catch (e) { console.warn('bs-shadow fetch fail', e); window._bsByKey = {}; }
    bsDecorateCalendar();
    bsRenderDivergence();
  };

  // ---- divergence table (its own tab; period toggle; all other filters inherited) ----
  window.bsDivPeriod = function (el) {
    window._bsDivPeriod = el.dataset.v;
    el.parentElement.querySelectorAll('span[data-v]').forEach(s => {
      const on = s === el; s.style.background = on ? '#6f42c1' : ''; s.style.color = on ? '#fff' : '#8b949e';
    });
    bsRenderDivergence();
  };
  window.bsDivSeg = window.bsDivPeriod;   // back-compat (index.html panel)
  window.bsDivBasis = function (el) {
    window._bsDivBasis = el.dataset.b;
    el.parentElement.querySelectorAll('span[data-b]').forEach(s => {
      const on = s === el; s.style.background = on ? '#1f6feb' : ''; s.style.color = on ? '#fff' : '#8b949e';
    });
    bsRenderDivergence(); bsDecorateCalendar();
  };

  window.bsRenderDivergence = function () {
    const tb = document.getElementById('bs-div-tbody');
    if (!tb) return;
    const tf = document.getElementById('bs-div-tfoot');
    const cov = document.getElementById('bs-div-cov');
    const basis = window._bsDivBasis;
    let period = window._bsDivPeriod || 'day';
    if (period === 'date') period = 'day';
    const groupKey = (typeof _calGroupKey === 'function')
      ? (t) => _calGroupKey(t, period)
      : (t) => (period === 'strategy' ? (t.strategy || 'unknown') : (t.exit_date || t.entry_date || '—'));

    const groups = {};
    (window.currentCalendarTrades || []).forEach(t => {
      const k = groupKey(t);
      const g = groups[k] || (groups[k] = { real: 0, bs: 0, n: 0, ok: 0 });
      const rb = _tradeRealBs(t, basis);
      g.real += rb.real; g.bs += rb.bs; g.n += 1; if (rb.ok) g.ok += 1;
    });

    let keys = Object.keys(groups);
    if (period === 'strategy') keys.sort((a, b) => Math.abs(groups[b].bs - groups[b].real) - Math.abs(groups[a].bs - groups[a].real));
    else keys.sort((a, b) => b.localeCompare(a));

    const label = (k) => period === 'strategy' ? (window.regLabel ? regLabel(k) : k)
      : (period === 'weekly' || period === 'monthly') && typeof _calPeriodLabel === 'function' ? _calPeriodLabel(k, period)
      : k;

    let tReal = 0, tBs = 0, tN = 0, tOk = 0;
    keys.forEach(k => { const g = groups[k]; tReal += g.real; tBs += g.bs; tN += g.n; tOk += g.ok; });
    const maxDiv = Math.max(1, ...keys.map(k => Math.abs(groups[k].bs - groups[k].real)));

    tb.innerHTML = keys.map(k => {
      const g = groups[k]; const d = g.bs - g.real;
      const col = d >= 0 ? '#d29922' : '#388bfd';
      const w = Math.round(Math.abs(d) / maxDiv * 100);
      const rc = g.real >= 0 ? '#3fb950' : '#f85149';
      return `<tr style="border-top:1px solid #21262d;text-align:right;">
        <td style="text-align:left;padding:4px 8px;">${label(k)}</td>
        <td style="padding:4px 8px;color:#8b949e;">${g.n}${g.ok < g.n ? '<span title="kuch legs non-NIFTY — BS nahi" style="color:#6e7681;">*</span>' : ''}</td>
        <td style="padding:4px 8px;color:${rc};">${inr(g.real)}</td>
        <td style="padding:4px 8px;color:#a371f7;">${inr(g.bs)}</td>
        <td style="padding:4px 8px;color:${col};">${inr(d)}</td>
        <td style="padding:4px 8px;"><div style="height:8px;background:${col};width:${w}%;margin-left:auto;border-radius:2px;"></div></td>
      </tr>`;
    }).join('') || `<tr><td colspan="6" style="text-align:center;color:#6e7681;padding:12px;">Is filter/month me koi trade nahi</td></tr>`;

    if (tf) {
      const dT = tBs - tReal;
      tf.innerHTML = keys.length ? `<tr style="border-top:2px solid #30363d;text-align:right;font-weight:700;">
        <td style="text-align:left;padding:6px 8px;">TOTAL</td>
        <td style="padding:6px 8px;">${tN}</td>
        <td style="padding:6px 8px;color:${tReal >= 0 ? '#3fb950' : '#f85149'};">${inr(tReal)}</td>
        <td style="padding:6px 8px;color:#a371f7;">${inr(tBs)}</td>
        <td style="padding:6px 8px;color:${dT >= 0 ? '#d29922' : '#388bfd'};">${inr(dT)}</td>
        <td></td></tr>` : '';
    }
    if (cov) {
      const miss = (window._bsMissing || []).length;
      cov.innerHTML = (tOk < tN ? `<span style="color:#d29922">*</span> ${tN - tOk}/${tN} legs non-NIFTY (BS skip, Real me ginte). ` : '')
        + (miss ? `${miss} din abhi shadow nahi bana (roz 15:50 pe banega).` : '');
    }
  };
})();
