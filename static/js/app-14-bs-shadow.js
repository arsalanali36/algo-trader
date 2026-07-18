/* ==========================================================================
   app-14-bs-shadow.js — Real vs Black-Scholes premium overlay for the Stats tab.

   The daily _ops/bs_shadow.py reprices every REAL completed trade under a
   Black-Scholes model at its OWN entry/exit minute → data/bs_shadow/<date>.json
   → /api/bs-shadow. Here we surface it: a Real | BS | Compare toggle that
   overlays BS + divergence on the calendar, plus a dedicated divergence panel.
   Display-only; real numbers reproduce the dashboard exactly.
   ========================================================================== */
(function () {
  window.calPremiumMode = window.calPremiumMode || 'real';   // real | bs | compare
  window._bsShadowDays = window._bsShadowDays || {};         // {date: {real_*, bs_*, n, bs_n, by_strategy}}
  window._bsDivMode = window._bsDivMode || 'date';           // date | strategy
  window._bsDivBasis = window._bsDivBasis || 'net';          // net | gross

  const inr = (v) => (v >= 0 ? '+' : '') + '₹' + Math.round(v).toLocaleString('en-IN');

  // active strategy filter — mirrors the calendar (saved view > single-strat > all)
  function bsActiveStrats() {
    if (window.calActiveView && Array.isArray(window.calActiveView.strategies) &&
        window.calActiveView.strategies.length) return window.calActiveView.strategies.slice();
    const sel = document.getElementById('cal-strat');
    if (sel && sel.value) return [sel.value];
    return null;   // null = all strategies
  }

  // real/bs for one date under the active strategy filter + basis
  function bsDayVals(dateStr, basis) {
    const day = window._bsShadowDays[dateStr];
    if (!day) return null;
    const rk = basis === 'gross' ? 'real_gross' : 'real_net';
    const bk = basis === 'gross' ? 'bs_gross' : 'bs_net';
    const strats = bsActiveStrats();
    if (!strats) return { real: day[rk] || 0, bs: day[bk] || 0, n: day.n || 0, bs_n: day.bs_n || 0 };
    let real = 0, bs = 0, n = 0, bsn = 0;
    strats.forEach(sid => {
      const v = (day.by_strategy || {})[sid];
      if (v) { real += v[rk] || 0; bs += v[bk] || 0; n += v.n || 0; bsn += v.bs_n || 0; }
    });
    return { real, bs, n, bs_n: bsn };
  }

  window.calSetPremium = function (mode, el) {
    window.calPremiumMode = mode;
    const seg = document.getElementById('cal-prem');
    if (seg) seg.querySelectorAll('span[data-v]').forEach(s => {
      const on = s.dataset.v === mode;
      s.classList.toggle('on', on);
      s.style.background = on ? '#6f42c1' : '';
      s.style.color = on ? '#fff' : '#8b949e';
    });
    const card = document.getElementById('bs-div-card');
    if (card) card.style.display = (mode === 'real') ? 'none' : '';
    if (mode === 'real') { bsDecorateCalendar(); return; }   // strip overlay, keep real cells
    bsRefresh();
  };

  window.bsDivSeg = function (el) {
    window._bsDivMode = el.dataset.v;
    el.parentElement.querySelectorAll('span[data-v]').forEach(s => {
      const on = s === el;
      s.style.background = on ? '#6f42c1' : ''; s.style.color = on ? '#fff' : '#8b949e';
    });
    bsRenderDivergence();
  };
  window.bsDivBasis = function (el) {
    window._bsDivBasis = el.dataset.b;
    el.parentElement.querySelectorAll('span[data-b]').forEach(s => {
      const on = s === el;
      s.style.background = on ? '#1f6feb' : ''; s.style.color = on ? '#fff' : '#8b949e';
    });
    bsRenderDivergence(); bsDecorateCalendar();
  };

  // fetch the current calendar month's shadow, then overlay + panel
  window.bsRefresh = async function () {
    if (window.calPremiumMode === 'real') return;
    try {
      const y = (typeof calYear !== 'undefined') ? calYear : new Date().getFullYear();
      const m = ((typeof calMonth !== 'undefined') ? calMonth : new Date().getMonth()) + 1;
      const r = await fetch(`/api/bs-shadow?year=${y}&month=${m}`);
      const j = await r.json();
      window._bsShadowDays = j.days || {};
      window._bsMissing = j.missing || [];
    } catch (e) { console.warn('bs-shadow fetch fail', e); window._bsShadowDays = {}; }
    bsDecorateCalendar();
    bsRenderDivergence();
  };

  // overlay BS + Δ onto each calendar day cell
  window.bsDecorateCalendar = function () {
    document.querySelectorAll('.cal-day-cell .bs-ov').forEach(n => n.remove());
    document.querySelectorAll('.cal-day-cell .cal-day-pnl').forEach(n => { n.style.opacity = ''; });
    const mode = window.calPremiumMode;
    if (mode === 'real') return;
    document.querySelectorAll('.cal-day-cell[data-date]').forEach(cell => {
      const v = bsDayVals(cell.dataset.date, 'gross');   // calendar cells are GROSS
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

  // divergence panel (By Date / By Strategy)
  window.bsRenderDivergence = function () {
    const tb = document.getElementById('bs-div-tbody');
    const tf = document.getElementById('bs-div-tfoot');
    const cov = document.getElementById('bs-div-cov');
    if (!tb) return;
    const basis = window._bsDivBasis;
    const strats = bsActiveStrats();
    const dates = Object.keys(window._bsShadowDays).sort();
    let rows = [], tReal = 0, tBs = 0, tN = 0, tBsN = 0;

    if (window._bsDivMode === 'strategy') {
      const agg = {};
      dates.forEach(d => {
        const bystrat = (window._bsShadowDays[d] || {}).by_strategy || {};
        Object.keys(bystrat).forEach(sid => {
          if (strats && strats.indexOf(sid) < 0) return;
          const v = bystrat[sid];
          const a = agg[sid] || (agg[sid] = { real: 0, bs: 0, n: 0, bs_n: 0 });
          a.real += v[basis === 'gross' ? 'real_gross' : 'real_net'] || 0;
          a.bs += v[basis === 'gross' ? 'bs_gross' : 'bs_net'] || 0;
          a.n += v.n || 0; a.bs_n += v.bs_n || 0;
        });
      });
      rows = Object.keys(agg).map(sid => ({ label: (window.regLabel ? regLabel(sid) : sid), ...agg[sid] }))
        .sort((a, b) => Math.abs(b.bs - b.real) - Math.abs(a.bs - a.real));
    } else {
      rows = dates.map(d => { const v = bsDayVals(d, basis); return v && v.n ? { label: d, ...v } : null; })
        .filter(Boolean);
    }
    rows.forEach(r => { tReal += r.real; tBs += r.bs; tN += r.n; tBsN += r.bs_n; });
    const maxDiv = Math.max(1, ...rows.map(r => Math.abs(r.bs - r.real)));

    tb.innerHTML = rows.map(r => {
      const d = r.bs - r.real;
      const col = d >= 0 ? '#d29922' : '#388bfd';
      const w = Math.round(Math.abs(d) / maxDiv * 100);
      const rc = r.real >= 0 ? '#3fb950' : '#f85149';
      return `<tr style="border-top:1px solid #21262d;text-align:right;">
        <td style="text-align:left;padding:4px 8px;">${r.label}</td>
        <td style="padding:4px 8px;color:#8b949e;">${r.n}${r.bs_n < r.n ? '<span title="kuch legs non-NIFTY — BS nahi" style="color:#6e7681;">*</span>' : ''}</td>
        <td style="padding:4px 8px;color:${rc};">${inr(r.real)}</td>
        <td style="padding:4px 8px;color:#a371f7;">${inr(r.bs)}</td>
        <td style="padding:4px 8px;color:${col};">${inr(d)}</td>
        <td style="padding:4px 8px;"><div style="height:8px;background:${col};width:${w}%;margin-left:auto;border-radius:2px;"></div></td>
      </tr>`;
    }).join('');

    const dT = tBs - tReal;
    tf.innerHTML = `<tr style="border-top:2px solid #30363d;text-align:right;font-weight:700;">
      <td style="text-align:left;padding:6px 8px;">TOTAL</td>
      <td style="padding:6px 8px;">${tN}</td>
      <td style="padding:6px 8px;color:${tReal >= 0 ? '#3fb950' : '#f85149'};">${inr(tReal)}</td>
      <td style="padding:6px 8px;color:#a371f7;">${inr(tBs)}</td>
      <td style="padding:6px 8px;color:${dT >= 0 ? '#d29922' : '#388bfd'};">${inr(dT)}</td>
      <td></td></tr>`;

    if (cov) {
      const miss = (window._bsMissing || []).length;
      cov.innerHTML = (tBsN < tN ? `<span style="color:#d29922">*</span> ${tN - tBsN}/${tN} legs non-NIFTY (BS skip, Real me ginte). ` : '')
        + (miss ? `${miss} din abhi shadow nahi bana (bs_shadow.py chalega).` : '');
    }
  };
})();
