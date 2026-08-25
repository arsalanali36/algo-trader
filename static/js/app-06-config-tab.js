// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    const DEFAULT_CONFIGS = {
      ema: {
        symbols: ["NIFTY"], instrument: "equity", strike_offset: "0", timeframe: "1m", qty: 1, active: true
      },
      rsi: {
        symbols: ["NIFTY"], instrument: "equity", strike_offset: "0", timeframe: "5m",
        period: 14, oversold: 30, overbought: 70, qty: 1, max_trades_per_symbol: 2, active: true
      },
      range: {
        symbols: ["NIFTY"], instrument: "equity", strike_offset: "0", timeframe: "1m",
        max_candle_size: 25, qty: 1, max_trades_per_symbol: 2,
        hawa_me_zone: false, fresh_zone_only: true, zone_exit: false, atr_exit: true, fib_exit: false, active: true
      }
    };

    const STRAT_FIELDS = {
      bb: [
        { id: 'symbols', label: 'Symbols', type: 'symbols' },
        { id: 'instrument', label: 'Instrument', type: 'select', opts: ['equity', 'options'] },
        { id: 'timeframe', label: 'Timeframe', type: 'select', opts: ['1m', '3m', '5m', '15m', '30m', '1D'] },
        { id: 'bb_window', label: 'BB Window', type: 'number' },
        { id: 'bb_std', label: 'BB Std Dev', type: 'number' },
        { id: 'allow_short', label: 'Allow Short', type: 'select', opts: ['true', 'false'] },
        { id: 'qty', label: 'Quantity', type: 'number' },
        { id: 'active', label: 'Active', type: 'select', opts: ['true', 'false'] }
      ],
      ema: [
        { id: 'symbols', label: 'Symbols', type: 'symbols' },
        { id: 'instrument', label: 'Instrument', type: 'select', opts: ['equity', 'options'] },
        { id: 'strike_offset', label: 'Strike Offset', type: 'select', opts: ['-3', '-2', '-1', '0', '1', '2', '3'] },
        { id: 'timeframe', label: 'Timeframe', type: 'select', opts: ['1m', '3m', '5m', '15m', '30m'] },
        { id: 'qty', label: 'Quantity', type: 'number' },
        { id: 'active', label: 'Active', type: 'select', opts: ['true', 'false'] }
      ],
      rsi: [
        { id: 'symbols', label: 'Symbols', type: 'symbols' },
        { id: 'instrument', label: 'Instrument', type: 'select', opts: ['equity', 'options'] },
        { id: 'strike_offset', label: 'Strike Offset', type: 'select', opts: ['-3', '-2', '-1', '0', '1', '2', '3'] },
        { id: 'timeframe', label: 'Timeframe', type: 'select', opts: ['1m', '3m', '5m', '15m', '30m'] },
        { id: 'period', label: 'RSI Period', type: 'number' },
        { id: 'oversold', label: 'Oversold', type: 'number' },
        { id: 'overbought', label: 'Overbought', type: 'number' },
        { id: 'qty', label: 'Quantity', type: 'number' },
        { id: 'max_trades_per_symbol', label: 'Max Trades/Symbol', type: 'number' },
        { id: 'active', label: 'Active', type: 'select', opts: ['true', 'false'] }
      ],
      range: [
        { id: 'symbols', label: 'Symbols', type: 'symbols' },
        { id: 'instrument', label: 'Instrument', type: 'select', opts: ['equity', 'options'] },
        { id: 'strike_offset', label: 'Strike Offset', type: 'select', opts: ['-3', '-2', '-1', '0', '1', '2', '3'] },
        { id: 'timeframe', label: 'Timeframe', type: 'select', opts: ['1m', '3m', '5m', '15m', '30m'] },
        { id: 'max_candle_size', label: 'Max Candle Size (pts)', type: 'number' },
        { id: 'qty', label: 'Quantity', type: 'number' },
        { id: 'max_trades_per_symbol', label: 'Max Trades/Symbol', type: 'number' },
        { id: 'hawa_me_zone', label: 'Hawa Me Zone', type: 'checkbox' },
        { id: 'fresh_zone_only', label: 'Use Fresh Zone Only', type: 'checkbox' },
        { id: 'zone_exit', label: 'Zone Exit', type: 'checkbox' },
        { id: 'atr_exit', label: 'ATR Exit', type: 'checkbox' },
        { id: 'fib_exit', label: 'Fib Exit', type: 'checkbox' },
        { id: 'active', label: 'Active', type: 'select', opts: ['true', 'false'] }
      ]
    };

    function renderConfigTable() {
      const container = document.getElementById('cfg-content');
      const type = activeCfgTab;
      const fields = STRAT_FIELDS[type];

      // Find variants of this type
      let variants = Object.keys(GLOBAL_CONFIG).filter(k => k.startsWith(type + '_'));
      if (variants.length === 0) {
        container.innerHTML = `<div style="color:#8b949e;padding:24px;text-align:center">No ${type.toUpperCase()} variation yet. Click "+ Add" to create one.</div><div style="padding:8px"><button class="btn btn-gray" onclick="addVariant('${type}')">+ Add ${type.toUpperCase()} Variation</button></div>`;
        return;
      }

      let html = `<div class="cfg-table-container"><table class="cfg-table">`;

      // Headers
      html += `<tr><th>Attributes</th>`;
      variants.forEach(v => {
        html += `<th>${v.toUpperCase()}</th>`;
      });
      html += `<th><button class="btn btn-gray" onclick="addVariant('${type}')">+ Add</button></th></tr>`;

      // Rows for each field
      fields.forEach(f => {
        html += `<tr><td>${f.label}</td>`;
        variants.forEach(v => {
          const val = GLOBAL_CONFIG[v][f.id] ?? '';

          if (f.type === 'symbols') {
            let symStr = (Array.isArray(val) ? val : []).join(', ') || 'None';
            html += `<td>
          <button class="btn btn-gray" style="font-size:11px;padding:3px 6px;margin-bottom:4px" onclick="openSymbolModal('${v}')">✏️ Select</button>
          <div style="font-size:11px;color:#8b949e;word-wrap:break-word;">${symStr}</div>
        </td>`;
          }
          else if (f.type === 'select') {
            html += `<td><select id="cfg-${v}-${f.id}">`;
            f.opts.forEach(opt => {
              let selected = String(val) === String(opt) ? 'selected' : '';
              html += `<option value="${opt}" ${selected}>${opt}</option>`;
            });
            html += `</select></td>`;
          }
          else if (f.type === 'number') {
            html += `<td><input type="number" id="cfg-${v}-${f.id}" value="${val}"></td>`;
          }
          else if (f.type === 'checkbox') {
            let chk = val ? 'checked' : '';
            html += `<td><input type="checkbox" id="cfg-${v}-${f.id}" ${chk}></td>`;
          }
        });
        html += `<td></td></tr>`;
      });

      // Action Row
      html += `<tr><td>Actions</td>`;
      variants.forEach(v => {
        html += `<td>
      <div style="display:flex;flex-direction:column;gap:6px">
        <button class="btn btn-blue" style="padding:5px;" onclick="saveVariant('${v}')">💾 Save</button>
        <button class="btn btn-red" style="padding:5px;" onclick="deleteVariant('${v}')">🗑 Delete</button>
      </div>
    </td>`;
      });
      html += `<td></td></tr>`;

      html += `</table></div>`;
      container.innerHTML = html;
    }

    function addVariant(type) {
      let keys = Object.keys(GLOBAL_CONFIG).filter(k => k.startsWith(type + '_v'));
      let vNums = keys.map(k => parseInt(k.replace(type + '_v', '')) || 0);
      let nextV = (vNums.length ? Math.max(...vNums) : 0) + 1;

      let newCfg = JSON.parse(JSON.stringify(DEFAULT_CONFIGS[type]));
      if (keys.length > 0) {
        let baseKey = `${type}_v1`;
        if (!GLOBAL_CONFIG[baseKey]) baseKey = keys[0];
        newCfg = JSON.parse(JSON.stringify(GLOBAL_CONFIG[baseKey]));
      }

      GLOBAL_CONFIG[`${type}_v${nextV}`] = newCfg;
      renderConfigTable();
    }

    function deleteVariant(vKey) {
      if (confirm(`Are you sure you want to delete ${vKey.toUpperCase()}?`)) {
        delete GLOBAL_CONFIG[vKey];
        fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(GLOBAL_CONFIG)
        }).then(() => {
          flash(`${vKey} deleted!`);
          loadAll();
        });
      }
    }

    function saveVariant(vKey) {
      const type = vKey.split('_')[0];
      const fields = STRAT_FIELDS[type];

      fields.forEach(f => {
        if (f.type !== 'symbols') {
          let el = document.getElementById(`cfg-${vKey}-${f.id}`);
          if (el) {
            let val = el.value;
            if (f.type === 'number') val = Number(val);
            else if (f.type === 'checkbox') val = el.checked;
            if (val === 'true') val = true;
            if (val === 'false') val = false;
            GLOBAL_CONFIG[vKey][f.id] = val;
          }
        }
      });

      fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(GLOBAL_CONFIG)
      }).then(async (r) => {
        let res = await r.json();
        flash(res.msg);
        loadAll();
      });
    }

    // ── SYMBOL MODAL ──
    function openSymbolModal(vKey) {
      currentModalTarget = vKey;
      document.getElementById('modal-title').innerText = `Symbols for ${vKey.toUpperCase()}`;

      let currentSyms = GLOBAL_CONFIG[vKey].symbols || [];
      let html = '';
      NIFTY50.forEach(s => {
        let chk = currentSyms.includes(s) ? 'checked' : '';
        html += `<label class="symbol-cb"><input type="checkbox" value="${s}" class="mod-cb" ${chk}> ${s}</label>`;
      });
      document.getElementById('modal-cbs').innerHTML = html;
      document.getElementById('symbol-modal').style.display = 'flex';
    }
    function closeModal() {
      document.getElementById('symbol-modal').style.display = 'none';
      currentModalTarget = null;
    }
    function selectAllSymbols(state) {
      document.querySelectorAll('.mod-cb').forEach(cb => cb.checked = state);
    }
    function confirmSymbols() {
      if (!currentModalTarget) return;
      let selected = [];
      document.querySelectorAll('.mod-cb').forEach(cb => {
        if (cb.checked) selected.push(cb.value);
      });
      GLOBAL_CONFIG[currentModalTarget].symbols = selected;
      closeModal();
      renderConfigTable();
    }

    // ── BACKEND ACTIONS ──
    async function startBot(s, mode) {
      let r = await fetch(`/api/start?s=${s}&mode=${mode}`, { method: 'POST' });
      let j = await r.json();
      flash(j.msg, j.msg.includes('✅') ? '#3fb950' : '#d29922');
      checkStatus().then(renderControlTab);
    }
    async function stopBot(s) {
      let r = await fetch(`/api/stop?s=${s}`, { method: 'POST' });
      let j = await r.json();
      flash(j.msg, '#f85149');
      checkStatus().then(renderControlTab);
    }

    // ── PNL & LOG UPDATE LOOPS ──
    function _card(title, color, content) {
      return `<div style="background:#161b22;border:1px solid ${color}40;border-radius:8px;overflow:hidden;margin-bottom:16px">
    <div style="background:${color}15;padding:8px 14px;font-size:12px;font-weight:700;color:${color};border-bottom:1px solid ${color}30">${title}</div>
    <div>${content}</div>
  </div>`;
    }
    const _pnlTH = (t, a) => `<th style="padding:7px 12px;color:#8b949e;font-size:11px;font-weight:600;border-bottom:1px solid #30363d;text-align:${a || 'left'};white-space:nowrap">${t}</th>`;
    const _pnlTD = (t, c, a, x) => `<td style="padding:7px 12px;font-size:12px;color:${c || '#e6edf3'};text-align:${a || 'left'};${x || ''}">${t}</td>`;
    function _pnlTable(cols, rows) {
      let h = `<table style="width:100%;border-collapse:collapse;table-layout:fixed"><thead><tr>${cols.map(c => _pnlTH(c.l, c.a)).join('')}</tr></thead><tbody>`;
      rows.forEach(r => { h += `<tr style="border-bottom:1px solid #21262d">${r}</tr>`; });
      return h + '</tbody></table>';
    }
    function _pnlColor(v) { return v == null ? '#8b949e' : v >= 0 ? '#3fb950' : '#f85149'; }
    function _fmt(v) { if (v == null || isNaN(v)) return '—'; return (v >= 0 ? '+' : '') + Math.abs(v).toFixed(2); }
    // Only update DOM if content actually changed — avoids blank flash on every poll
    function _setHtml(el, html) { if (el && el.innerHTML !== html) el.innerHTML = html; }

    // ── ZERODHA OPTIONS CHARGES ──────────────────────────────────────────────────
    // Ref: zerodha.com/charges (Options intraday/positional)
    //
    // ⚠️ DATE-BLIND, and knowingly so. The single source of truth is
    // scratch/nifty_trend/charges.py, which looks STT/txn up by the trade's ENTRY
    // date (options STT: 0.0625% → 0.10% on 2024-10-01 → 0.15% on 2026-04-01).
    // These literals are the CURRENT regime only. The Python mirror
    // (trader_dashboard._zerodha_charges) was pointed at charges.py on 2026-07-16;
    // this JS copy still hardcodes, because the regime table would have to be
    // ported client-side or the tax served from the backend.
    // Consequence: correct for anything entered on/after 2026-04-01 (i.e. every
    // trade in the DB today), OVERSTATED for anything older. If you ever render
    // pre-April-2026 trades here, fix this first — a dashboard-vs-EOD-report tax
    // mismatch is exactly what TRAP #118 cost a session to chase down.
    function calcCharges(entryPx, exitPx, qty, entrySide, sym) {
      if (!entryPx || !exitPx || !qty) return null;
      // Crypto (Delta) — NOT Zerodha STT/brokerage. Delta taker commission ≈ 0.03%
      // of NOTIONAL per side (observed effective rate ~0.00027 on notional), in INR.
      // Prices here are INR-per-lot; notional comes from the strike in the symbol.
      if (sym && /-(BTC|ETH)-/.test(sym) && /^[CP]-/.test(sym)) {
        const parts = String(sym).split('-');
        const K = parseFloat(parts[2]) || 0;
        const cv = sym.indexOf('-BTC-') >= 0 ? 0.001 : 0.01;
        const usdinr = 85, rate = 0.0003;
        return rate * (K * cv) * usdinr * qty * 2;   // entry + exit legs
      }
      const buySide = entrySide === 'BUY' ? entryPx : exitPx;
      const sellSide = entrySide === 'SELL' ? entryPx : exitPx;
      const buyTurn = buySide * qty;
      const sellTurn = sellSide * qty;
      const totalTurn = buyTurn + sellTurn;
      const brokerage = 40;                       // 20 × 2 orders
      // Budget-2026 rates (effective 2026-04-01, verified zerodha.com/charges 2026-07-14).
      // Dashboard costs TODAY's trades only → current regime. Backtest-side date-aware
      // regime table = scratch/nifty_trend/charges.py (keep the two in sync).
      const stt = 0.0015 * sellTurn;        // 0.15% on sell premium (was 0.0625% pre-Oct-24, 0.10% to Mar-26)
      const exchCharges = 0.0003553 * totalTurn;   // 0.03553% both legs (was 0.053%)
      const sebi = 0.0000001 * totalTurn;     // 10/crore
      const stampDuty = 0.00003 * buyTurn;       // 0.003% on buy side
      const gst = 0.18 * (brokerage + exchCharges + sebi);
      return brokerage + stt + exchCharges + sebi + stampDuty + gst;
    }

    // Friendly exit-reason badge (item F) — turns the raw exit-leg tag stored by
    // order_store (_exit_reason) into a short coloured label. '' / unknown → dash.
    // Full list of raw reasons + what each one means: 🚨 Risk tab → "Reasons For
    // Exit" card (2026-07-02). Keep this switch and that card's HTML in sync —
    // grep-audited against every real extra_tags/tag/reason= call site in
    // trader_dashboard.py / webhook_executor.py / broker_sync.py, matches
    // order_store.py's server-side _EXIT_REASON_PREFIXES 1:1.
    // Pull the ₹ / % / pt amount out of an exit-reason tag so the badge can show
    // "kitna SL tha" (#6, 2026-07-07). Tags carry it as a suffix:
    //   DEFAULT_TSL_SL:-2000  DEFAULT_TSL_TARGET:4000  SL_HIT:rs:2000
    //   SL_HIT:pct:5  SL_HIT:pt:15  SL_HIT:premium:120  SL_HIT:index:24000
    // Returns '' if nothing parseable.
    function _exitReasonAmt(r) {
      let m = r.match(/^DEFAULT_TSL_(SL|TARGET):(-?\d+(?:\.\d+)?)/);
      if (m) {
        const v = parseFloat(m[2]);
        if (m[1] === 'SL') return v < 0 ? '₹' + Math.abs(v).toLocaleString('en-IN')
          : '+₹' + v.toLocaleString('en-IN') + ' locked';
        return '₹' + Math.abs(v).toLocaleString('en-IN');
      }
      m = r.match(/^(?:SL|TP)_HIT:([a-z_]+):(-?\d+(?:\.\d+)?)/i);
      if (m) {
        const type = m[1].toLowerCase(), v = parseFloat(m[2]);
        if (type === 'rs') return '₹' + v.toLocaleString('en-IN');
        if (type === 'pct') return v + '%';
        if (type === 'pt') return v + ' pt';
        if (type === 'trailing_pt') return 'trail ' + v + ' pt';
        if (type === 'premium') return '@₹' + v.toLocaleString('en-IN');
        if (type === 'index') return 'idx ' + v.toLocaleString('en-IN');
        return String(v);
      }
      // Legacy "SL_HIT:5%/₹2000"-style tags — grab first %/₹ number.
      if (/SL_HIT|TP_HIT/.test(r)) {
        m = r.match(/(\d+(?:\.\d+)?)\s*%/); if (m) return m[1] + '%';
        m = r.match(/₹\s*(\d+(?:\.\d+)?)/); if (m) return '₹' + parseFloat(m[1]).toLocaleString('en-IN');
      }
      // RMS daily target / max-loss carry the ₹ in free text, e.g.
      //   "🎯 Daily profit target ₹3,000 hit for 'range_v1' (today's P&L ₹3,045)"
      // Show BOTH: the target/limit that was SET + how much actually locked, so a
      // setting you changed is self-documenting on the row (no need to remember it).
      if (/^RMS_(PROFIT_TARGET|MAXLOSS)/.test(r)) {
        const nums = r.match(/₹\s*[\d,]+(?:\.\d+)?/g) || [];
        const clean = s => s.replace(/\s+/g, '');
        if (nums.length >= 2) return clean(nums[0]) + ' · P&L ' + clean(nums[1]);
        if (nums.length === 1) return clean(nums[0]);
      }
      return '';
    }

    function _exitReasonBadge(raw) {
      if (!raw) return '<span style="color:#6e7681">—</span>';
      const r = String(raw);
      let label = r, color = '#8b949e';
      // Per-position triggers (pos_monitor_loop)
      if (r.startsWith('SL_HIT')) { label = '🛑 Stop-Loss'; color = '#f85149'; }
      else if (r.startsWith('TP_HIT')) { label = '🎯 Target'; color = '#3fb950'; }
      // Account/EOD/expiry-wide triggers (pos_monitor_loop)
      else if (r.startsWith('EXPIRY_ITM_SQUAREOFF')) { label = '📅 Expiry ITM'; color = '#f85149'; }
      else if (r.startsWith('EXPIRY_EOD_SQUAREOFF')) { label = '📅 Expiry EOD (2:55)'; color = '#d29922'; }
      else if (r.startsWith('EOD_315_SQUAREOFF')) { label = '⏰ 3:15 EOD'; color = '#d29922'; }
      else if (r.startsWith('KILL_FLOOR')) { label = '🔒 Kill-Floor'; color = '#f85149'; }
      else if (r.startsWith('TRAILING_PROFIT_LOCK')) { label = '🔒 Trailing Lock'; color = '#f0883e'; }
      else if (r.startsWith('DEFAULT_TSL_TARGET')) { label = '🎯 Aggr-Trail Target'; color = '#3fb950'; }
      else if (r.startsWith('DEFAULT_TSL_SL')) { label = '🛡️ Aggr-Trail SL'; color = '#f0883e'; }
      else if (r.startsWith('GROUP_TARGET')) { label = '🎯 Group Target'; color = '#3fb950'; }
      else if (r.startsWith('GROUP_SL')) { label = '🛑 Group SL'; color = '#f85149'; }
      else if (r.startsWith('RMS_MAXLOSS')) { label = '⚠️ RMS Daily Max-Loss'; color = '#f85149'; }
      else if (r.startsWith('RMS_PROFIT_TARGET')) { label = '✅ RMS Daily Target'; color = '#3fb950'; }
      else if (r.startsWith('NO_PRICE_EMERGENCY_EXIT')) { label = '🚨 No-Price Emergency'; color = '#f85149'; }
      // Strategy's own exit logic (not pos_monitor_loop-driven)
      else if (r.startsWith('ATR_TRAILING')) { label = '📉 ATR Trailing'; color = '#f0883e'; }
      else if (r.startsWith('RSI_MIDLINE_EXIT')) { label = '↩️ RSI Midline'; color = '#58a6ff'; }
      else if (r.startsWith('ORB_OVN_NEXTDAY')) { label = '🌅 Next-Day Exit (9:20)'; color = '#58a6ff'; }
      // Auto-Rolling ATM Straddle (02.09) — buy-back on roll / unwind on abort
      else if (r.startsWith('ROLLER_ROLL_EXIT')) { label = '🔄 Rolled to new ATM'; color = '#58a6ff'; }
      else if (r.startsWith('ROLLER_ABORT')) { label = '↩️ Roll Aborted (unwound)'; color = '#8b949e'; }
      // Option mission strategies' own TP/SL/rollback exits (task 71 — Straddle,
      // Debit Vertical, Ratio Backspread, Short-Vol Iron-Fly, VRP)
      // NOTE: keep this family list in sync with order_store._EXIT_REASON_PREFIXES —
      // a reason not listed THERE never reaches this function at all (it's dropped
      // as unrecognised and the column shows blank, badge or no badge). That split
      // is exactly why every mission exit read blank until 2026-07-16: these badges
      // already existed, the backend prefixes didn't.
      else if (/^(STRADDLE|STRANGLE|DVERT|BSPRD|SVOL|VRPC|VRP|ORBST|ORB|BNF|CHAIN)_TRAIL/.test(r)) { label = '📉 Profit Trail Lock'; color = '#f0883e'; }
      else if (/^(STRADDLE|STRANGLE|DVERT|BSPRD|SVOL|VRPC|VRP|ORBST|ORB|BNF|CHAIN)_(TP|TARGET)/.test(r)) { label = '🎯 Strategy Target'; color = '#3fb950'; }
      else if (/^(STRADDLE|STRANGLE|DVERT|BSPRD|SVOL|VRPC|VRP|ORBST|ORB|BNF|CHAIN)_SL/.test(r)) { label = '🛡️ Strategy SL'; color = '#f0883e'; }
      else if (/^(STRADDLE|STRANGLE|DVERT|BSPRD|SVOL|VRPC|VRP|ORBST|ORB|BNF|CHAIN)_ROLLBACK/.test(r)) { label = '↩️ Leg Rollback'; color = '#8b949e'; }
      // Webhook (TradingView-signal-driven strategies)
      else if (r.startsWith('IDX_TRAIL')) { label = '📉 Index Trail SL'; color = '#f0883e'; }
      else if (r.startsWith('TRAIL_SL')) { label = '📉 Trail SL'; color = '#f0883e'; }
      else if (r.startsWith('TARGET')) { label = '🎯 Target'; color = '#3fb950'; }
      else if (r.startsWith('GLOBAL_CAP')) { label = '🚫 Max Trades/Day'; color = '#8b949e'; }
      else if (r.startsWith('SQUAREOFF_315')) { label = '⏰ 3:15 EOD (Webhook)'; color = '#d29922'; }
      else if (r.startsWith('REVERSAL')) { label = '🔄 Reversal'; color = '#58a6ff'; }
      else if (r.startsWith('TV_EXIT')) { label = '📡 TV Exit Signal'; color = '#58a6ff'; }
      // Manual / broker-detected
      else if (r.startsWith('MANUAL_CLOSE')) { label = '✋ Manual Close'; color = '#8b949e'; }
      else if (r.startsWith('EXTERNALLY_CLOSED') || r.startsWith('MANUAL_EXIT_BROKER')) { label = '🌐 Closed at Broker'; color = '#8b949e'; }

      // Append the SL/TP threshold amount (#6) — e.g. "🛡️ Default SL ₹2,000".
      const amt = _exitReasonAmt(r);
      if (amt) label += ' ' + amt;

      const isGroup = /_GROUP$/.test(r);
      if (isGroup) label += ' (hedge pair)';
      return `<span style="color:${color};font-size:11px;white-space:nowrap" title="${r.replace(/"/g, '&quot;')}">${label}</span>`;
    }

