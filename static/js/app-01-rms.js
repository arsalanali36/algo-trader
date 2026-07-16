// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // ── RISK TAB (global + per-strategy max-loss) ──
    let RISK_CFG = { global: {}, per_strategy: {} };
    async function renderRmsSummary() {
      const el = document.getElementById('rms-summary-content');
      let d = {};
      try {
        await checkStatus(); // refresh process PIDs & modes first
        const r = await fetch('/api/rms-summary');
        d = await r.json();
      } catch (e) {
        el.innerHTML = '<div style="color:#f85149;font-size:12px;padding:6px">Load failed: ' + e + '</div>'; return;
      }
      // 2026-07-14 (user request): per-strategy RMS table Per-Strategy Override table
      // me MERGE ho gayi (Run Controls / Status / Capital Used LIVE cells wahan patch
      // hote hain; Capital Cap / Open Pos / Unrealized P&L / Max-Loss Used columns
      // RETIRED). Ye section ab sirf broker balance + webhook trades/day + floors.
      window._lastRmsData = d;
      _rmsPatchOverrideCells(d);

      const html = _renderRmsExtras(d);
      el.innerHTML = html || '<div style="color:#8b949e;font-size:12px;padding:6px">Per-strategy detail ab ⚙ Per-Strategy Override table me hai (Run Controls / Status / Capital Used wahi dikhte hain).</div>';
    }

    // Live cells in the Per-Strategy Override table (.rms-run/.rms-gate/.rms-cap) —
    // PATCH only, table kabhi re-render nahi (unsaved input edits safe rehte hain).
    function _rmsPatchOverrideCells(d) {
      if (!d) return;
      const rows = d.strategies || [];
      const fmtRs = v => v === null || v === undefined ? '—' : Math.round(v).toLocaleString('en-IN');
      const gateBadge = s => {
        if (!s.blocked) return '<span style="color:#3fb950">✅ Active</span>';
        const c = s.block_hard ? '#f85149' : '#d29922';
        const lbl = s.block_hard ? '🛑 No further entries today' : '⏸ Capital full';
        return `<span style="color:${c}" title="${(s.block_reason || '').replace(/"/g, '&quot;')}">${lbl}</span>`;
      };
      // Per-strategy: live → broker used_margin (split equally among live strategies);
      // paper/stopped → paper estimate. Broker total split: distribute broker_used_margin
      // proportionally across live strategies by their paper-estimate weight.
      const liveStrats = rows.filter(s => s.run_mode === 'live');
      const totalPaperEst = liveStrats.reduce((a, s) => a + (s.capital_used || 0), 0);
      const brokerUsed = (d.totals || {}).broker_used_margin;
      const brokerTotal = (d.totals || {}).broker_total;

      rows.forEach(s => {
        const key = (window.CSS && CSS.escape) ? CSS.escape(s.strategy) : s.strategy;
        const runEl = document.querySelector(`.rms-run[data-rms="${key}"]`);
        const gateEl = document.querySelector(`.rms-gate[data-rms="${key}"]`);
        const capEl = document.querySelector(`.rms-cap[data-rms="${key}"]`);
        if (!runEl && !gateEl && !capEl) return;   // strategy not in the override table (e.g. _ui_config)
        // Capital to display: live → actual broker margin (proportional share), paper → estimate
        let capDisplay, capLabel;
        if (s.run_mode === 'live' && brokerUsed != null) {
          const share = totalPaperEst > 0 ? (s.capital_used / totalPaperEst) : (1 / liveStrats.length);
          capDisplay = Math.round(brokerUsed * share);
          capLabel = 'live';
        } else {
          capDisplay = s.capital_used;
          capLabel = s.run_mode === 'paper' ? 'paper' : null;
        }
        const capCapDisplay = (s.run_mode === 'live' && brokerTotal) ? brokerTotal : s.capital_cap;
        const capPct = (capCapDisplay && capDisplay) ? Math.min(100, Math.round(capDisplay / capCapDisplay * 100)) : null;

        let runControlsHtml = '—';
        const isWebhook = s.strategy.toLowerCase().startsWith('webhook');
        if (!isWebhook) {
          const pid = RUNNING_PIDS[s.strategy] || RUNNING_PIDS[s.strategy.toLowerCase()];
          const mode = RUNNING_PIDS[s.strategy + '_mode'] || RUNNING_PIDS[s.strategy.toLowerCase() + '_mode'] || 'paper';
          if (pid) {
            const modeBadge = mode === 'live'
              ? '<span style="color:#f85149;font-weight:600;">🔴 Live</span>'
              : '<span style="color:#58a6ff;font-weight:600;">🔵 Paper</span>';
            runControlsHtml = `<div style="display:flex;align-items:center;white-space:nowrap">${modeBadge} <span style="font-size:10px;color:#8b949e;margin-left:3px">(PID ${pid})</span>`
              + `<button class="btn btn-red" style="padding:3px 7px;font-size:11px;margin-left:8px;line-height:1;" onclick="riskStopBot('${s.strategy}')">Stop</button></div>`;
          } else {
            runControlsHtml = `<div style="display:flex;align-items:center;white-space:nowrap"><span style="color:#8b949e">⚫ Stopped</span>`
              + `<button class="btn btn-green" style="padding:3px 7px;font-size:11px;margin-left:8px;line-height:1;" onclick="riskStartBot('${s.strategy}','paper')">Paper</button>`
              + `<button class="btn btn-blue" style="padding:3px 7px;font-size:11px;margin-left:4px;line-height:1;" onclick="riskStartBot('${s.strategy}','live')">Live</button></div>`;
          }
        } else {
          const whCfg = (GLOBAL_CONFIG.webhooks && GLOBAL_CONFIG.webhooks[s.strategy]) || {};
          const isActive = whCfg.active === true;
          const mode = whCfg.mode || 'paper';

          if (isActive) {
            const modeBadge = mode === 'live'
              ? '<span style="color:#f85149;font-weight:600;">🔴 Live</span>'
              : '<span style="color:#58a6ff;font-weight:600;">🔵 Paper</span>';
            runControlsHtml = `<div style="display:flex;align-items:center;white-space:nowrap">${modeBadge}`
              + `<button class="btn btn-red" style="padding:3px 7px;font-size:11px;margin-left:8px;line-height:1;" onclick="riskToggleWebhook('${s.strategy}','stop')">Stop</button></div>`;
          } else {
            runControlsHtml = `<div style="display:flex;align-items:center;white-space:nowrap"><span style="color:#8b949e">⚫ Stopped</span>`
              + `<button class="btn btn-green" style="padding:3px 7px;font-size:11px;margin-left:8px;line-height:1;" onclick="riskToggleWebhook('${s.strategy}','paper')">Paper</button>`
              + `<button class="btn btn-blue" style="padding:3px 7px;font-size:11px;margin-left:4px;line-height:1;" onclick="riskToggleWebhook('${s.strategy}','live')">Live</button></div>`;
          }
        }

        const capLabelHtml = capLabel === 'live'
          ? ' <span style="color:#58a6ff;font-size:10px">live</span>'
          : capLabel === 'paper'
            ? ' <span style="color:#8b949e;font-size:10px">paper</span>'
            : '';
        if (runEl) { runEl.innerHTML = runControlsHtml; runEl.style.color = ''; }
        if (gateEl) { gateEl.innerHTML = gateBadge(s); gateEl.style.color = ''; }
        if (capEl) {
          capEl.innerHTML = `${fmtRs(capDisplay)}${capPct !== null ? ` <span style="color:${capPct >= 90 ? '#f85149' : '#8b949e'};font-size:10px">(${capPct}%)</span>` : ''}${capLabelHtml}`;
          capEl.style.color = '#e6edf3';
        }
      });
    }

    // RMS section ka bacha hua (non-per-strategy) content — broker balance strip +
    // webhook trades/day table. Built html return karta hai.
    function _renderRmsExtras(d) {
      const t = d.totals || {};
      let html = '';

      // ── Actual broker balance (Zerodha/Dhan real margin) ──
      if (t.broker_ok) {
        const bName = (t.broker_name || 'broker').toUpperCase();
        const fmt = v => v != null ? '₹' + v.toLocaleString('en-IN') : '—';
        const bAvail = fmt(t.broker_available);
        const bUsed = fmt(t.broker_used_margin);
        const bTotal = fmt(t.broker_total);
        const bCash = fmt(t.broker_cash);
        const estUsed = t.capital_used > 0 ? '₹' + Math.round(t.capital_used).toLocaleString('en-IN') : '—';
        html += `<div style="margin-top:12px;padding:10px 14px;background:#161b22;border:1px solid #30363d;border-radius:6px;font-size:12px;display:flex;flex-wrap:wrap;gap:16px;align-items:center">
      <span style="font-weight:700;color:#58a6ff">🏦 ${bName}</span>
      <span><span style="color:#8b949e">Collateral: </span><span style="font-weight:600">${fmt(t.broker_collateral)}</span></span>
      <span><span style="color:#8b949e">Used margin: </span><span style="color:${t.broker_used_margin > 0 ? '#f85149' : '#e6edf3'};font-weight:600">${bUsed}</span></span>
      <span><span style="color:#8b949e">Cash: </span><span style="font-weight:600;color:${t.broker_cash < 0 ? '#f85149' : '#e6edf3'}">${bCash}</span></span>
      <span><span style="color:#8b949e">Paper capital: </span><span style="font-weight:600">${bTotal}</span></span>
      <span><span style="color:#8b949e">Paper estimate: </span><span style="font-weight:600">${estUsed}</span></span>
    </div>`;
      }

      // ── Webhook max-trades-per-day status ──
      const wh = d.webhook || [];
      const whg = d.webhook_global || {};
      if (wh.length || (whg.global_max_trades)) {
        html += '<div style="margin-top:14px;font-size:12px;font-weight:700;color:#58a6ff">🔗 Webhook — Trades / Day</div>';
        html += '<table class="cfg-table" style="margin-top:6px"><tr><th>Strategy</th><th>Symbol</th><th>Trades Today</th><th>Max/Day</th><th>Status</th></tr>';
        wh.forEach(w => {
          const limitHit = w.maxed || w.blocked;
          let statusTxt, statusCol;
          if (w.maxed) { statusTxt = '🛑 Max trades reached — no further entries'; statusCol = '#f85149'; }
          else if (w.blocked) { statusTxt = (w.block_hard ? '🛑 ' : '⏸ ') + (w.block_reason || 'blocked'); statusCol = w.block_hard ? '#f85149' : '#d29922'; }
          else { statusTxt = '✅ Open'; statusCol = '#3fb950'; }
          html += `<tr><td title="${w.strategy}">${regLabel(w.strategy)}</td><td>${w.symbol || '—'}</td>
        <td style="color:${limitHit ? '#f85149' : '#e6edf3'};font-weight:600">${w.trades_today}</td>
        <td>${w.max_trades || '—'}</td>
        <td style="font-size:11px;color:${statusCol}" title="${(w.block_reason || '').replace(/"/g, '&quot;')}">${statusTxt}</td></tr>`;
        });
        if (whg.global_max_trades) {
          html += `<tr style="border-top:2px solid #30363d;font-weight:700"><td colspan="2">GLOBAL (all webhook strategies)</td>
        <td style="color:${whg.maxed ? '#f85149' : '#e6edf3'}">${whg.total_trades_today}</td>
        <td>${whg.global_max_trades}</td>
        <td style="color:${whg.maxed ? '#f85149' : '#3fb950'};font-size:11px">${whg.maxed ? '🛑 Global max reached' : '✅ Open'}</td></tr>`;
        }
        html += '</table>';
      }

      return html;
    }

    async function syncBrokerPositions(btn) {
      const result = document.getElementById('sync-broker-result');
      btn.disabled = true;
      btn.textContent = '⏳ Syncing…';
      result.style.color = '#8b949e';
      result.textContent = '';
      try {
        const r = await fetch('/api/sync-positions', { method: 'POST' });
        const d = await r.json();
        result.style.color = d.ghosts_cleared > 0 ? '#f85149' : '#3fb950';
        result.textContent = d.msg;
        if (d.ghosts_cleared > 0) loadPnl();  // refresh P&L table
      } catch (e) {
        result.style.color = '#f85149';
        result.textContent = 'Sync failed: ' + e;
      } finally {
        btn.disabled = false;
        btn.textContent = '🔄 Sync from Broker';
      }
    }

    async function reconcileManualTrades(btn) {
      const result = document.getElementById('reconcile-manual-result');
      btn.disabled = true;
      btn.textContent = '⏳ Checking Zerodha…';
      result.style.color = '#8b949e';
      result.textContent = '';
      try {
        const r = await fetch('/api/reconcile-manual-trades?broker=kite', { method: 'POST' });
        const d = await r.json();
        if (!d.ok) {
          result.style.color = '#f85149';
          result.textContent = 'Failed: ' + (d.msg || 'unknown error');
          return;
        }
        result.style.color = (d.manual_inserted > 0) ? '#d29922' : '#3fb950';
        result.textContent = d.msg;
        if (d.manual_inserted > 0) loadPnl();  // refresh P&L table
      } catch (e) {
        result.style.color = '#f85149';
        result.textContent = 'Reconcile failed: ' + e;
      } finally {
        btn.disabled = false;
        btn.textContent = '🧾 Reconcile vs Broker';
      }
    }

    async function runReconcile() {
      const el = document.getElementById('rms-reconcile-result');
      el.textContent = 'Checking…';
      try {
        const r = await fetch('/api/rms-reconcile');
        const d = await r.json();
        if (d.broker_available === null || d.broker_available === undefined) {
          el.style.color = '#8b949e';
          el.textContent = d.note || 'Could not reach broker funds API';
          return;
        }
        const ours = d.our_capital_in_use ?? 0;
        const avail = d.broker_available;
        el.style.color = '#3fb950';
        el.textContent = `Our capital-in-use: ${Math.round(ours).toLocaleString('en-IN')} · Broker available: ${Math.round(avail).toLocaleString('en-IN')} — ${d.note || ''}`;
      } catch (e) {
        el.style.color = '#f85149';
        el.textContent = 'Reconcile failed: ' + e;
      }
    }

    async function renderBrokerBalances() {
      const el = document.getElementById('broker-balances-content');
      if (!el) return;
      function fmt(n) { return n == null ? '—' : '₹' + Math.round(n).toLocaleString('en-IN'); }
      function card(label, color, b) {
        const ok = b && b.ok;
        return `<div style="border:1px solid #30363d;border-radius:8px;padding:12px">
      <div style="font-size:12px;font-weight:700;color:${color};margin-bottom:8px">${label}</div>
      ${ok ? `
        <div style="font-size:11px;color:#8b949e;display:flex;justify-content:space-between;margin-bottom:4px"><span>Cash</span><span style="color:#e6edf3">${fmt(b.cash != null ? b.cash : b.available)}</span></div>
        <div style="font-size:11px;color:#8b949e;display:flex;justify-content:space-between;margin-bottom:4px"><span>Collateral</span><span style="color:#e6edf3">${fmt(b.collateral)}</span></div>
        <div style="font-size:11px;color:#8b949e;display:flex;justify-content:space-between;margin-bottom:4px"><span>Available Margin</span><span style="color:#e6edf3">${fmt(b.available)}</span></div>
        <div style="font-size:12px;color:#8b949e;display:flex;justify-content:space-between;padding-top:6px;border-top:1px solid #21262d"><span>Total Margin</span><span style="color:#3fb950;font-weight:700">${fmt(b.total_margin)}</span></div>
      ` : `<div style="font-size:11px;color:#f85149">⚠️ balance unavailable${b && b.error ? ' — ' + b.error.slice(0, 60) : ' (token/login check karo)'}</div>`}
    </div>`;
      }
      try {
        const r = await fetch('/api/broker-balances');
        const d = await r.json();
        el.innerHTML = card('DHAN', '#1f6feb', d.dhan) + card('ZERODHA', '#a371f7', d.kite);
      } catch (e) {
        el.innerHTML = '<div style="color:#f85149;font-size:11px">balance fetch failed</div>';
      }
    }

    function _rlFmtAge(ts) {
      const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
      if (s < 60) return s + 's ago';
      return Math.round(s / 60) + 'm ago';
    }

    async function renderRateLimitEvents() {
      const top = document.getElementById('rl-top-offenders');
      const body = document.getElementById('rl-events-body');
      if (!top || !body) return;
      try {
        const r = await fetch('/api/rate-limit-events');
        const d = await r.json();
        if (!d.top_offenders || !d.top_offenders.length) {
          top.innerHTML = '<span style="font-size:11px;color:#3fb950">✅ Last 15 min me koi throttle/429 nahi — sab clean</span>';
        } else {
          top.innerHTML = d.top_offenders.map(([ctx, n]) =>
            `<span style="font-size:11px;font-weight:600;padding:3px 9px;border-radius:10px;background:#d2992222;border:1px solid #d29922;color:#d29922">${ctx} × ${n}</span>`
          ).join('');
        }
        if (!d.events || !d.events.length) {
          body.innerHTML = `<tr><td colspan="5" style="padding:10px;color:#8b949e;font-size:11px">Koi event nahi abhi tak.</td></tr>`;
        } else {
          body.innerHTML = d.events.map(e => {
            const kindColor = e.kind === '429' ? '#f85149' : (e.kind === 'timeout' ? '#f85149' : '#d29922');
            const kindLabel = e.kind === '429' ? '🔴 429' : (e.kind === 'timeout' ? '⛔ timeout' : '🟡 throttle');
            return `<tr style="border-top:1px solid #21262d">
          <td style="padding:5px 8px;color:#8b949e">${_rlFmtAge(e.ts)}</td>
          <td style="padding:5px 8px;color:${kindColor};font-weight:600">${kindLabel}</td>
          <td style="padding:5px 8px;color:#8b949e">${e.priority || '—'}</td>
          <td style="padding:5px 8px;color:#e6edf3">${e.context}</td>
          <td style="padding:5px 8px;color:#8b949e">${e.wait != null ? e.wait + 's' : '—'}</td>
        </tr>`;
          }).join('');
        }
      } catch (e) {
        body.innerHTML = `<tr><td colspan="5" style="padding:10px;color:#f85149;font-size:11px">fetch failed: ${e}</td></tr>`;
      }
    }
    setInterval(() => { if (activeTab === 'log') renderRateLimitEvents(); }, 5000);
    setInterval(() => { if (activeTab === 'calendar') { calendarRender(true); } }, 60000);
    setInterval(() => { if (activeTab === 'orders') { loadPeakGraph(); } }, 60000);

    async function saveDefaultBroker() {
      const broker = document.getElementById('default-broker-select').value;
      try {
        const r = await fetch('/api/risk-config'); const cur = await r.json();
        const payload = { global: { ...cur.global, default_broker: broker }, per_strategy: cur.per_strategy || {} };
        const res = await fetch('/api/risk-config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const j = await res.json();
        RISK_CFG = payload;
        const msgEl = document.getElementById('risk-msg');
        if (msgEl) msgEl.textContent = `Live orders ab ${broker.toUpperCase()} pe jaayenge`;
      } catch (e) {
        alert('Default broker save failed: ' + e);
      }
    }

    // ── RMS tab v2 layout helper (2026-07-02) — sidebar section switch (config + monitoring tabs, all in one sidebar) ──
    function switchSettingsTab(sectionId, el) {
      document.querySelectorAll('#tab-risk .settings-section').forEach(s => s.classList.remove('active'));
      const sec = document.getElementById(sectionId);
      if (sec) sec.classList.add('active');
      document.querySelectorAll('#tab-risk .folder-tab').forEach(t => t.classList.remove('active'));
      if (el) el.classList.add('active');
    }

    async function renderRiskTab() {
      renderRmsSummary();
      renderBrokerBalances();
      try { const r = await fetch('/api/risk-config'); RISK_CFG = await r.json(); } catch (e) { }
      document.getElementById('default-broker-select').value = RISK_CFG.global.default_broker || 'dhan';
      document.getElementById('risk-global-pct').value = RISK_CFG.global.max_loss_pct ?? '';
      document.getElementById('risk-global-rs').value = RISK_CFG.global.max_loss_rs ?? '';
      document.getElementById('risk-global-capital').value = RISK_CFG.global.capital_rs ?? '';
      document.getElementById('risk-global-total-capital').value = RISK_CFG.global.total_capital_rs ?? '';
      document.getElementById('risk-global-margin').value = RISK_CFG.global.margin_multiplier ?? '';
      document.getElementById('risk-global-mode').value = RISK_CFG.global.capital_mode || 'reject';
      document.getElementById('risk-global-shadow').value = RISK_CFG.global.shadow_live ? 'true' : 'false';
      document.getElementById('risk-global-instloss').value = RISK_CFG.global.default_sl_rs ?? '';
      // --- Added by Antigravity AI: Load Default SL/Target & Custom Trailing Steps ---
      document.getElementById('risk-global-default-sl-type').value = RISK_CFG.global.default_sl_type || '';
      document.getElementById('risk-global-default-sl-val').value = RISK_CFG.global.default_sl_val ?? '';
      document.getElementById('risk-global-default-sl-candle').checked = RISK_CFG.global.default_sl_candle_close === true;
      document.getElementById('risk-global-default-tp-type').value = RISK_CFG.global.default_tp_type || '';
      document.getElementById('risk-global-default-tp-val').value = RISK_CFG.global.default_tp_val ?? '';
      document.getElementById('risk-global-default-tp-candle').checked = RISK_CFG.global.default_tp_candle_close === true;
      document.getElementById('risk-global-trailing-step-band1').value = RISK_CFG.global.trailing_step_band_1 ?? '';
      document.getElementById('risk-global-trailing-step-band2').value = RISK_CFG.global.trailing_step_band_2 ?? '';
      document.getElementById('risk-global-trailing-step-band3').value = RISK_CFG.global.trailing_step_band_3 ?? '';
      document.getElementById('risk-global-trailing-step-band4').value = RISK_CFG.global.trailing_step_band_4 ?? '';
      toggleGlobalTrailingInfo();
      // --- End Antigravity AI addition ---
      document.getElementById('risk-global-profit-target').value = RISK_CFG.global.profit_target_rs ?? '';
      document.getElementById('risk-global-hedge-strikes').value = RISK_CFG.global.hedge_offset_strikes ?? '';
      document.getElementById('risk-global-hedge-premium').value = RISK_CFG.global.hedge_max_premium_rs ?? '';
      document.getElementById('risk-global-liqfilter').value = RISK_CFG.global.liquidity_filter === false ? 'false' : 'true';
      document.getElementById('risk-global-hedge-mode').value = RISK_CFG.global.hedge_enabled === false ? 'false' : 'true';
      // Max-premium per-index entry cap (2026-07-07)
      document.getElementById('risk-maxprem-nifty').value = RISK_CFG.global.max_premium_nifty != null ? RISK_CFG.global.max_premium_nifty : '';
      document.getElementById('risk-maxprem-banknifty').value = RISK_CFG.global.max_premium_banknifty != null ? RISK_CFG.global.max_premium_banknifty : '';
      document.getElementById('risk-maxprem-stock').value = RISK_CFG.global.max_premium_stock != null ? RISK_CFG.global.max_premium_stock : '';
      document.getElementById('risk-exit-squareoff').value = RISK_CFG.global.auto_squareoff_at || '15:15';
      document.getElementById('risk-exit-noentry').value = RISK_CFG.global.no_entry_after || '15:15';
      document.getElementById('risk-per-instrument-enabled').value = RISK_CFG.global.per_instrument_lock_enabled === true ? 'true' : 'false';
      document.getElementById('risk-per-instrument-arm').value = RISK_CFG.global.per_instrument_lock_arm_rs != null ? RISK_CFG.global.per_instrument_lock_arm_rs : '';
      document.getElementById('risk-per-instrument-gap').value = RISK_CFG.global.per_instrument_lock_gap_rs != null ? RISK_CFG.global.per_instrument_lock_gap_rs : '';
      document.getElementById('risk-per-instrument-confirm').value = RISK_CFG.global.per_instrument_lock_confirm_secs != null ? RISK_CFG.global.per_instrument_lock_confirm_secs : '';
      document.getElementById('risk-killfloor-enabled').value = RISK_CFG.global.kill_floor_enabled === true ? 'true' : 'false';
      document.getElementById('risk-killfloor-arm').value = RISK_CFG.global.kill_floor_arm_rs != null ? RISK_CFG.global.kill_floor_arm_rs : '';
      document.getElementById('risk-killfloor-gap').value = RISK_CFG.global.kill_floor_gap_rs != null ? RISK_CFG.global.kill_floor_gap_rs : '';
      document.getElementById('risk-killfloor-confirm').value = RISK_CFG.global.kill_floor_confirm_secs != null ? RISK_CFG.global.kill_floor_confirm_secs : '';
      // Default Target/SL exit profile (2026-07-04)
      document.getElementById('risk-tsl-enabled').value = RISK_CFG.global.default_tsl_enabled === true ? 'true' : 'false';
      document.getElementById('risk-tsl-target').value = RISK_CFG.global.default_tsl_target_per_lot != null ? RISK_CFG.global.default_tsl_target_per_lot : '';
      document.getElementById('risk-tsl-initsl').value = RISK_CFG.global.default_tsl_initial_sl_per_lot != null ? RISK_CFG.global.default_tsl_initial_sl_per_lot : '';
      document.getElementById('risk-tsl-fav').value = RISK_CFG.global.default_tsl_favour_step != null ? RISK_CFG.global.default_tsl_favour_step : '';
      document.getElementById('risk-tsl-move').value = RISK_CFG.global.default_tsl_sl_move != null ? RISK_CFG.global.default_tsl_sl_move : '';
      document.getElementById('risk-tsl-aggpct').value = RISK_CFG.global.default_tsl_aggressive_pct != null ? RISK_CFG.global.default_tsl_aggressive_pct : '';
      document.getElementById('risk-tsl-aggmult').value = RISK_CFG.global.default_tsl_aggressive_mult != null ? RISK_CFG.global.default_tsl_aggressive_mult : '';
      document.getElementById('risk-tsl-cushion').value = RISK_CFG.global.default_tsl_min_cushion != null ? RISK_CFG.global.default_tsl_min_cushion : '';
      // Per-Trade Default SL mode (2026-07-07 merge) — infer from old flags when
      // the new key isn't set yet (mirrors risk_gate.default_sl_profile()).
      let _dsMode = RISK_CFG.global.default_sl_mode;
      let _dsEn = RISK_CFG.global.default_sl_enabled;
      if (_dsMode !== 'legacy' && _dsMode !== 'dropdown' && _dsMode !== 'aggressive') {
        if (RISK_CFG.global.default_tsl_enabled) { _dsMode = 'aggressive'; _dsEn = true; }
        else if (RISK_CFG.global.default_sl_type) { _dsMode = 'dropdown'; _dsEn = true; }
        else if (RISK_CFG.global.default_sl_rs) { _dsMode = 'legacy'; _dsEn = true; }
        else { _dsMode = 'legacy'; _dsEn = (_dsEn === undefined ? false : _dsEn); }
      }
      document.getElementById('risk-defsl-mode').value = _dsMode;
      document.getElementById('risk-defsl-enabled').value = (_dsEn === false ? 'false' : 'true');
      document.getElementById('risk-legacy-sl').value = RISK_CFG.global.default_legacy_sl_rs != null ? RISK_CFG.global.default_legacy_sl_rs
        : (RISK_CFG.global.default_sl_rs != null ? RISK_CFG.global.default_sl_rs : '');
      document.getElementById('risk-legacy-tp').value = RISK_CFG.global.default_legacy_tp_rs != null ? RISK_CFG.global.default_legacy_tp_rs : '';
      setDefaultSlMode(_dsMode);
      renderTslPreview();
      killFloorStatusPoll();
      perInstrumentLockStatusPoll();

      // List every known strategy id from GLOBAL_CONFIG so future strategies show up automatically.
      let cfg = {};
      try { const r2 = await fetch('/api/config'); cfg = await r2.json(); } catch (e) { }
      // Pretty display name for a config-key strategy id (orb_v1 -> "ORB v1",
      // ARS_CHAIN_V1_PAPER -> "Ars Chain v1 (Paper)"). Raw id kept in title attr.
      function fmtStratName(id){
        const ACR={orb:'ORB',ema:'EMA',rsi:'RSI',ars:'Ars',bb:'BB',vwap:'VWAP',ui:'UI',ml:'ML'};
        return id.split(/[_\s]+/).map(w=>{
          const lw=w.toLowerCase();
          if(!w) return '';
          if(ACR[lw]) return ACR[lw];
          if(/^v\d+$/i.test(w)) return w.toLowerCase();
          if(lw==='paper') return '(Paper)';
          return w.charAt(0).toUpperCase()+w.slice(1).toLowerCase();
        }).filter(Boolean).join(' ');
      }
      const reserved = new Set(['_risk', 'webhooks']);
      // drop internal _-prefixed keys (e.g. _ui_config) — they aren't strategies
      const stratIds = Object.keys(cfg).filter(k => !reserved.has(k) && !k.startsWith('_'));
      // Webhook strategies (cfg.webhooks) bhi merged table me — Run Controls + overrides
      // (2026-07-14: RMS Live Summary table is table me merge hui)
      Object.keys(cfg.webhooks || {}).forEach(w => { if (!stratIds.includes(w)) stratIds.push(w); });

      // Reasons For Exit tab — which strategies are live right now, and whether
      // webhook-specific exit reasons apply (only webhook_executor produces
      // TARGET/TRAIL_SL/IDX_TRAIL/REVERSAL/TV_EXIT/GLOBAL_CAP/SQUAREOFF_315 — the
      // other strategies never will, so flag that clearly instead of showing a
      // static list that may not match what's actually running).
      const activeStratEl = document.getElementById('exit-reasons-active-strats');
      if (activeStratEl) {
        const activeIds = stratIds.filter(id => cfg[id] && cfg[id].active === true);
        const hasWebhook = activeIds.some(id => id.toLowerCase().includes('webhook'));
        activeStratEl.innerHTML = activeIds.length
          ? `<b style="color:#3fb950">🟢 Abhi live:</b> ${activeIds.join(', ')}`
          + (hasWebhook ? ' — <span style="color:#58a6ff">webhook_v1 active hai, "Webhook" group ke reasons applicable</span>'
            : ' — <span style="color:#8b949e">koi webhook strategy active nahi, "Webhook" group abhi applicable nahi</span>')
          : '<span style="color:#8b949e">⚪ Koi strategy abhi live nahi (sab paused/stopped)</span>';
      }

      const tbl = document.getElementById('risk-strategy-table');
      if (!stratIds.length) {
        tbl.innerHTML = '<div style="color:#8b949e;font-size:12px;padding:10px">Koi strategy configured nahi hai abhi.</div>';
        return;
      }
      // task 81 ⚙ values — per-strategy Default SL/Target VALUE overrides. Kept in
      // window._dslVals (not table inputs) and merged into the save payload, so the
      // table's own Save can't drop them. Rebuilt from RISK_CFG on every render.
      window._dslVals = {};
      stratIds.forEach(id => {
        const ov = RISK_CFG.per_strategy[id] || {};
        const dv = {};
        DSL_VAL_KEYS.forEach(k => { if (ov[k] !== undefined && ov[k] !== null && ov[k] !== '') dv[k] = ov[k]; });
        if (Object.keys(dv).length) window._dslVals[id] = dv;
      });
      // Excel-style collapsible "Advanced" column group (2026-07-14, user request) —
      // Day Cap / Per-Trade SL / Capital / Margin / Mode / Shadow-Live / Auto-Hedge sab
      // ek toggle ke peeche; everyday view = Run Controls + Status + Capital Used +
      // Default SL/Target. Run/Status/Capital Used cells LIVE hain — renderRmsSummary
      // unhe PATCH karta hai (table re-render nahi, warna unsaved edits udte).
      const _advC = localStorage.getItem('ps_adv_collapsed') !== '0';   // default: collapsed
      let html = `<div style="display:flex;justify-content:flex-end;margin-bottom:6px">
          <button id="psadv-toggle" onclick="togglePsAdv()" style="padding:4px 10px;background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:6px;font-size:11.5px;cursor:pointer"
            title="Day Cap / Per-Trade SL / Capital ₹ / Margin Mult. / Mode / Shadow-Live / Auto-Hedge columns">${_advC ? '⊞ Advanced columns dikhao' : '⊟ Advanced columns chhupao'}</button></div>`
        + `<table id="ps-ovr-table" class="cfg-table${_advC ? ' advc' : ''}"><tr><th>Strategy</th>`
        + '<th style="width:175px">Run Controls</th>'
        + '<th>Status</th>'
        + '<th>Capital Used</th>'
        + '<th colspan="2" style="text-align:center;color:#d29922" title="Per-Trade Default SL &amp; Target — is strategy ke NAYE trades pe Default-SL lage ya nahi, kaunsa mode, aur ⚙ se uski values. Blank = global fallback.">🎯 Default SL/Target</th>'
        + '<th class="psadv" colspan="4" style="text-align:center;color:#d29922">1️⃣ Strategy Day Cap</th>'
        + '<th class="psadv" style="color:#d29922">2️⃣ Per-Trade SL ₹</th>'
        + '<th class="psadv">Capital ₹</th><th class="psadv">Margin Mult.</th><th class="psadv">Mode</th><th class="psadv" style="color:#f85149">Shadow-Live</th>'
        + '<th class="psadv" colspan="2" style="text-align:center;color:#3fb950">🛡️ Auto-Hedge</th></tr>'
        + '<tr><th></th><th></th><th></th><th></th><th>Enabled</th><th>Mode</th>'
        + '<th class="psadv">Max Loss %</th><th class="psadv">Max Loss ₹</th><th class="psadv" style="color:#3fb950">Max Profit ₹</th><th class="psadv" style="color:#d29922">Max Trades</th><th class="psadv"></th>'
        + '<th class="psadv"></th><th class="psadv"></th><th class="psadv"></th><th class="psadv"></th>'
        + '<th class="psadv">Min Strikes</th><th class="psadv">Max Premium ₹</th></tr>';
      // group + order like the Logs sidebar (shared STRAT_GROUPS / MISSION_*)
      const _rlc = k => String(k).toLowerCase();
      const _rUsed = new Set();
      const _rOrdered = [];
      STRAT_GROUPS.forEach(g => {
        const inG = stratIds.filter(k => g.keys.includes(_rlc(k)))
                            .sort((a, b) => g.keys.indexOf(_rlc(a)) - g.keys.indexOf(_rlc(b)));
        if (inG.length) { _rOrdered.push({ h: g.title }); inG.forEach(k => { _rUsed.add(k); _rOrdered.push({ id: k }); }); }
      });
      const _rLeft = stratIds.filter(k => !_rUsed.has(k)).sort();
      if (_rLeft.length) { _rOrdered.push({ h: 'Other' }); _rLeft.forEach(k => _rOrdered.push({ id: k })); }
      _rOrdered.forEach(item => {
        if (item.h) {
          html += `<tr><td colspan="16" style="padding:9px 8px 4px;font-size:10px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:#6e7681;background:#0d1117;border-top:1px solid #21262d">${item.h}</td></tr>`;
          return;
        }
        const id = item.id;
        const ov = RISK_CFG.per_strategy[id] || {};
        const _num = MISSION_NUM[_rlc(id)];
        const _numBadge = _num ? `<span style="display:inline-block;min-width:16px;padding:0 4px;margin-right:6px;border-radius:4px;background:rgba(88,166,255,.18);color:#58a6ff;font-size:10px;font-weight:700;text-align:center">${_num}</span>` : '';
        const dispName = _numBadge + (MISSION_NAME[_rlc(id)] || fmtStratName(id));
        const curMode = ov.capital_mode || '';
        const curShadow = ov.shadow_live === true ? 'true' : (ov.shadow_live === false ? 'false' : '');
        html += `<tr><td title="${id}">${dispName}</td>
      <td class="rms-run" data-rms="${id}" style="white-space:nowrap;color:#8b949e">—</td>
      <td class="rms-gate" data-rms="${id}" style="font-size:11px;color:#8b949e">—</td>
      <td class="rms-cap" data-rms="${id}" style="white-space:nowrap;color:#8b949e">—</td>
      <td><select id="risk-dslen-${id}" style="background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #d29922;border-radius:4px">
            <option value="" ${ov.default_sl_enabled == null ? 'selected' : ''}>global (${(RISK_CFG.global || {}).default_sl_enabled ? 'ON' : 'OFF'})</option>
            <option value="false" ${ov.default_sl_enabled === false ? 'selected' : ''}>OFF</option>
            <option value="true" ${ov.default_sl_enabled === true ? 'selected' : ''}>ON</option>
          </select></td>
      <td><div style="display:flex;align-items:center;gap:4px;flex-wrap:nowrap"><select id="risk-dslmode-${id}" style="background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #d29922;border-radius:4px;max-width:150px">
            <option value="" ${!ov.default_sl_mode ? 'selected' : ''}>global (${({ legacy: 'Legacy', dropdown: 'Dropdown', aggressive: 'Aggressive' })[_globalDslMode()] || 'Dropdown'})</option>
            <option value="legacy" ${ov.default_sl_mode === 'legacy' ? 'selected' : ''}>Legacy — fixed ₹</option>
            <option value="dropdown" ${ov.default_sl_mode === 'dropdown' ? 'selected' : ''}>Dropdown — type+value</option>
            <option value="aggressive" ${ov.default_sl_mode === 'aggressive' ? 'selected' : ''}>Aggressive — Per-lot trail</option>
          </select><button id="risk-dslvals-btn-${id}" onclick="openDslValModal('${id}')"
            title="Is strategy ke apne SL/Target VALUES (Target/SL ₹, type, trail steps + Aggressive graph preview) — blank = global fallback"
            style="flex:0 0 auto;padding:4px 7px;background:${window._dslVals && window._dslVals[id] ? '#3a2b0a' : '#0d1117'};color:${window._dslVals && window._dslVals[id] ? '#e0a325' : '#8b949e'};border:1px solid #d29922;border-radius:4px;cursor:pointer">⚙</button></div></td>
      <td class="psadv"><input id="risk-pct-${id}" type="number" step="0.1" placeholder="global"
            value="${ov.max_loss_pct ?? ''}" style="width:100px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #30363d;border-radius:4px"/></td>
      <td class="psadv"><input id="risk-rs-${id}" type="number" step="1" placeholder="global"
            value="${ov.max_loss_rs ?? ''}" style="width:120px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #30363d;border-radius:4px"/></td>
      <td class="psadv"><input id="risk-profit-${id}" type="number" step="1" placeholder="global"
            value="${ov.profit_target_rs ?? ''}" style="width:120px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #3fb950;border-radius:4px"/></td>
      <td class="psadv"><input id="risk-maxtrades-${id}" type="number" step="1" min="1" placeholder="off"
            value="${ov.max_trades_per_day ?? ''}" style="width:80px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #d29922;border-radius:4px"/></td>
      <td class="psadv"><input id="risk-instloss-${id}" type="number" step="1" placeholder="global"
            value="${ov.default_sl_rs ?? ''}" style="width:120px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #d29922;border-radius:4px"/></td>
      <td class="psadv"><input id="risk-capital-${id}" type="number" step="1" placeholder="global"
            value="${ov.capital_rs ?? ''}" style="width:140px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #30363d;border-radius:4px"/></td>
      <td class="psadv"><input id="risk-margin-${id}" type="number" step="0.1" placeholder="global"
            value="${ov.margin_multiplier ?? ''}" style="width:100px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #30363d;border-radius:4px"/></td>
      <td class="psadv"><select id="risk-mode-${id}" style="background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #30363d;border-radius:4px">
            <option value="" ${curMode === '' ? 'selected' : ''}>global default</option>
            <option value="reject" ${curMode === 'reject' ? 'selected' : ''}>Reject</option>
            <option value="size_down" ${curMode === 'size_down' ? 'selected' : ''}>Size down</option>
          </select></td>
      <td class="psadv"><select id="risk-shadow-${id}" style="background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #f85149;border-radius:4px">
            <option value="" ${curShadow === '' ? 'selected' : ''}>global default</option>
            <option value="false" ${curShadow === 'false' ? 'selected' : ''}>OFF</option>
            <option value="true" ${curShadow === 'true' ? 'selected' : ''}>ON</option>
          </select></td>
      <td class="psadv"><input id="risk-hedgestrikes-${id}" type="number" step="1" placeholder="global"
            value="${ov.hedge_offset_strikes ?? ''}" style="width:110px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #3fb950;border-radius:4px"/></td>
      <td class="psadv"><input id="risk-hedgepremium-${id}" type="number" step="0.5" placeholder="global"
            value="${ov.hedge_max_premium_rs ?? ''}" style="width:120px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #3fb950;border-radius:4px"/></td>
      </tr>`;
      });
      html += '</table><button class="btn btn-amber" style="margin-top:10px" onclick="saveRiskConfig()">💾 Save</button>';
      tbl.innerHTML = html;
      // merged RMS live cells — fill immediately from the last fetch (renderRmsSummary
      // ka agla poll bhi inhe patch karta rahega)
      if (window._lastRmsData) _rmsPatchOverrideCells(window._lastRmsData);
    }

    async function loadPeakGraph() {
      const container = document.getElementById('peak-pnl-graph');
      if (!container) return;
      // Pass selected date so graph works for any day, not just today
      const selDate = (document.getElementById('ord-date') || {}).value || '';
      const _pk = window._peakStrat || '__all';       // selected strategy ('__all' = account)
      const _pm = window._peakPMode || 'cur';          // realised | unreal | cur (task 73 switch)
      const isAll = (_pk === '__all');
      let resp = {};
      try {
        const qs = new URLSearchParams();
        if (selDate) qs.set('date', selDate);
        qs.set('strat', _pk);
        const r = await fetch('/api/peak-pnl-history?' + qs.toString());
        resp = await r.json();
      } catch (e) {
        container.innerHTML = '<div style="color:#f85149;font-size:12px;padding:20px">Load failed</div>';
        return;
      }
      const pts = resp.data || [];
      const entries = resp.entries || [];      // [{time, cum_pnl, sym}]
      const lockPct = resp.lock_pct ? parseFloat(resp.lock_pct) : null;
      const lockRs = resp.lock_rs ? parseFloat(resp.lock_rs) : null;
      const profitTarget = resp.profit_target_rs ? parseFloat(resp.profit_target_rs) : null;

      if (!pts || pts.length < 2) {
        container.innerHTML = '<div style="color:#8b949e;font-size:12px;padding:20px">Data nahi hai abhi — market hours mein position open hone ke baad graph aayega.</div>';
        return;
      }

      const W = container.clientWidth || 700, H = 200;
      const PAD = { t: 14, r: 100, b: 28, l: 60 };
      const gW = W - PAD.l - PAD.r, gH = H - PAD.t - PAD.b;

      // MTM line — pick the plotted series: snapshot per-strategy (r/u/cur) if the
      // backend has it for this key+date, else reconstruct a realised step-curve
      // from the currently-loaded completed trades (past dates / no snapshot).
      const _ss = (resp.strat_series || {})[_pk];
      const _ssHas = _ss && Array.isArray(_ss.r) && _ss.r.length === pts.length
        && (_ss.r.some(v => v) || _ss.u.some(v => v));   // has real snapshot data (not a pre-v4 date)
      let rawTotals;
      if (isAll && _pm === 'cur') {
        rawTotals = pts.map(p => p[1]);                  // account current = existing MTM line (always correct)
      } else if (_ssHas) {
        rawTotals = pts.map((_, i) => _pm === 'real' ? _ss.r[i] : _pm === 'unreal' ? _ss.u[i] : (_ss.r[i] + _ss.u[i]));
      } else {
        // no per-strategy snapshot (past date) → reconstruct realised; unrealised unknown
        rawTotals = _pm === 'unreal' ? pts.map(() => 0) : _reconstructPeakSeries(pts);
      }
      const totals = rawTotals.map((v, i, arr) => {
        const w = 5, half = Math.floor(w / 2);
        const sl = arr.slice(Math.max(0, i - half), Math.min(arr.length, i + half + 1));
        return sl.reduce((a, b) => a + b, 0) / sl.length;
      });
      let _runMax = 0;
      const dailyPeakEver = pts.map(p => {
        const v3 = p.length > 3 ? p[3] : p[1];
        _runMax = Math.max(_runMax, v3);
        return _runMax;
      });
      // Floor/target/entry markers are account-level (kill-floor + all entries) —
      // only meaningful on the "__all" view; hidden when a single strategy is picked.
      const floors = !isAll ? pts.map(() => null) : pts.map((_, i) => {
        const pk = dailyPeakEver[i];
        if (pk <= 0) return null;
        if (lockPct) return pk * (1 - lockPct / 100);
        if (lockRs) return pk - lockRs;
        return null;
      });

      // Time → x-position mapping using pts time strings
      const timeToX = (hm) => {
        // Find nearest index by lexicographic time comparison
        let best = 0, bestDiff = Infinity;
        pts.forEach((p, i) => {
          const diff = Math.abs(hm.localeCompare(p[0]));
          // Use numeric difference via minutes
          const toMin = t => { const [h, m] = t.split(':').map(Number); return h * 60 + m; };
          const d = Math.abs(toMin(hm) - toMin(p[0]));
          if (d < bestDiff) { bestDiff = d; best = i; }
        });
        return PAD.l + (best / (pts.length - 1)) * gW;
      };

      const allVals = [...totals, ...floors.filter(f => f !== null)];
      if (profitTarget) allVals.push(profitTarget);
      const minV = Math.min(...allVals, 0);
      const maxV = Math.max(...allVals, 0);
      const range = maxV - minV || 1;

      const px = (i) => PAD.l + (i / (pts.length - 1)) * gW;
      const py = (v) => PAD.t + gH - ((v - minV) / range) * gH;

      const mtmPath = totals.map((v, i) => `${i === 0 ? 'M' : 'L'}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(' ');

      let floorPath = '';
      let inSeg = false;
      floors.forEach((f, i) => {
        if (f === null) { inSeg = false; return; }
        floorPath += `${inSeg ? 'L' : 'M'}${px(i).toFixed(1)},${py(f).toFixed(1)} `;
        inSeg = true;
      });

      // 🎯 Daily Profit Target — dashed green horizontal line (account view only)
      let targetLine = '';
      if (isAll && profitTarget && profitTarget > 0) {
        const ty = py(profitTarget);
        if (ty >= PAD.t && ty <= PAD.t + gH) {
          targetLine = `
    <line x1="${PAD.l}" y1="${ty.toFixed(1)}" x2="${W - PAD.r}" y2="${ty.toFixed(1)}"
          stroke="#3fb950" stroke-width="1.5" stroke-dasharray="8,4" opacity="0.85"/>
    <text x="${(W - PAD.r + 4).toFixed(1)}" y="${(ty - 3).toFixed(1)}" font-size="10" fill="#3fb950">🎯 ₹${Math.round(profitTarget).toLocaleString('en-IN')}</text>`;
        }
      }

      // ▲ Entry markers — small green triangle at each entry_time on the P&L curve
      // (account-level markers; shown only on the "__all" view)
      let entryMarkers = '';
      if (isAll) entries.forEach(([etime, cumAtEntry, sym]) => {
        const ex = timeToX(etime);
        const ey = py(cumAtEntry);
        if (ey < PAD.t || ey > PAD.t + gH) return;
        // Upward triangle pointing at the curve
        const tw = 6;
        entryMarkers += `
    <polygon points="${ex},${(ey + 1).toFixed(1)} ${(ex - tw).toFixed(1)},${(ey + tw * 1.6).toFixed(1)} ${(ex + tw).toFixed(1)},${(ey + tw * 1.6).toFixed(1)}"
             fill="#58a6ff" stroke="none" opacity="0.9">
      <title>${etime} ENTRY${sym ? ': ' + sym : ''} (P&amp;L then: ₹${Math.round(cumAtEntry).toLocaleString('en-IN')})</title>
    </polygon>`;
      });

      const z0 = py(0);
      const zeroLine = (z0 >= PAD.t && z0 <= PAD.t + gH)
        ? `<line x1="${PAD.l}" y1="${z0.toFixed(1)}" x2="${W - PAD.r}" y2="${z0.toFixed(1)}" stroke="#30363d" stroke-width="1" stroke-dasharray="3,3"/>`
        : '';

      let yLabels = '';
      for (let s = 0; s <= 4; s++) {
        const v = minV + (range * s / 4);
        const y = py(v);
        const col = v > 0 ? '#3fb950' : v < 0 ? '#f85149' : '#6e7681';
        yLabels += `<line x1="${PAD.l}" y1="${y.toFixed(1)}" x2="${W - PAD.r}" y2="${y.toFixed(1)}" stroke="#161b22" stroke-width="1"/>`;
        yLabels += `<text x="${PAD.l - 5}" y="${(y + 4).toFixed(1)}" text-anchor="end" font-size="10" fill="${col}">${Math.round(v).toLocaleString('en-IN')}</text>`;
      }

      let xLabels = '';
      const step = Math.max(1, Math.floor(pts.length / 6));
      for (let i = 0; i < pts.length; i += step) {
        xLabels += `<text x="${px(i).toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="10" fill="#6e7681">${pts[i][0]}</text>`;
      }

      const lastMtm = rawTotals[rawTotals.length - 1];   // plotted series' last value
      const lastPk = dailyPeakEver[dailyPeakEver.length - 1];
      const lastFlr = floors[floors.length - 1];
      const mtmCol = lastMtm >= 0 ? '#3fb950' : '#f85149';
      const drawDown = lastPk > 0 ? lastPk - lastMtm : 0;
      const lockLabel = lockPct ? `${lockPct}% floor` : lockRs ? `₹${lockRs} floor` : 'floor';

      let rightLabels = `
    <circle cx="${(W - PAD.r + 4).toFixed(1)}" cy="${py(lastMtm).toFixed(1)}" r="3" fill="${mtmCol}"/>
    <text x="${(W - PAD.r + 8).toFixed(1)}" y="${(py(lastMtm) + 4).toFixed(1)}" font-size="11" fill="${mtmCol}" font-weight="600">₹${Math.round(lastMtm).toLocaleString('en-IN')}</text>`;
      if (lastFlr !== null) {
        rightLabels += `
    <circle cx="${(W - PAD.r + 4).toFixed(1)}" cy="${py(lastFlr).toFixed(1)}" r="3" fill="#f0883e"/>
    <text x="${(W - PAD.r + 8).toFixed(1)}" y="${(py(lastFlr) + 4).toFixed(1)}" font-size="10" fill="#f0883e">${lockLabel} ₹${Math.round(lastFlr).toLocaleString('en-IN')}</text>`;
      }

      const ddBadge = (isAll && lastPk > 0)
        ? `<text x="${(W / 2).toFixed(1)}" y="${PAD.t - 2}" text-anchor="middle" font-size="10" fill="#8b949e">Peak ₹${Math.round(lastPk).toLocaleString('en-IN')}  |  DD ₹${Math.round(drawDown).toLocaleString('en-IN')}${entries.length ? '  |  ' + entries.length + ' entries' : ''}</text>`
        : '';

      container.innerHTML = `
  <svg width="${W}" height="${H}" style="overflow:visible;display:block">
    ${yLabels}${zeroLine}${xLabels}${ddBadge}
    ${targetLine}
    <path d="${mtmPath}" fill="none" stroke="${mtmCol}" stroke-width="2.5"/>
    ${floorPath ? `<path d="${floorPath}" fill="none" stroke="#f0883e" stroke-width="1.5" stroke-dasharray="6,4"/>` : ''}
    ${entryMarkers}
    ${rightLabels}
  </svg>`;

      // graph sub-header: which scope + which P&L mode is showing
      const _gs = document.getElementById('peak-graph-scope');
      if (_gs) {
        const nm = isAll ? 'All strategies' : (regLabel(_pk) || _pk);
        const pmL = { real: 'Realised', unreal: 'Unrealised', cur: 'Current (R+U)' }[_pm] || 'Current';
        _gs.textContent = nm + ' — ' + pmL;
      }
    }

    // Reconstruct a realised step-curve (cumulative net by exit_time) from the
    // currently-loaded completed trades, aligned to the peak graph's time points.
    // Used when the backend has no per-strategy snapshot for this key/date (past
    // dates). Orders are already scoped to _peakStrat (picking a strategy filters
    // them), so summing all loaded details gives that strategy's own curve.
    function _reconstructPeakSeries(pts) {
      const det = ((window._lastOrdersData || {}).details) || [];
      const toMin = s => { const p = String(s).split(':'); return (+p[0] || 0) * 60 + (+p[1] || 0); };
      const comp = det.filter(t => t.exit_time && t._net != null && _peakTradeMatch(t))
        .map(t => ({ x: toMin(t.exit_time), v: t._net }))
        .sort((a, b) => a.x - b.x);
      return pts.map(p => {
        const tm = toMin(p[0]);
        let cum = 0;
        for (const c of comp) { if (c.x <= tm) cum += c.v; else break; }
        return cum;
      });
    }

    async function saveRiskConfig() {
      let cfg = {};
      try { const r2 = await fetch('/api/config'); cfg = await r2.json(); } catch (e) { }
      const reserved = new Set(['_risk', 'webhooks']);
      // drop internal _-prefixed keys (e.g. _ui_config) — they aren't strategies
      const stratIds = Object.keys(cfg).filter(k => !reserved.has(k) && !k.startsWith('_'));
      // webhook strategies bhi (merged table me unki bhi override row hai)
      Object.keys(cfg.webhooks || {}).forEach(w => { if (!stratIds.includes(w)) stratIds.push(w); });

      const gPct = document.getElementById('risk-global-pct').value;
      const gRs = document.getElementById('risk-global-rs').value;
      const gCap = document.getElementById('risk-global-capital').value;
      const gTotalCap = document.getElementById('risk-global-total-capital').value;
      const gMar = document.getElementById('risk-global-margin').value;
      const gMode = document.getElementById('risk-global-mode').value;
      const gShadow = document.getElementById('risk-global-shadow').value;
      const gInstLoss = document.getElementById('risk-global-instloss').value;
      const gHedgeStrikes = document.getElementById('risk-global-hedge-strikes').value;
      const gHedgePremium = document.getElementById('risk-global-hedge-premium').value;
      const gLiqFilter = document.getElementById('risk-global-liqfilter').value;
      const gProfitTarget = document.getElementById('risk-global-profit-target').value;
      const gHedgeEnabled = document.getElementById('risk-global-hedge-mode').value;
      const payload = {
        global: {
          default_broker: document.getElementById('default-broker-select').value || 'dhan',
          max_loss_pct: gPct !== '' ? parseFloat(gPct) : null,
          max_loss_rs: gRs !== '' ? parseFloat(gRs) : null,
          capital_rs: gCap !== '' ? parseFloat(gCap) : null,
          total_capital_rs: gTotalCap !== '' ? parseFloat(gTotalCap) : null,
          margin_multiplier: gMar !== '' ? parseFloat(gMar) : null,
          capital_mode: gMode || 'reject',
          shadow_live: gShadow === 'true',
          default_sl_rs: gInstLoss !== '' ? parseFloat(gInstLoss) : null,
          // --- Added by Antigravity AI: Save Default SL/Target & Custom Trailing Steps ---
          default_sl_type: document.getElementById('risk-global-default-sl-type').value || null,
          default_sl_val: document.getElementById('risk-global-default-sl-val').value !== '' ? document.getElementById('risk-global-default-sl-val').value : null,
          default_sl_candle_close: document.getElementById('risk-global-default-sl-candle').checked,
          default_tp_type: document.getElementById('risk-global-default-tp-type').value || null,
          default_tp_val: document.getElementById('risk-global-default-tp-val').value !== '' ? document.getElementById('risk-global-default-tp-val').value : null,
          default_tp_candle_close: document.getElementById('risk-global-default-tp-candle').checked,
          trailing_step_band_1: document.getElementById('risk-global-trailing-step-band1').value !== '' ? parseFloat(document.getElementById('risk-global-trailing-step-band1').value) : null,
          trailing_step_band_2: document.getElementById('risk-global-trailing-step-band2').value !== '' ? parseFloat(document.getElementById('risk-global-trailing-step-band2').value) : null,
          trailing_step_band_3: document.getElementById('risk-global-trailing-step-band3').value !== '' ? parseFloat(document.getElementById('risk-global-trailing-step-band3').value) : null,
          trailing_step_band_4: document.getElementById('risk-global-trailing-step-band4').value !== '' ? parseFloat(document.getElementById('risk-global-trailing-step-band4').value) : null,
          // --- End Antigravity AI addition ---
          hedge_offset_strikes: gHedgeStrikes !== '' ? parseInt(gHedgeStrikes) : null,
          hedge_max_premium_rs: gHedgePremium !== '' ? parseFloat(gHedgePremium) : null,
          liquidity_filter: gLiqFilter === 'true',
          profit_target_rs: gProfitTarget !== '' ? parseFloat(gProfitTarget) : null,
          hedge_enabled: gHedgeEnabled === 'true',
          // Max-premium per-index entry cap (2026-07-07) — same wholesale-replace
          // caveat: /api/risk-config POST replaces _risk.global wholesale, so
          // these must be present in every save payload.
          max_premium_nifty: document.getElementById('risk-maxprem-nifty').value !== '' ? parseFloat(document.getElementById('risk-maxprem-nifty').value) : null,
          max_premium_banknifty: document.getElementById('risk-maxprem-banknifty').value !== '' ? parseFloat(document.getElementById('risk-maxprem-banknifty').value) : null,
          max_premium_stock: document.getElementById('risk-maxprem-stock').value !== '' ? parseFloat(document.getElementById('risk-maxprem-stock').value) : null,
          // Exit & no-entry time (single-source for ALL strategies + webhook) — same
          // wholesale-replace caveat: must be in every save payload.
          auto_squareoff_at: document.getElementById('risk-exit-squareoff').value || '15:15',
          no_entry_after: document.getElementById('risk-exit-noentry').value || '15:15',
          // Per-Trade Default SL merge (2026-07-07) — one mode drives Legacy /
          // Dropdown / Aggressive. Same wholesale-replace caveat as below.
          default_sl_mode: document.getElementById('risk-defsl-mode').value,
          default_sl_enabled: document.getElementById('risk-defsl-enabled').value === 'true',
          default_legacy_sl_rs: document.getElementById('risk-legacy-sl').value !== '' ? parseFloat(document.getElementById('risk-legacy-sl').value) : null,
          default_legacy_tp_rs: document.getElementById('risk-legacy-tp').value !== '' ? parseFloat(document.getElementById('risk-legacy-tp').value) : null,
          // PER-INSTRUMENT LOCK + KILL-FLOOR (2026-07-02) — /api/risk-config POST
          // replaces _risk wholesale, so these MUST be in every save payload or
          // saving any other risk setting would silently erase them.
          per_instrument_lock_enabled: document.getElementById('risk-per-instrument-enabled').value === 'true',
          per_instrument_lock_arm_rs: document.getElementById('risk-per-instrument-arm').value !== '' ? parseFloat(document.getElementById('risk-per-instrument-arm').value) : null,
          per_instrument_lock_gap_rs: document.getElementById('risk-per-instrument-gap').value !== '' ? parseFloat(document.getElementById('risk-per-instrument-gap').value) : null,
          per_instrument_lock_confirm_secs: document.getElementById('risk-per-instrument-confirm').value !== '' ? parseFloat(document.getElementById('risk-per-instrument-confirm').value) : null,
          kill_floor_enabled: document.getElementById('risk-killfloor-enabled').value === 'true',
          kill_floor_arm_rs: document.getElementById('risk-killfloor-arm').value !== '' ? parseFloat(document.getElementById('risk-killfloor-arm').value) : null,
          kill_floor_gap_rs: document.getElementById('risk-killfloor-gap').value !== '' ? parseFloat(document.getElementById('risk-killfloor-gap').value) : null,
          kill_floor_confirm_secs: document.getElementById('risk-killfloor-confirm').value !== '' ? parseFloat(document.getElementById('risk-killfloor-confirm').value) : null,
          // DEFAULT TARGET/SL exit profile (2026-07-04) — same wholesale-replace caveat as above
          default_tsl_enabled: document.getElementById('risk-tsl-enabled').value === 'true',
          default_tsl_target_per_lot: document.getElementById('risk-tsl-target').value !== '' ? parseFloat(document.getElementById('risk-tsl-target').value) : null,
          default_tsl_initial_sl_per_lot: document.getElementById('risk-tsl-initsl').value !== '' ? parseFloat(document.getElementById('risk-tsl-initsl').value) : null,
          default_tsl_favour_step: document.getElementById('risk-tsl-fav').value !== '' ? parseFloat(document.getElementById('risk-tsl-fav').value) : null,
          default_tsl_sl_move: document.getElementById('risk-tsl-move').value !== '' ? parseFloat(document.getElementById('risk-tsl-move').value) : null,
          default_tsl_aggressive_pct: document.getElementById('risk-tsl-aggpct').value !== '' ? parseFloat(document.getElementById('risk-tsl-aggpct').value) : null,
          default_tsl_aggressive_mult: document.getElementById('risk-tsl-aggmult').value !== '' ? parseFloat(document.getElementById('risk-tsl-aggmult').value) : null,
          default_tsl_min_cushion: document.getElementById('risk-tsl-cushion').value !== '' ? parseFloat(document.getElementById('risk-tsl-cushion').value) : null,
        },
        per_strategy: {}
      };
      stratIds.forEach(id => {
        const pEl = document.getElementById(`risk-pct-${id}`);
        const rEl = document.getElementById(`risk-rs-${id}`);
        const ptEl = document.getElementById(`risk-profit-${id}`);
        const mtEl = document.getElementById(`risk-maxtrades-${id}`);
        const cEl = document.getElementById(`risk-capital-${id}`);
        const mEl = document.getElementById(`risk-margin-${id}`);
        const modeEl = document.getElementById(`risk-mode-${id}`);
        const shadowEl = document.getElementById(`risk-shadow-${id}`);
        const instLossEl = document.getElementById(`risk-instloss-${id}`);
        const dslEnEl = document.getElementById(`risk-dslen-${id}`);
        const dslModeEl = document.getElementById(`risk-dslmode-${id}`);
        const hedgeStrikesEl = document.getElementById(`risk-hedgestrikes-${id}`);
        const hedgePremiumEl = document.getElementById(`risk-hedgepremium-${id}`);
        if (!pEl || !rEl) return;
        const p = pEl.value, r = rEl.value, c = cEl ? cEl.value : '', m = mEl ? mEl.value : '';
        const pt = ptEl ? ptEl.value : '';
        const mt = mtEl ? mtEl.value : '';
        const mode = modeEl ? modeEl.value : '';
        const shadow = shadowEl ? shadowEl.value : '';
        const instLoss = instLossEl ? instLossEl.value : '';
        const dslEn = dslEnEl ? dslEnEl.value : '';
        const dslMode = dslModeEl ? dslModeEl.value : '';
        const hedgeStrikes = hedgeStrikesEl ? hedgeStrikesEl.value : '';
        const hedgePremium = hedgePremiumEl ? hedgePremiumEl.value : '';
        const dslVals = (window._dslVals || {})[id] || {};
        const hasDslVals = Object.keys(dslVals).length > 0;
        if (p !== '' || r !== '' || pt !== '' || mt !== '' || c !== '' || m !== '' || mode !== '' || shadow !== '' || instLoss !== ''
          || dslEn !== '' || dslMode !== '' || hasDslVals || hedgeStrikes !== '' || hedgePremium !== '') {
          payload.per_strategy[id] = {
            max_loss_pct: p !== '' ? parseFloat(p) : null,
            max_loss_rs: r !== '' ? parseFloat(r) : null,
            profit_target_rs: pt !== '' ? parseFloat(pt) : null,
            max_trades_per_day: mt !== '' ? parseInt(mt) : null,
            capital_rs: c !== '' ? parseFloat(c) : null,
            margin_multiplier: m !== '' ? parseFloat(m) : null,
            capital_mode: mode || null,
            shadow_live: shadow === '' ? null : (shadow === 'true'),
            default_sl_rs: instLoss !== '' ? parseFloat(instLoss) : null,
            default_sl_enabled: dslEn === '' ? null : (dslEn === 'true'),
            default_sl_mode: dslMode || null,
            hedge_offset_strikes: hedgeStrikes !== '' ? parseInt(hedgeStrikes) : null,
            hedge_max_premium_rs: hedgePremium !== '' ? parseFloat(hedgePremium) : null,
            ...dslVals,   // task 81 ⚙ — per-strategy Default SL/Target value overrides
          };
        }
      });
      try {
        const res = await fetch('/api/risk-config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const j = await res.json();
        document.getElementById('risk-msg').textContent = j.msg || 'Saved';
        RISK_CFG = payload;
        // Task 13 — if the Change History panel is open, refresh it so a just-saved
        // change shows up immediately.
        const _ap = document.getElementById('rms-audit-panel');
        if (_ap && _ap.style.display !== 'none') loadRmsAudit();
      } catch (e) {
        document.getElementById('risk-msg').textContent = 'Save failed: ' + e;
      }
    }

    // ── task 81 ⚙ — per-strategy Default SL/Target VALUE overrides ──────────────
    // Modal editor next to the Mode dropdown in Per-Strategy Override. Values live
    // in window._dslVals[id] and merge into saveRiskConfig's payload (blank =
    // inherit global card's value). Backend: risk_gate default_instrument_sl_tags /
    // default_target_sl_config read per_strategy[<sid>] first.
    const DSL_VAL_KEYS = ['default_legacy_tp_rs', 'default_legacy_sl_rs',
      'default_sl_type', 'default_sl_val', 'default_sl_candle_close',
      'default_tp_type', 'default_tp_val', 'default_tp_candle_close',
      'default_tsl_target_per_lot', 'default_tsl_initial_sl_per_lot', 'default_tsl_favour_step',
      'default_tsl_sl_move', 'default_tsl_aggressive_pct', 'default_tsl_aggressive_mult',
      'default_tsl_min_cushion'];
    const _DSL_TYPE_OPTS = [['', '(global default)'], ['pct', '%'], ['pt', 'Points (premium)'],
      ['trailing_pt', 'Trailing Points (premium)'], ['rs', 'Amount (₹ per lot)'],
      ['premium', 'Premium level'], ['index', 'Index/Underlying level']];
    function _globalDslMode() {
      const g = (RISK_CFG && RISK_CFG.global) || {};
      if (['legacy', 'dropdown', 'aggressive'].includes(g.default_sl_mode)) return g.default_sl_mode;
      if (g.default_tsl_enabled) return 'aggressive';
      if (g.default_sl_type) return 'dropdown';
      if (g.default_sl_rs) return 'legacy';
      return 'dropdown';
    }
    function _dslField(k, label, ph, val, kind) {
      const base = 'width:150px;background:#0d1117;color:#e6edf3;padding:5px;border:1px solid #30363d;border-radius:4px';
      let inp;
      if (kind === 'seltype') {
        inp = `<select id="dslv-${k}" style="${base}">` + _DSL_TYPE_OPTS.map(o =>
          `<option value="${o[0]}" ${String(val ?? '') === o[0] ? 'selected' : ''}>${o[1]}</option>`).join('') + '</select>';
      } else if (kind === 'tri') {
        const cur = val === true || val === 'true' ? 'true' : (val === false || val === 'false' ? 'false' : '');
        inp = `<select id="dslv-${k}" style="${base}">
          <option value="" ${cur === '' ? 'selected' : ''}>global default</option>
          <option value="true" ${cur === 'true' ? 'selected' : ''}>ON</option>
          <option value="false" ${cur === 'false' ? 'selected' : ''}>OFF</option></select>`;
      } else {
        inp = `<input id="dslv-${k}" type="${kind === 'num' ? 'number' : 'text'}" step="any"
          placeholder="global: ${ph}" value="${val ?? ''}" style="${base}">`;
      }
      return `<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin:7px 0">
        <span style="color:#adbac7;font-size:12.5px">${label}</span>${inp}</div>`;
    }
    function openDslValModal(id) {
      const g = (RISK_CFG && RISK_CFG.global) || {};
      const rowModeEl = document.getElementById(`risk-dslmode-${id}`);
      const mode = (rowModeEl && rowModeEl.value) || _globalDslMode();
      const dv = (window._dslVals || {})[id] || {};
      let fields = '';
      if (mode === 'legacy') {
        fields = _dslField('default_legacy_tp_rs', 'Fixed Target ₹ / lot', g.default_legacy_tp_rs ?? 5000, dv.default_legacy_tp_rs, 'num')
          + _dslField('default_legacy_sl_rs', 'Fixed SL ₹ / lot', g.default_legacy_sl_rs ?? 2000, dv.default_legacy_sl_rs, 'num');
      } else if (mode === 'aggressive') {
        fields = _dslField('default_tsl_target_per_lot', 'Target ₹/lot', g.default_tsl_target_per_lot ?? 2000, dv.default_tsl_target_per_lot, 'num')
          + _dslField('default_tsl_initial_sl_per_lot', 'Initial SL ₹/lot', g.default_tsl_initial_sl_per_lot ?? 1000, dv.default_tsl_initial_sl_per_lot, 'num')
          + _dslField('default_tsl_favour_step', 'Favour step ₹/lot', g.default_tsl_favour_step ?? 100, dv.default_tsl_favour_step, 'num')
          + _dslField('default_tsl_sl_move', 'SL move ₹/lot', g.default_tsl_sl_move ?? 100, dv.default_tsl_sl_move, 'num')
          + _dslField('default_tsl_aggressive_pct', 'Aggressive @ %', g.default_tsl_aggressive_pct ?? 50, dv.default_tsl_aggressive_pct, 'num')
          + _dslField('default_tsl_aggressive_mult', 'Aggressive mult', g.default_tsl_aggressive_mult ?? 2, dv.default_tsl_aggressive_mult, 'num')
          + _dslField('default_tsl_min_cushion', 'Min cushion ₹/lot', g.default_tsl_min_cushion ?? 0, dv.default_tsl_min_cushion, 'num');
      } else {
        fields = _dslField('default_sl_type', 'SL Type', '', dv.default_sl_type, 'seltype')
          + _dslField('default_sl_val', 'SL Value', g.default_sl_val ?? '', dv.default_sl_val, 'text')
          + _dslField('default_sl_candle_close', 'SL — Candle Close only 🕯', dv.default_sl_candle_close, dv.default_sl_candle_close, 'tri')
          + _dslField('default_tp_type', 'Target Type', '', dv.default_tp_type, 'seltype')
          + _dslField('default_tp_val', 'Target Value', g.default_tp_val ?? '', dv.default_tp_val, 'text')
          + _dslField('default_tp_candle_close', 'Target — Candle Close only 🕯', dv.default_tp_candle_close, dv.default_tp_candle_close, 'tri');
      }
      const modeName = { legacy: 'Legacy — fixed ₹', dropdown: 'Dropdown — type+value', aggressive: 'Aggressive — Per-lot trail' }[mode];
      let m = document.getElementById('dsl-val-modal');
      if (!m) { m = document.createElement('div'); m.id = 'dsl-val-modal'; document.body.appendChild(m); }
      m.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:10001;display:flex;align-items:center;justify-content:center';
      m.innerHTML = `<div style="background:#161b22;border:1px solid #d29922;border-radius:10px;padding:20px 22px;min-width:420px;max-width:${mode === 'aggressive' ? '760px' : '520px'};max-height:90vh;overflow:auto" onclick="event.stopPropagation()">
        <div style="font-weight:700;font-size:14px;margin-bottom:2px;color:#e6edf3">🎯 ${(typeof regLabel === 'function' && regLabel(id)) || id} — Default SL/Target values</div>
        <div style="color:#8b949e;font-size:11.5px;margin-bottom:10px">Mode: <b style="color:#d29922">${modeName}</b> — blank = global fallback value. Yahan set karo to sirf IS strategy ke naye trades pe lagegi.</div>
        ${fields}
        ${mode === 'aggressive' ? '<div id="dslv-graph-mount" style="margin-top:12px"></div>' : ''}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
          <button onclick="clearDslVals('${id}')" style="padding:6px 12px;background:#21262d;color:#f85149;border:1px solid #30363d;border-radius:6px;cursor:pointer">Clear (global use karo)</button>
          <button onclick="closeDslValModal()" style="padding:6px 12px;background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:6px;cursor:pointer">Cancel</button>
          <button onclick="applyDslVals('${id}','${mode}')" style="padding:6px 12px;background:#d29922;color:#0d1117;border:0;border-radius:6px;cursor:pointer;font-weight:700">Apply</button>
        </div>
        <div style="color:#6e7681;font-size:10.5px;margin-top:8px">Apply ke baad table ka 💾 Save dabana zaroori hai — tabhi VPS config me likhta hai.</div>
      </div>`;
      m.onclick = closeDslValModal;
      if (mode === 'aggressive') {
        // Live graph preview (the retired global card's own graph block, moved here
        // on demand — same hover/Table tab, ids stay unique because it's ONE node).
        const mount = document.getElementById('dslv-graph-mount');
        const blk = document.getElementById('tsl-preview-block');
        if (mount && blk) mount.appendChild(blk);
        ['default_tsl_target_per_lot', 'default_tsl_initial_sl_per_lot', 'default_tsl_favour_step',
          'default_tsl_sl_move', 'default_tsl_aggressive_pct', 'default_tsl_aggressive_mult',
          'default_tsl_min_cushion'].forEach(k => {
            const el = document.getElementById('dslv-' + k);
            if (el) el.oninput = _dslvGraph;
          });
        _dslvGraph();
      }
    }
    // Effective aggressive cfg for the modal graph: modal field → global → default
    function _dslvGraph() {
      const g = (RISK_CFG && RISK_CFG.global) || {};
      const eff = (k, d) => {
        const el = document.getElementById('dslv-' + k);
        let v = el && el.value !== '' ? parseFloat(el.value) : NaN;
        if (isNaN(v) && g[k] !== undefined && g[k] !== null && String(g[k]) !== '') v = parseFloat(g[k]);
        return isNaN(v) ? d : v;
      };
      const cfg = {
        tgt: eff('default_tsl_target_per_lot', 2000), initSL: eff('default_tsl_initial_sl_per_lot', 1000),
        fav: Math.max(1, eff('default_tsl_favour_step', 100)), mov: eff('default_tsl_sl_move', 100),
        aggpct: eff('default_tsl_aggressive_pct', 50), aggmult: eff('default_tsl_aggressive_mult', 2),
        cushion: eff('default_tsl_min_cushion', 0)
      };
      try { renderTslPreview(cfg); } catch (e) { }
    }
    function closeDslValModal() {
      // return the shared graph block to its (hidden) home before tearing the modal down
      const home = document.getElementById('tsl-preview-home');
      const blk = document.getElementById('tsl-preview-block');
      if (home && blk && blk.parentElement !== home) home.appendChild(blk);
      const m = document.getElementById('dsl-val-modal');
      if (m) m.remove();
    }
    // Excel-style Advanced column-group toggle (Capital/Margin/Mode/Shadow/Hedge)
    function togglePsAdv() {
      const t = document.getElementById('ps-ovr-table');
      const b = document.getElementById('psadv-toggle');
      if (!t) return;
      const collapsed = t.classList.toggle('advc');
      try { localStorage.setItem('ps_adv_collapsed', collapsed ? '1' : '0'); } catch (e) { }
      if (b) b.textContent = collapsed ? '⊞ Advanced columns dikhao' : '⊟ Advanced columns chhupao';
    }
    function clearDslVals(id) {
      if (window._dslVals) delete window._dslVals[id];
      _dslBtnRefresh(id);
      closeDslValModal();
      const msg = document.getElementById('risk-msg');
      if (msg) msg.textContent = '⚙ values cleared — ab 💾 Save dabao';
    }
    function applyDslVals(id, mode) {
      const keys = mode === 'legacy' ? ['default_legacy_tp_rs', 'default_legacy_sl_rs']
        : mode === 'aggressive' ? ['default_tsl_target_per_lot', 'default_tsl_initial_sl_per_lot', 'default_tsl_favour_step',
          'default_tsl_sl_move', 'default_tsl_aggressive_pct', 'default_tsl_aggressive_mult', 'default_tsl_min_cushion']
        : ['default_sl_type', 'default_sl_val', 'default_sl_candle_close', 'default_tp_type', 'default_tp_val', 'default_tp_candle_close'];
      window._dslVals = window._dslVals || {};
      const dv = { ...(window._dslVals[id] || {}) };
      keys.forEach(k => {
        const el = document.getElementById(`dslv-${k}`);
        if (!el) return;
        const v = el.value;
        if (v === '' || v == null) { delete dv[k]; return; }
        if (k.endsWith('candle_close')) dv[k] = (v === 'true');
        else if (k.endsWith('_type') || k === 'default_sl_val' || k === 'default_tp_val') dv[k] = v;   // val can be "gap:step" for trailing_pt
        else dv[k] = parseFloat(v);
      });
      if (Object.keys(dv).length) window._dslVals[id] = dv; else delete window._dslVals[id];
      _dslBtnRefresh(id);
      closeDslValModal();
      const msg = document.getElementById('risk-msg');
      if (msg) msg.textContent = '⚙ values set — ab 💾 Save dabao';
    }
    function _dslBtnRefresh(id) {
      const b = document.getElementById(`risk-dslvals-btn-${id}`);
      if (!b) return;
      const has = !!(window._dslVals && window._dslVals[id]);
      b.style.background = has ? '#3a2b0a' : '#0d1117';
      b.style.color = has ? '#e0a325' : '#8b949e';
    }

    // ── Task 13 — RMS value-change audit log ──
    let _rmsAuditData = [];
    function toggleRmsAudit() {
      const p = document.getElementById('rms-audit-panel');
      if (!p) return;
      const show = p.style.display === 'none';
      p.style.display = show ? 'block' : 'none';
      if (show) loadRmsAudit();
    }
    async function loadRmsAudit() {
      const body = document.getElementById('rms-audit-body');
      body.innerHTML = '<div style="padding:14px;color:#8b949e">Loading…</div>';
      try {
        const r = await fetch('/api/rms-audit-log');
        _rmsAuditData = await r.json();
      } catch (e) {
        _rmsAuditData = [];
        body.innerHTML = '<div style="padding:14px;color:#f85149">Load failed: ' + e + '</div>';
        return;
      }
      renderRmsAudit();
    }
    function renderRmsAudit() {
      const body = document.getElementById('rms-audit-body');
      const q = (document.getElementById('rms-audit-filter').value || '').toLowerCase().trim();
      const rows = (_rmsAuditData || []).filter(r =>
        !q || (r.field || '').toLowerCase().includes(q) || (r.scope || '').toLowerCase().includes(q));
      if (!rows.length) {
        body.innerHTML = '<div style="padding:14px;color:#8b949e">' +
          (_rmsAuditData.length ? 'No matches.' : 'No changes recorded yet. Save a Risk setting to start the history.') + '</div>';
        return;
      }
      const fmtDate = ts => {
        // "2026-07-08 11:42:03" → "08 Jul, 11:42"
        try {
          const [d, t] = ts.split(' ');
          const [y, m, day] = d.split('-');
          const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][parseInt(m) - 1];
          return `${day} ${mon}, ${t.slice(0, 5)}`;
        } catch (e) { return ts; }
      };
      const esc = s => String(s == null || s === '' ? '—' : s).replace(/</g, '&lt;');
      let h = '<div style="display:grid;grid-template-columns:120px 1fr 96px 96px 90px;gap:0;font-size:10.5px;color:#8b949e;font-weight:700;padding:8px 12px;border-bottom:1px solid #21262d;background:#0d1117;position:sticky;top:0">' +
        '<div>Date / Time</div><div>Field</div><div style="text-align:right">Old</div><div style="text-align:right">New</div><div style="text-align:right">Scope</div></div>';
      rows.forEach(r => {
        h += '<div style="display:grid;grid-template-columns:120px 1fr 96px 96px 90px;padding:7px 12px;border-bottom:1px solid #161b22">' +
          `<div style="color:#adbac7">${fmtDate(r.ts)}</div>` +
          `<div style="color:#e6edf3">${esc(r.field)}</div>` +
          `<div style="text-align:right;color:#f85149">${esc(r.old)}</div>` +
          `<div style="text-align:right;color:#3fb950">${esc(r.new)}</div>` +
          `<div style="text-align:right;color:#6e7681">${esc(r.scope)}</div></div>`;
      });
      body.innerHTML = h;
    }

    // ── Default Target/SL exit profile — live Graph/Table preview (2026-07-04) ──
    let _tslGeo = null, _tslPts = null;
    function _tslINR(n) { return (n < 0 ? '-' : '') + '₹' + Math.abs(Math.round(n)).toLocaleString('en-IN'); }
    function _tslCfg() {
      const g = id => { const v = parseFloat(document.getElementById(id).value); return isNaN(v) ? null : v; };
      return {
        tgt: g('risk-tsl-target') ?? 2000, initSL: g('risk-tsl-initsl') ?? 1000,
        fav: Math.max(1, g('risk-tsl-fav') ?? 100), mov: g('risk-tsl-move') ?? 100,
        aggpct: g('risk-tsl-aggpct') ?? 50, aggmult: g('risk-tsl-aggmult') ?? 2,
        cushion: g('risk-tsl-cushion') ?? 0
      };
    }
    function _tslCompute(c) {
      const aggAt = c.tgt * c.aggpct / 100; let sl = -c.initSL, p = 0, be = null, g = 0;
      const pts = [{ p: 0, sl: sl, ph: 'Start', mv: 0 }];
      while (p < c.tgt && g < 5000) {
        g++; p += c.fav; if (p > c.tgt) p = c.tgt;
        const agg = p > aggAt, mv = agg ? c.mov * c.aggmult : c.mov; sl += mv;
        const ceil = p - c.cushion; if (sl > ceil) sl = ceil; if (sl < -c.initSL) sl = -c.initSL;
        if (p === c.tgt) { pts.push({ p, sl, ph: 'Target', mv: 0, exit: true }); break; }
        if (be === null && sl >= 0) be = p;
        pts.push({ p, sl, ph: agg ? 'Aggressive' : 'Normal', mv });
      }
      return { pts, aggAt, be, tgt: c.tgt, initSL: c.initSL, cushion: c.cushion };
    }
    // Per-Trade Default SL merge (2026-07-07) — show only the active mode's
    // fields (Legacy / Dropdown / Aggressive). Only one is ever applied.
    function setDefaultSlMode(mode) {
      // 2026-07-14: global Per-Trade Default SL card RETIRED (user request) —
      // Per-Strategy Override (Enabled/Mode + ⚙ values) is the only UI now. The
      // mode sub-rows stay permanently hidden (inputs kept in DOM so load/save
      // keep preserving the stored global fallback values).
      ['sl-mode-legacy-row', 'sl-mode-dropdown-row', 'sl-mode-aggressive-row', 'sl-mode-aggressive-graph']
        .forEach(id => { const el = document.getElementById(id); if (el) el.style.display = 'none'; });
    }

    function renderTslPreview(cfgOverride) {
      // cfgOverride (task 81 ⚙ modal) — render with an explicit cfg (per-strategy
      // effective values) instead of reading the retired global card's inputs.
      const wrap = document.getElementById('tsl-svg-wrap'); if (!wrap) return;
      const c = cfgOverride || _tslCfg(); if (c.tgt <= 0) { wrap.innerHTML = '<div style="color:#8b949e;font-size:12px;padding:10px">Target ₹/lot 0 se bada rakho.</div>'; document.getElementById('tsl-preview-table').innerHTML = ''; return; }
      const d = _tslCompute(c);
      const L = 58, R = 650, T = 15, B = 205, PW = R - L, PH = B - T;
      const yMin = -d.initSL, yMax = d.tgt, span = (yMax - yMin) || 1;
      const X = p => L + (p / d.tgt) * PW, Y = v => B - ((v - yMin) / span) * PH;
      _tslGeo = { L, R, T, B, tgt: d.tgt, yMin, span, PW, PH }; _tslPts = d.pts;
      const slPath = d.pts.map((o, i) => (i ? 'L' : 'M') + X(o.p).toFixed(1) + ' ' + Y(o.sl).toFixed(1)).join(' ');
      const profPath = 'M' + X(0) + ' ' + Y(0) + ' L' + X(d.tgt).toFixed(1) + ' ' + Y(d.tgt).toFixed(1);
      const cushionPoly = 'M' + X(0) + ' ' + Y(0) + ' L' + X(d.tgt).toFixed(1) + ' ' + Y(d.tgt).toFixed(1) + ' L'
        + d.pts.map(o => X(o.p).toFixed(1) + ' ' + Y(o.sl).toFixed(1)).reverse().join(' L') + ' Z';
      const y0 = Y(0), xAgg = X(d.aggAt);
      const yt = [yMin, yMin / 2, 0, d.tgt / 2, d.tgt].filter((t, i, a) => a.indexOf(t) === i);
      const grid = yt.map(t => '<line x1="' + L + '" y1="' + Y(t).toFixed(1) + '" x2="' + R + '" y2="' + Y(t).toFixed(1) + '" stroke="#21262d" stroke-width="0.5"/>'
        + '<text x="' + (L - 6) + '" y="' + (Y(t) + 4).toFixed(1) + '" text-anchor="end" fill="#8b949e" font-size="10" font-family="monospace">' + _tslINR(t) + '</text>').join('');
      const xt = [0, d.aggAt, d.tgt].filter((t, i, a) => a.indexOf(t) === i);
      const xlab = xt.map(t => '<text x="' + X(t).toFixed(1) + '" y="' + (B + 16) + '" text-anchor="middle" fill="#8b949e" font-size="10" font-family="monospace">+' + _tslINR(t) + '</text>').join('');
      const beMark = d.be != null ? '<circle cx="' + X(d.be).toFixed(1) + '" cy="' + y0.toFixed(1) + '" r="3.5" fill="#58a6ff"/>'
        + '<text x="' + (X(d.be) + 6).toFixed(1) + '" y="' + (y0 - 7).toFixed(1) + '" fill="#c9d1d9" font-size="10">breakeven +' + _tslINR(d.be) + '</text>' : '';
      wrap.innerHTML = '<svg viewBox="0 0 680 230" width="100%" xmlns="http://www.w3.org/2000/svg" style="display:block">'
        + grid
        + '<line x1="' + L + '" y1="' + y0.toFixed(1) + '" x2="' + R + '" y2="' + y0.toFixed(1) + '" stroke="#484f58" stroke-width="1"/>'
        + '<path d="' + cushionPoly + '" fill="#1f6feb" opacity="0.14"/>'
        + '<line x1="' + xAgg.toFixed(1) + '" y1="' + T + '" x2="' + xAgg.toFixed(1) + '" y2="' + B + '" stroke="#d29922" stroke-width="1.5" stroke-dasharray="5 4"/>'
        + '<text x="' + (xAgg + 5).toFixed(1) + '" y="' + (T + 12) + '" fill="#d29922" font-size="10">aggressive ' + c.aggmult + 'x</text>'
        + '<path d="' + profPath + '" fill="none" stroke="#8b949e" stroke-width="1.6" stroke-dasharray="6 5"/>'
        + '<path d="' + slPath + '" fill="none" stroke="#58a6ff" stroke-width="2.4" stroke-linejoin="round"/>'
        + '<circle cx="' + X(0) + '" cy="' + Y(-d.initSL).toFixed(1) + '" r="3.5" fill="#f85149"/>'
        + '<text x="' + (L + 6) + '" y="' + (Y(-d.initSL) - 5).toFixed(1) + '" fill="#f85149" font-size="10">init SL ' + _tslINR(-d.initSL) + '</text>'
        + '<circle cx="' + X(d.tgt).toFixed(1) + '" cy="' + Y(d.tgt).toFixed(1) + '" r="3.5" fill="#3fb950"/>'
        + '<text x="' + (X(d.tgt) - 6).toFixed(1) + '" y="' + (Y(d.tgt) - 6).toFixed(1) + '" text-anchor="end" fill="#3fb950" font-size="10">target ' + _tslINR(d.tgt) + '</text>'
        + beMark + xlab
        + '<circle id="tsl-hover-dot" r="4" fill="#58a6ff" stroke="#0d1117" stroke-width="1.5" style="display:none"/>'
        + '<rect x="' + L + '" y="' + T + '" width="' + PW + '" height="' + PH + '" fill="transparent" style="cursor:crosshair"'
        + ' onmousemove="tslHover(event)" onmouseleave="tslHoverOut()"/>'
        + '</svg>';
      // table
      let rows = d.pts;
      if (rows.length > 60) { const step = Math.ceil(rows.length / 40); rows = rows.filter((_, i) => i % step === 0 || i === d.pts.length - 1); }
      document.getElementById('tsl-preview-table').innerHTML =
        '<table style="width:100%;border-collapse:collapse;font-size:11.5px;font-family:monospace">'
        + '<thead><tr style="position:sticky;top:0;background:#161b22;color:#8b949e">'
        + '<th style="padding:6px 10px;text-align:left">Profit</th><th style="padding:6px 10px;text-align:left">Phase</th>'
        + '<th style="padding:6px 10px;text-align:right">SL move</th><th style="padding:6px 10px;text-align:right">SL now</th></tr></thead><tbody>'
        + rows.map(x => {
          const slCol = x.sl > 0 ? '#3fb950' : (x.sl < 0 ? '#f85149' : '#8b949e');
          const phCol = x.exit ? '#3fb950' : (x.ph === 'Aggressive' ? '#d29922' : '#8b949e');
          const bg = x.exit ? '#12261a' : (x.ph === 'Aggressive' ? '#2a220d' : 'transparent');
          return '<tr style="background:' + bg + ';border-top:1px solid #21262d">'
            + '<td style="padding:5px 10px;color:#e6edf3">' + (x.p > 0 ? '+' : '') + _tslINR(x.p) + '</td>'
            + '<td style="padding:5px 10px;color:' + phCol + '">' + x.ph + '</td>'
            + '<td style="padding:5px 10px;text-align:right;color:#6e7681">' + (x.mv ? '+' + _tslINR(x.mv) : '—') + '</td>'
            + '<td style="padding:5px 10px;text-align:right;color:' + slCol + ';font-weight:600">' + _tslINR(x.sl) + '</td></tr>';
        }).join('') + '</tbody></table>';
    }
    function tslHover(ev) {
      if (!_tslGeo || !_tslPts) return;
      const svg = ev.target.ownerSVGElement, rect = svg.getBoundingClientRect();
      const sx = (ev.clientX - rect.left) / rect.width * 680;
      const g = _tslGeo, p = Math.max(0, Math.min(g.tgt, (sx - g.L) / g.PW * g.tgt));
      let best = _tslPts[0]; for (const o of _tslPts) if (Math.abs(o.p - p) < Math.abs(best.p - p)) best = o;
      const X = pp => g.L + (pp / g.tgt) * g.PW, Y = v => g.B - ((v - g.yMin) / g.span) * g.PH;
      const dot = document.getElementById('tsl-hover-dot');
      if (dot) { dot.setAttribute('cx', X(best.p).toFixed(1)); dot.setAttribute('cy', Y(best.sl).toFixed(1)); dot.style.display = 'block'; }
      const tip = document.getElementById('tsl-tooltip');
      const cushGap = best.p - best.sl;
      tip.innerHTML = 'Profit <b style="color:#e6edf3">' + (best.p > 0 ? '+' : '') + _tslINR(best.p) + '</b> · '
        + '<span style="color:' + (best.ph === 'Aggressive' ? '#d29922' : (best.exit ? '#3fb950' : '#8b949e')) + '">' + best.ph + '</span><br>'
        + 'SL <b style="color:' + (best.sl >= 0 ? '#3fb950' : '#f85149') + '">' + _tslINR(best.sl) + '</b>'
        + '<span style="color:#6e7681"> · give-back ' + _tslINR(cushGap) + '</span>';
      tip.style.left = (X(best.p) / 680 * rect.width) + 'px';
      tip.style.top = (Y(best.sl) / 230 * rect.height) + 'px';
      tip.style.display = 'block';
    }
    function tslHoverOut() {
      const dot = document.getElementById('tsl-hover-dot'); if (dot) dot.style.display = 'none';
      const tip = document.getElementById('tsl-tooltip'); if (tip) tip.style.display = 'none';
    }
    function tslShowTab(which) {
      const g = document.getElementById('tsl-preview-graph'), t = document.getElementById('tsl-preview-table');
      const gb = document.getElementById('tsl-tab-graph'), tb = document.getElementById('tsl-tab-table');
      const on = 'background:#1f6feb20;border:1px solid #1f6feb;border-radius:6px;color:#58a6ff';
      const off = 'background:#21262d;border:1px solid #30363d;border-radius:6px;color:#8b949e';
      if (which === 'table') {
        g.style.display = 'none'; t.style.display = 'block';
        tb.style.cssText = 'padding:4px 12px;font-size:12px;font-weight:600;cursor:pointer;' + on;
        gb.style.cssText = 'padding:4px 12px;font-size:12px;font-weight:600;cursor:pointer;' + off;
      } else {
        g.style.display = 'block'; t.style.display = 'none';
        gb.style.cssText = 'padding:4px 12px;font-size:12px;font-weight:600;cursor:pointer;' + on;
        tb.style.cssText = 'padding:4px 12px;font-size:12px;font-weight:600;cursor:pointer;' + off;
      }
    }

    // ── KILL-FLOOR live status (RMS tab big display) ──
    async function killFloorStatusPoll() {
      const box = document.getElementById('killfloor-live');
      if (!box) return;
      try {
        const r = await fetch('/api/kill-floor-status');
        const d = await r.json();
        if (!d.config || !d.config.enabled) { box.style.display = 'none'; return; }
        box.style.display = 'block';
        const fEl = document.getElementById('kf-floor');
        const pEl = document.getElementById('kf-peak');
        const sEl = document.getElementById('kf-status');
        fEl.textContent = d.floor != null ? ('₹' + Math.round(d.floor).toLocaleString('en-IN')) : '— (not armed)';
        pEl.textContent = '₹' + Math.round(d.peak || 0).toLocaleString('en-IN');
        if (d.fired) {
          sEl.textContent = '🔒 FIRED — profit locked, entries blocked today';
          sEl.style.color = '#f85149';
          fEl.style.color = '#f85149';
        } else if (d.breaching) {
          sEl.textContent = '⏳ MTM floor ke neeche — confirm timer chal raha hai';
          sEl.style.color = '#d29922';
          fEl.style.color = '#d29922';
        } else if (d.armed) {
          sEl.textContent = '🟢 ARMED — floor trail ho raha hai';
          sEl.style.color = '#3fb950';
          fEl.style.color = '#3fb950';
        } else {
          sEl.textContent = '⚪ Waiting — MTM ne abhi Arm ₹ cross nahi kiya';
          sEl.style.color = '#8b949e';
          fEl.style.color = '#8b949e';
        }
      } catch (e) { /* dashboard reachable nahi — box jaisa hai waisa chhodo */ }
    }
    setInterval(() => { if (activeTab === 'risk') killFloorStatusPoll(); }, 5000);

    // ── Per-Instrument Trailing Lock live status (RMS tab) ──
    async function perInstrumentLockStatusPoll() {
      const box = document.getElementById('per-instrument-live');
      const rows = document.getElementById('pi-lock-rows');
      if (!box || !rows) return;
      try {
        const r = await fetch('/api/per-instrument-lock-status');
        const d = await r.json();
        if (!d.config || !d.config.enabled) { box.style.display = 'none'; return; }
        box.style.display = 'block';
        const positions = d.positions || [];
        if (!positions.length) {
          rows.innerHTML = 'No open position tracked yet.';
          return;
        }
        rows.innerHTML = positions.map(p => {
          let statusTxt, color;
          if (p.fired) { statusTxt = '🔒 FIRED'; color = '#f85149'; }
          else if (p.breaching) { statusTxt = '⏳ confirm timer chal raha'; color = '#d29922'; }
          else if (p.armed) { statusTxt = '🟢 ARMED'; color = '#3fb950'; }
          else { statusTxt = '⚪ waiting for arm'; color = '#8b949e'; }
          const floorTxt = p.floor != null ? ('₹' + Math.round(p.floor).toLocaleString('en-IN')) : '—';
          return `<div style="display:flex;gap:18px;align-items:baseline;padding:4px 0;border-bottom:1px solid #21262d">
        <span style="font-weight:600;color:#e6edf3;min-width:110px">${p.sym}</span>
        <span style="color:#8b949e">peak ₹${Math.round(p.peak || 0).toLocaleString('en-IN')}</span>
        <span style="color:#8b949e">floor ${floorTxt}</span>
        <span style="color:${color};font-weight:600">${statusTxt}</span>
      </div>`;
        }).join('');
      } catch (e) { /* dashboard reachable nahi — box jaisa hai waisa chhodo */ }
    }
    setInterval(() => { if (activeTab === 'risk') perInstrumentLockStatusPoll(); }, 5000);

