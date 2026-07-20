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
    // restore each cell's main Real number to its ALL-legs value (undo any prior
    // index-only swap) + reset styling — so toggling back to Real mode is exact.
    document.querySelectorAll('.cal-day-cell .cal-day-pnl:not(.bs-ov)').forEach(n => {
      n.style.opacity = ''; n.style.color = '';
      if (n.dataset.allReal != null) n.textContent = n.dataset.allReal;
    });
    const mode = window.calPremiumMode;
    if (mode === 'real') return;
    // sum index-only (gross) Real + BS per date over the calendar's own (already-
    // filtered) trades — BS can't price stocks, so this is the BS-comparable set.
    const byDate = {};
    (window.currentCalendarTrades || []).forEach(t => {
      const rb = _tradeRealBs(t, 'gross');                 // cells are GROSS
      if (!rb.ok) return;                                  // NIFTY/BNF only — BS can't price stocks
      const d = t.exit_date || t.entry_date; if (!d) return;
      const a = byDate[d] || (byDate[d] = { bs: 0, real: 0, n: 0 });
      a.bs += rb.bs; a.real += rb.real; a.n += 1;
    });
    document.querySelectorAll('.cal-day-cell[data-date]').forEach(cell => {
      const v = byDate[cell.dataset.date];
      if (!v || !v.n) return;
      const realPnl = cell.querySelector('.cal-day-pnl:not(.bs-ov)');
      // A (fair tile) — in BS/Compare the big Real number becomes INDEX-ONLY, the
      // exact same legs BS covers, so Real vs BS is apples-to-apples (all-legs
      // Real incl. stocks stays available in Real mode + the per-day drilldown).
      if (realPnl) {
        if (realPnl.dataset.allReal == null) realPnl.dataset.allReal = realPnl.textContent;
        realPnl.textContent = inr(v.real) + ' ·idx';
        realPnl.style.opacity = '';
        realPnl.style.color = v.real >= 0 ? '#3fb950' : '#f85149';
      }
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
    // B — when a single calendar day is clicked, scope the table to that day and
    // group per-STRATEGY (jo us din chali + unka Real vs BS). Warna month/range +
    // chosen period toggle.
    const dateSel = window.calSelectedDateFilter || null;
    let period = window._bsDivPeriod || 'day';
    if (period === 'date') period = 'day';
    if (dateSel) period = 'strategy';
    const groupKey = (typeof _calGroupKey === 'function')
      ? (t) => _calGroupKey(t, period)
      : (t) => (period === 'strategy' ? (t.strategy || 'unknown') : (t.exit_date || t.entry_date || '—'));

    // Per group: realAll = every leg's Real (stocks incl.); realIdx/bs = ONLY the
    // BS-priceable index legs (fair, same-legs Δ). bsN=0 → stock-only → "BS n/a".
    // Per-STRATEGY view shows every strategy incl. stock-only ones (Real real,
    // BS "n/a") — that's the "kaun si strategy chali + divergence" drilldown.
    // Time buckets (day/weekly/monthly) stay index-only (stocks out) so each row's
    // Real vs BS is same-legs fair, exactly like before (no regression).
    const strat = (period === 'strategy');
    const groups = {};
    let exN = 0, incN = 0;                          // excluded (stock, no BS) vs included legs
    const diffs = [];                               // per-leg Δ = BS − Real (bias/noise signal, index legs)
    (window.currentCalendarTrades || []).forEach(t => {
      if (dateSel && (t.exit_date || t.entry_date) !== dateSel) return;   // date-scope
      const rb = _tradeRealBs(t, basis);
      if (!rb.ok && !strat) { exN += 1; return; }   // time buckets: BS-unpriceable stocks stay out (fair)
      const k = groupKey(t);
      const g = groups[k] || (groups[k] = { realAll: 0, realIdx: 0, bs: 0, n: 0, bsN: 0, idxN: 0 });
      g.realAll += rb.real; g.n += 1;
      if (/^(NIFTY|BANKNIFTY)/i.test(t.sym || '')) g.idxN += 1;   // index leg (stock vs "no spot" label)
      if (rb.ok) { incN += 1; diffs.push(rb.bs - rb.real); g.realIdx += rb.real; g.bs += rb.bs; g.bsN += 1; }
      else exN += 1;
    });

    let keys = Object.keys(groups);
    if (period === 'strategy') keys.sort((a, b) => Math.abs(groups[b].bs - groups[b].realIdx) - Math.abs(groups[a].bs - groups[a].realIdx));
    else keys.sort((a, b) => b.localeCompare(a));

    const label = (k) => period === 'strategy' ? (window.regLabel ? regLabel(k) : k)
      : (period === 'weekly' || period === 'monthly') && typeof _calPeriodLabel === 'function' ? _calPeriodLabel(k, period)
      : k;

    let tRealAll = 0, tRealIdx = 0, tBs = 0, tN = 0, tBsN = 0;
    keys.forEach(k => { const g = groups[k]; tRealAll += g.realAll; tRealIdx += g.realIdx; tBs += g.bs; tN += g.n; tBsN += g.bsN; });
    const maxDiv = Math.max(1, ...keys.map(k => Math.abs(groups[k].bs - groups[k].realIdx)));

    tb.innerHTML = keys.map(k => {
      const g = groups[k];
      const hasBs = g.bsN > 0;
      const d = g.bs - g.realIdx;
      const col = d >= 0 ? '#d29922' : '#388bfd';
      const w = hasBs ? Math.round(Math.abs(d) / maxDiv * 100) : 0;
      const rc = g.realAll >= 0 ? '#3fb950' : '#f85149';
      const naLbl = g.idxN > 0 ? 'n/a' : 'stock';   // index-but-no-spot vs actual stock (BS can't price)
      const naTip = g.idxN > 0 ? 'index leg par BS reprice ke liye entry-din ka spot nahi mila' : 'BS sirf NIFTY/BankNifty index options pe';
      const bsCell = hasBs ? `<span style="color:#a371f7;">${inr(g.bs)}</span>`
        : `<span style="color:#6e7681;" title="${naTip}">— <span style="font-size:9px;">${naLbl}</span></span>`;
      const dCell = hasBs ? `<span style="color:${col};">${inr(d)}</span>` : `<span style="color:#6e7681;">—</span>`;
      const bar = hasBs ? `<div style="height:8px;background:${col};width:${w}%;margin-left:auto;border-radius:2px;"></div>` : '';
      return `<tr style="border-top:1px solid #21262d;text-align:right;">
        <td style="text-align:left;padding:4px 8px;">${label(k)}</td>
        <td style="padding:4px 8px;color:#8b949e;">${g.n}</td>
        <td style="padding:4px 8px;color:${rc};">${inr(g.realAll)}</td>
        <td style="padding:4px 8px;">${bsCell}</td>
        <td style="padding:4px 8px;">${dCell}</td>
        <td style="padding:4px 8px;">${bar}</td>
      </tr>`;
    }).join('') || `<tr><td colspan="6" style="text-align:center;color:#6e7681;padding:12px;">${dateSel ? 'Is din koi trade nahi' : 'Is filter/month me koi trade nahi'}</td></tr>`;

    if (tf) {
      const dT = tBs - tRealIdx;
      // two footer rows: index-only (BS-fair, same legs) + all-legs Real (stocks incl.)
      tf.innerHTML = keys.length ? `<tr style="border-top:2px solid #30363d;text-align:right;font-weight:700;">
        <td style="text-align:left;padding:6px 8px;">Index-only (BS-fair)</td>
        <td style="padding:6px 8px;color:#58a6ff;">${tBsN}</td>
        <td style="padding:6px 8px;color:${tRealIdx >= 0 ? '#3fb950' : '#f85149'};">${inr(tRealIdx)}</td>
        <td style="padding:6px 8px;color:#a371f7;">${inr(tBs)}</td>
        <td style="padding:6px 8px;color:${dT >= 0 ? '#d29922' : '#388bfd'};">${inr(dT)}</td>
        <td></td></tr>`
        + (strat && exN ? `<tr style="text-align:right;color:#8b949e;">
        <td style="text-align:left;padding:4px 8px;">All legs Real (stocks incl.)</td>
        <td style="padding:4px 8px;">${tN}</td>
        <td style="padding:4px 8px;color:${tRealAll >= 0 ? '#3fb950' : '#f85149'};">${inr(tRealAll)}</td>
        <td style="padding:4px 8px;">—</td><td style="padding:4px 8px;">—</td><td></td></tr>` : '') : '';
    }
    if (cov) {
      const miss = (window._bsMissing || []).length;
      // per-leg Δ signal: is the BS-vs-Real gap a real bias, or just noise?
      const n = diffs.length;
      const dm = n ? diffs.reduce((a, b) => a + b, 0) / n : 0;
      const sd = n > 1 ? Math.sqrt(diffs.reduce((a, b) => a + (b - dm) * (b - dm), 0) / n) : 0;
      const tstat = (n && sd) ? dm / (sd / Math.sqrt(n)) : 0;
      const real = Math.abs(tstat) >= 2;
      const verdict = real ? 'real bias' : 'abhi noise';
      const vcol = real ? '#d29922' : '#3fb950';
      const scope = dateSel ? `<span style="color:#58a6ff;font-weight:600">📅 ${dateSel} · per-strategy</span> — ` : '';
      const sig = n ? `<span style="border:1px solid ${vcol};border-radius:5px;padding:2px 8px;color:${vcol};font-weight:600">`
        + `per-leg Δ ${inr(dm)} · t=${tstat.toFixed(1)} · ${verdict}</span> `
        + `<span style="color:#6e7681">(signal — roz ke number = noise; ~1000 legs pe pakka pata)</span><br>` : '';
      cov.innerHTML = scope + sig
        + `<b style="color:#a371f7">${incN} NIFTY/BankNifty legs</b> pe Real vs BS (fair, same legs). `
        + (exN ? `<span style="color:#8b949e">${exN} stock legs BS-model se bahar (BS sirf index options pe) — "BS n/a" rows.</span> ` : '')
        + (miss ? `${miss} din shadow pending.` : '');
    }
  };
})();
