// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // ── QUICK ORDER FLOATING PANEL ───────────────────────────────────────────────
    (function () {
      const LOT_SIZES = { NIFTY: 65, BANKNIFTY: 30 };  // fallback; overwritten by API
      let qoSym = 'NIFTY', qoAtm = 0, qoMode = 'paper', qoBroker = 'dhan';

      // fetch real lot sizes from scrip master
      fetch('/api/lot-sizes').then(r => r.json()).then(d => {
        LOT_SIZES.NIFTY = d.NIFTY || 65;
        LOT_SIZES.BANKNIFTY = d.BANKNIFTY || 30;
        updateQtyHint();
      }).catch(() => { });

      const panel = document.createElement('div');
      panel.id = 'qo-panel';
      panel.innerHTML = `
<div id="qo-header" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;cursor:grab">
  <span style="color:#58a6ff;font-size:12px;font-weight:bold;letter-spacing:1px">QUICK ORDER</span>
  <span id="qo-close" style="color:#8b949e;font-size:16px;cursor:pointer;line-height:1">&#x2715;</span>
</div>
<div style="display:flex;background:#0d1117;border-radius:6px;padding:2px;margin-bottom:8px;gap:2px">
  <button id="qo-paper" onclick="qoSetMode('paper')" style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:11px;font-weight:bold;cursor:pointer;background:#d29922;color:#0d1117">PAPER</button>
  <button id="qo-live"  onclick="qoSetMode('live')"  style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:11px;font-weight:bold;cursor:pointer;background:transparent;color:#8b949e">LIVE</button>
</div>
<div style="display:flex;background:#0d1117;border-radius:6px;padding:2px;margin-bottom:12px;gap:2px">
  <button id="qo-broker-dhan" onclick="qoSetBroker('dhan')" style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:11px;font-weight:bold;cursor:pointer;background:#1f6feb;color:#fff">DHAN</button>
  <button id="qo-broker-kite" onclick="qoSetBroker('kite')" style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:11px;font-weight:bold;cursor:pointer;background:transparent;color:#8b949e">ZERODHA</button>
</div>
<div style="font-size:10px;color:#8b949e;margin-bottom:4px">Symbol</div>
<div style="display:flex;gap:6px;margin-bottom:10px">
  <button id="qo-sym-NIFTY"     onclick="qoSetSym('NIFTY')"     style="flex:1;padding:6px 0;border:1px solid #1f6feb;border-radius:5px;background:#1f6feb22;color:#58a6ff;font-size:12px;font-weight:bold;cursor:pointer">NIFTY</button>
  <button id="qo-sym-BANKNIFTY" onclick="qoSetSym('BANKNIFTY')" style="flex:1;padding:6px 0;border:1px solid #30363d;border-radius:5px;background:#21262d;color:#8b949e;font-size:12px;font-weight:bold;cursor:pointer">BANKNIFTY</button>
</div>
<div style="font-size:10px;color:#8b949e;margin-bottom:4px">Strike offset (ATM)</div>
<div id="qo-atm-row" style="display:flex;gap:3px;margin-bottom:10px">
  ${[-3, -2, -1, 0, 1, 2, 3].map(v => `<button onclick="qoSetAtm(${v})" data-atm="${v}" style="flex:1;padding:4px 0;border:1px solid ${v === 0 ? '#1f6feb' : '#30363d'};border-radius:4px;background:${v === 0 ? '#1f6feb' : '#21262d'};color:${v === 0 ? '#fff' : '#8b949e'};font-size:10px;cursor:pointer">${v === 0 ? 'ATM' : v > 0 ? '+' + v : v}</button>`).join('')}
</div>
<div style="font-size:10px;color:#8b949e;margin-bottom:4px">Option — select karo, phir BUY/SELL</div>
<div id="qo-ltp-box" style="background:#0d1117;border:1px solid #21262d;border-radius:7px;padding:8px 10px;margin-bottom:10px">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
    <div id="qo-opt-ce" onclick="qoSetOpt('CE')" title="Call select karo" style="background:#3fb95015;border:1px solid #3fb95040;border-radius:5px;padding:5px 7px;cursor:pointer">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
        <span style="font-size:9px;color:#3fb950;font-weight:700">CE · Call</span>
        <span id="qo-tick-ce" style="font-size:11px;color:#3fb950;visibility:hidden">✓</span>
      </div>
      <div id="qo-ce-ltp" style="font-size:13px;color:#e6edf3;font-weight:700">⏳</div>
      <div id="qo-ce-sym" style="font-size:12px;color:#adbac7;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">—</div>
    </div>
    <div id="qo-opt-pe" onclick="qoSetOpt('PE')" title="Put select karo" style="background:#f8514915;border:1px solid #f8514940;border-radius:5px;padding:5px 7px;cursor:pointer">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
        <span style="font-size:9px;color:#f85149;font-weight:700">PE · Put</span>
        <span id="qo-tick-pe" style="font-size:11px;color:#f85149;visibility:hidden">✓</span>
      </div>
      <div id="qo-pe-ltp" style="font-size:13px;color:#e6edf3;font-weight:700">⏳</div>
      <div id="qo-pe-sym" style="font-size:12px;color:#adbac7;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">—</div>
    </div>
  </div>
  <div id="qo-ts" style="font-size:9px;color:#484f58;text-align:right;margin-top:4px">fetching...</div>
</div>
<div style="font-size:10px;color:#8b949e;margin-bottom:4px">Limit Price <span style="color:#484f58">(blank = LTP par bhejo)</span></div>
<div style="display:flex;align-items:center;gap:6px;margin-bottom:12px">
  <input id="qo-price" type="number" step="0.05" placeholder="auto (LTP)" style="flex:1;background:#0d1117;border:1px solid #30363d;border-radius:5px;color:#e6edf3;padding:6px 8px;font-size:13px;outline:none">
</div>
<div style="font-size:10px;color:#8b949e;margin-bottom:4px">Lots</div>
<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
  <input id="qo-lots" type="number" value="3" min="1" oninput="updateQtyHint()" style="width:54px;background:#0d1117;border:1px solid #30363d;border-radius:5px;color:#e6edf3;padding:5px 8px;font-size:13px;text-align:center;outline:none">
  <span style="font-size:11px;color:#8b949e">= <span id="qo-qty-num" style="color:#e6edf3;font-weight:bold">65</span> qty <span style="color:#444;font-size:10px">(1L=<span id="qo-ls">65</span>)</span></span>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:6px">
  <button onclick="qoOrder('BUY')"  style="padding:11px;background:#3fb950;border:none;border-radius:6px;color:#fff;font-size:14px;font-weight:bold;cursor:pointer">BUY <span id="qo-buy-leg" style="font-size:11px;opacity:.85">CE</span></button>
  <button onclick="qoOrder('SELL')" style="padding:11px;background:#f85149;border:none;border-radius:6px;color:#fff;font-size:14px;font-weight:bold;cursor:pointer">SELL <span id="qo-sell-leg" style="font-size:11px;opacity:.85">CE</span></button>
</div>
<div style="font-size:9px;color:#6e7681;text-align:center;margin-bottom:8px">jo <b id="qo-leg-hint" style="color:#adbac7">CE</b> select hai usi pe BUY/SELL chalega</div>
<div id="qo-status" style="font-size:11px;color:#8b949e;text-align:center;min-height:16px">Mode: PAPER</div>`;

      Object.assign(panel.style, {
        position: 'fixed', bottom: '24px', right: '24px', width: '268px',
        background: '#161b22', border: '1px solid #30363d', borderRadius: '10px',
        padding: '14px', fontFamily: 'monospace', zIndex: '9999',
        boxShadow: '0 4px 24px rgba(0,0,0,0.6)', userSelect: 'none', display: 'none'
      });
      document.body.appendChild(panel);

      // Create floating bubble icon (FAB)
      const fab = document.createElement('button');
      fab.id = 'qo-fab';
      fab.innerHTML = '⚡';
      fab.title = 'Quick Order';
      Object.assign(fab.style, {
        position: 'fixed', bottom: '24px', right: '24px',
        width: '50px', height: '50px', borderRadius: '50%',
        background: '#1f6feb', border: 'none', color: '#ffffff',
        fontSize: '20px', display: 'flex', alignItems: 'center', justifyContent: 'center',
        cursor: 'pointer', zIndex: '9998', boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
        transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)'
      });

      fab.onmouseenter = () => {
        fab.style.background = '#388bfd';
        fab.style.transform = 'scale(1.1)';
        fab.style.boxShadow = '0 6px 20px rgba(31, 111, 235, 0.4)';
      };
      fab.onmouseleave = () => {
        fab.style.background = '#1f6feb';
        fab.style.transform = 'scale(1)';
        fab.style.boxShadow = '0 4px 16px rgba(0,0,0,0.5)';
      };
      fab.onmousedown = () => {
        fab.style.transform = 'scale(0.95)';
      };
      fab.onmouseup = () => {
        fab.style.transform = 'scale(1.1)';
      };
      fab.onclick = () => {
        window.qoToggle();
      };
      document.body.appendChild(fab);

      window.qoToggle = () => {
        const isHidden = panel.style.display === 'none';
        panel.style.display = isHidden ? '' : 'none';
        const f = document.getElementById('qo-fab');
        if (f) f.style.display = isHidden ? 'none' : 'flex';
        if (panel.style.display !== 'none') qoFetchLtp();
      };

      // ── WATCH PANEL ────────────────────────────────────────────────────────────
      // Floating panel — RSI candidates jo entry ke paas hain
      // Zones: OVERSOLD(<30 CE zone), NEAR_OS(30-35), OVERBOUGHT(>70 PE zone), NEAR_OB(65-70)
      const wp = document.createElement('div');
      wp.id = 'watch-panel';
      Object.assign(wp.style, {
        position: 'fixed', top: '60px', right: '16px', width: '340px',
        background: '#161b22', border: '1px solid #30363d', borderRadius: '10px',
        padding: '14px', zIndex: '9998', boxShadow: '0 4px 24px rgba(0,0,0,0.6)',
        display: 'none', fontFamily: 'monospace'
      });
      wp.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <span style="color:#58a6ff;font-size:12px;font-weight:bold;letter-spacing:1px">👁 RSI WATCH</span>
      <div style="display:flex;gap:8px;align-items:center">
        <span id="watch-updated" style="font-size:10px;color:#8b949e">—</span>
        <button onclick="watchFetch()" style="background:#21262d;border:1px solid #30363d;border-radius:4px;color:#8b949e;font-size:11px;padding:2px 7px;cursor:pointer">↻</button>
        <button onclick="watchToggle()" style="background:none;border:none;color:#8b949e;font-size:14px;cursor:pointer">✕</button>
      </div>
    </div>
    <div style="font-size:10px;color:#8b949e;margin-bottom:8px;display:flex;gap:10px">
      <span style="color:#3fb950">■ CE zone</span>
      <span style="color:#f85149">■ PE zone</span>
      <span style="color:#d29922">■ Approaching</span>
      <span style="color:#8b949e">■ Neutral</span>
    </div>
    <div id="watch-body" style="max-height:400px;overflow-y:auto">
      <div style="color:#8b949e;font-size:12px;text-align:center;padding:20px">Loading...</div>
    </div>`;
      document.body.appendChild(wp);

      let _watchTimer = null;

      window.watchFetch = function () {
        fetch('/api/watch')
          .then(r => r.json())
          .then(d => {
            const body = document.getElementById('watch-body');
            const upd = document.getElementById('watch-updated');
            if (d.updated) upd.textContent = d.updated.slice(11, 16) + ' IST';

            const rows = (d.symbols || []);
            if (!rows.length) {
              body.innerHTML = '<div style="color:#8b949e;font-size:12px;text-align:center;padding:20px">No data yet — wait for next scan cycle</div>';
              return;
            }

            // Zone → visual style
            const zStyle = {
              OVERSOLD: { bg: 'rgba(63,185,80,0.12)', border: '#3fb950', badge: 'CE ZONE', bc: '#3fb950' },
              NEAR_OS: { bg: 'rgba(210,153,34,0.10)', border: '#d29922', badge: 'CE SOON', bc: '#d29922' },
              OVERBOUGHT: { bg: 'rgba(248,81,73,0.12)', border: '#f85149', badge: 'PE ZONE', bc: '#f85149' },
              NEAR_OB: { bg: 'rgba(210,153,34,0.10)', border: '#d29922', badge: 'PE SOON', bc: '#d29922' },
              NEUTRAL: { bg: 'transparent', border: '#21262d', badge: '', bc: '#555' },
            };

            // Group by strategy — header dikhao jab strategy change ho
            let lastStrat = null;
            let html = '';
            const interesting = rows.filter(r => r.zone !== 'NEUTRAL');
            const neutral = rows.filter(r => r.zone === 'NEUTRAL');

            [...interesting, ...neutral].forEach(r => {
              const z = zStyle[r.zone] || zStyle.NEUTRAL;
              const pos = r.pos === 1 ? '<span style="color:#3fb950;font-size:10px">▲CE</span>'
                : r.pos === -1 ? '<span style="color:#f85149;font-size:10px">▼PE</span>' : '';
              const sig = r.signal === 'EXIT' ? '<span style="color:#8b949e;font-size:10px">EXIT</span>'
                : r.signal === 'BUY' ? '<span style="color:#3fb950;font-size:10px">★BUY</span>'
                  : r.signal === 'SELL' ? '<span style="color:#f85149;font-size:10px">★SELL</span>' : '';
              const strat = r.strategy || '';

              // Strategy group header
              if (strat !== lastStrat) {
                lastStrat = strat;
                html += `<div title="${strat}" style="color:#58a6ff;font-size:10px;font-weight:bold;letter-spacing:1px;
                                 padding:6px 4px 2px;margin-top:${html ? '8px' : '0'}">${regLabel(strat)}</div>`;
              }

              html += `
          <div style="display:flex;align-items:center;gap:6px;padding:5px 8px;margin-bottom:3px;
                      background:${z.bg};border:1px solid ${z.border};border-radius:6px">
            <span style="width:88px;color:#e6edf3;font-size:12px;font-weight:bold;flex-shrink:0">${r.sym}</span>
            <span style="width:52px;color:#8b949e;font-size:11px;text-align:right;flex-shrink:0">${r.close}</span>
            <span style="width:34px;color:#e6edf3;font-size:12px;font-weight:bold;text-align:right;flex-shrink:0">${r.rsi ?? ''}</span>
            <span style="flex:1;font-size:10px;color:${z.bc};font-weight:bold">${z.badge}</span>
            <span>${pos}${sig}</span>
          </div>`;
            });
            body.innerHTML = html;
          })
          .catch(() => { });
      };

      window.watchToggle = function () {
        wp.style.display = wp.style.display === 'none' ? '' : 'none';
        if (wp.style.display !== 'none') {
          watchFetch();
          // auto-refresh har 30s jab panel open ho
          _watchTimer = setInterval(() => watchFetch(), 30000);
        } else {
          clearInterval(_watchTimer);
        }
      };

      // drag

      const hdr = document.getElementById('qo-header');
      let dragging = false, dx = 0, dy = 0;
      hdr.addEventListener('mousedown', e => {
        dragging = true;
        const r = panel.getBoundingClientRect();
        dx = e.clientX - r.left; dy = e.clientY - r.top;
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
        e.preventDefault();
      });
      document.addEventListener('mousemove', e => {
        if (!dragging) return;
        panel.style.left = (e.clientX - dx) + 'px';
        panel.style.top = (e.clientY - dy) + 'px';
      });
      document.addEventListener('mouseup', () => dragging = false);
      // Cache element refs immediately after panel is in DOM
      const _qoElPeLtp = document.getElementById('qo-pe-ltp');
      const _qoElCeLtp = document.getElementById('qo-ce-ltp');
      const _qoElPeSym = document.getElementById('qo-pe-sym');
      const _qoElCeSym = document.getElementById('qo-ce-sym');
      const _qoElTs = document.getElementById('qo-ts');

      let _qoAutoRefresh = null;
      document.getElementById('qo-close').onclick = () => {
        panel.style.display = 'none';
        const f = document.getElementById('qo-fab');
        if (f) f.style.display = 'flex';
      };

      let _qoLtpTimer = null;
      async function qoFetchLtp() {
        if (window.feedPaused) return;
        try {
          const r = await fetch(`/api/option-ltp?symbol=${qoSym}&offset=${qoAtm}`);
          const j = await r.json();
          const now = new Date();
          const ts = now.getHours().toString().padStart(2, '0') + ':' + now.getMinutes().toString().padStart(2, '0') + ':' + now.getSeconds().toString().padStart(2, '0');
          if (j.ok) {
            window._qoPeLtp = j.pe_ltp; window._qoCeLtp = j.ce_ltp;
            _qoSyncPricePlaceholder();
            _qoElPeLtp.textContent = j.pe_ltp != null ? j.pe_ltp.toFixed(2) : '—';
            _qoElCeLtp.textContent = j.ce_ltp != null ? j.ce_ltp.toFixed(2) : '—';
            _qoElPeSym.textContent = (j.pe_sym || '').replace(qoSym + '-', '');
            _qoElCeSym.textContent = (j.ce_sym || '').replace(qoSym + '-', '');
            if (_qoElTs) _qoElTs.textContent = '⚡ ' + ts + (j._stale ? ' (stale)' : '');
          } else {
            _qoElPeLtp.textContent = j.msg || 'Error';
            _qoElCeLtp.textContent = '';
            if (_qoElTs) _qoElTs.textContent = '❌ ' + ts;
          }
        } catch (e) {
          _qoElPeLtp.textContent = 'JS Error: ' + e.message;
          if (_qoElTs) _qoElTs.textContent = '❌ err';
        }
      }

      // First fetch on open + auto-refresh every 4s
      qoFetchLtp();
      _qoAutoRefresh = setInterval(qoFetchLtp, 4000);

      window.qoGetMode = () => qoMode;

      window.qoSetMode = m => {
        qoMode = m;
        document.getElementById('qo-paper').style.background = m === 'paper' ? '#d29922' : 'transparent';
        document.getElementById('qo-paper').style.color = m === 'paper' ? '#0d1117' : '#8b949e';
        document.getElementById('qo-live').style.background = m === 'live' ? '#f85149' : 'transparent';
        document.getElementById('qo-live').style.color = m === 'live' ? '#fff' : '#8b949e';
        document.getElementById('qo-status').textContent = m === 'live' ? 'Mode: LIVE — Real orders!' : 'Mode: PAPER';
        document.getElementById('qo-status').style.color = m === 'live' ? '#f85149' : '#8b949e';
      };

      window.qoGetBroker = () => qoBroker;
      window.qoSetBroker = b => {
        qoBroker = b;
        document.getElementById('qo-broker-dhan').style.background = b === 'dhan' ? '#1f6feb' : 'transparent';
        document.getElementById('qo-broker-dhan').style.color = b === 'dhan' ? '#fff' : '#8b949e';
        document.getElementById('qo-broker-kite').style.background = b === 'kite' ? '#a371f7' : 'transparent';
        document.getElementById('qo-broker-kite').style.color = b === 'kite' ? '#fff' : '#8b949e';
      };
      function qoRefreshLtp() {
        clearTimeout(_qoLtpTimer);
        _qoLtpTimer = setTimeout(qoFetchLtp, 300);
      }

      window.qoSetSym = s => {
        qoSym = s;
        const _pe = document.getElementById('qo-price'); if (_pe) _pe.value = '';  // strike change → stale price clear
        ['NIFTY', 'BANKNIFTY'].forEach(sym => {
          const b = document.getElementById('qo-sym-' + sym);
          const sel = sym === s;
          b.style.borderColor = sel ? '#1f6feb' : '#30363d';
          b.style.background = sel ? '#1f6feb22' : '#21262d';
          b.style.color = sel ? '#58a6ff' : '#8b949e';
        });
        updateQtyHint();
        qoRefreshLtp();
      };

      window.qoSetAtm = v => {
        qoAtm = v;
        const _pe = document.getElementById('qo-price'); if (_pe) _pe.value = '';  // strike change → stale price clear
        document.querySelectorAll('#qo-atm-row button').forEach(b => {
          const bv = parseInt(b.dataset.atm);
          b.style.background = bv === v ? '#1f6feb' : '#21262d';
          b.style.borderColor = bv === v ? '#1f6feb' : '#30363d';
          b.style.color = bv === v ? '#fff' : '#8b949e';
        });
        qoRefreshLtp();
      };

      window.updateQtyHint = () => {
        const ls = LOT_SIZES[qoSym] || 65;
        const lots = parseInt(document.getElementById('qo-lots')?.value) || 1;
        const el = document.getElementById('qo-qty-num');
        const ls2 = document.getElementById('qo-ls');
        if (el) el.textContent = lots * ls;
        if (ls2) ls2.textContent = ls;
      };

      // Selected option leg (CE/PE) — BUY/SELL dono isi pe chalenge
      window._qoOptType = 'CE';
      window.qoSetOpt = type => {
        window._qoOptType = type;
        const ce = document.getElementById('qo-opt-ce'), pe = document.getElementById('qo-opt-pe');
        const tce = document.getElementById('qo-tick-ce'), tpe = document.getElementById('qo-tick-pe');
        if (ce) { ce.style.borderColor = type === 'CE' ? '#3fb950' : '#3fb95040'; ce.style.background = type === 'CE' ? '#3fb95028' : '#3fb95015'; }
        if (pe) { pe.style.borderColor = type === 'PE' ? '#f85149' : '#f8514940'; pe.style.background = type === 'PE' ? '#f8514928' : '#f8514915'; }
        if (tce) tce.style.visibility = type === 'CE' ? 'visible' : 'hidden';
        if (tpe) tpe.style.visibility = type === 'PE' ? 'visible' : 'hidden';
        ['qo-buy-leg', 'qo-sell-leg', 'qo-leg-hint'].forEach(id => { const e = document.getElementById(id); if (e) { e.textContent = type; e.style.color = id === 'qo-leg-hint' ? (type === 'CE' ? '#3fb950' : '#f85149') : ''; } });
        // leg badla → stale custom price hata do, blank = live LTP par jaayega
        const _priceEl = document.getElementById('qo-price'); if (_priceEl) _priceEl.value = '';
        _qoSyncPricePlaceholder();
      };

      // price box ka placeholder = selected leg ki live LTP (blank chhodo to yahi use hoga)
      function _qoSyncPricePlaceholder() {
        const t = window._qoOptType || 'CE';
        const v = t === 'CE' ? window._qoCeLtp : window._qoPeLtp;
        const el = document.getElementById('qo-price');
        if (el) el.placeholder = (v != null) ? ('auto = ' + v.toFixed(2) + ' (live ' + t + ')') : 'auto (live LTP)';
      }

      window.qoOrder = async side => {
        const lots = parseInt(document.getElementById('qo-lots').value) || 1;
        const st = document.getElementById('qo-status');
        const optType = window._qoOptType || 'CE';
        // blank price → auto: use displayed LTP of the SELECTED leg (CE/PE), side se nahi
        const liveLtp = optType === 'CE' ? window._qoCeLtp : window._qoPeLtp;
        let priceRaw = document.getElementById('qo-price').value.trim();
        let price = priceRaw === '' ? liveLtp : parseFloat(priceRaw);
        if (price == null || isNaN(price) || price <= 0) { st.textContent = 'Price nahi mila — LTP load hone do'; st.style.color = '#f85149'; return; }
        // safety: typed price live LTP se bahut door → Dhan price-band reject karega. Confirm maango.
        if (priceRaw !== '' && liveLtp && Math.abs(price - liveLtp) / liveLtp > 0.20) {
          const pct = Math.round(Math.abs(price - liveLtp) / liveLtp * 100);
          if (!confirm(`⚠️ Limit ${price.toFixed(2)} live ${optType} LTP ${liveLtp.toFixed(2)} se ${pct}% door hai.\nDhan price-band pe reject kar sakta. Phir bhi ${side} bhejun?`)) {
            st.textContent = 'cancelled'; st.style.color = '#8b949e'; return;
          }
        }
        st.textContent = `LIMIT ${side} ${optType} @ ${price.toFixed(2)}...`; st.style.color = '#d29922';
        try {
          const r = await fetch('/api/manual-order', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: qoSym, side, opt_type: optType, lots, strike_offset: qoAtm, mode: qoMode, broker: qoBroker, order_type: 'LIMIT', price })
          });
          const j = await r.json();
          st.textContent = j.msg || (j.ok ? 'Done' : 'Error');
          st.style.color = j.ok ? '#3fb950' : '#f85149';
        } catch (e) { st.textContent = 'Network error'; st.style.color = '#f85149'; }
      };

      // initial CE selection highlight
      qoSetOpt('CE');

      // ── SYMBOL WATCHLIST PANEL — REMOVED (2026-06-25, user request) ──────────
      // The right-side resizable/collapsible watchlist dock was removed. The IIFE
      // is kept but returns immediately, so nothing renders and its 5s
      // /api/positions-ltp poll no longer runs (also frees a little rate limit).
      // To restore the watchlist, delete the early `return;` below (git history).
      (function () {
        return;
        const LS_SYMS = 'wl_symbols', LS_WIDTH = 'wl_width', LS_COLLAPSED = 'wl_collapsed';
        let syms = [];
        try { syms = JSON.parse(localStorage.getItem(LS_SYMS) || '[]'); } catch (e) { syms = []; }
        let width = parseInt(localStorage.getItem(LS_WIDTH)) || 280;
        let collapsed = localStorage.getItem(LS_COLLAPSED) === '1';

        const wl = document.createElement('div');
        wl.id = 'wl-panel';
        Object.assign(wl.style, {
          position: 'fixed', top: '60px', right: '0', bottom: '10px',
          width: (collapsed ? '34px' : width + 'px'),
          background: '#161b22', border: '1px solid #30363d', borderRight: 'none',
          borderTopLeftRadius: '10px', borderBottomLeftRadius: '10px',
          zIndex: '9000', boxShadow: '-4px 0 16px rgba(0,0,0,0.4)',
          display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: 'monospace'
        });
        wl.innerHTML = `
      <div id="wl-splitter" title="Drag to resize" style="position:absolute;left:-3px;top:0;bottom:0;width:6px;cursor:col-resize;z-index:1"></div>
      <div style="display:flex;align-items:center;gap:6px;padding:8px 10px;border-bottom:1px solid #30363d;flex-shrink:0">
        <button id="wl-collapse-btn" title="Collapse/Expand" style="background:#21262d;border:1px solid #30363d;border-radius:4px;color:#8b949e;font-size:11px;width:20px;height:20px;cursor:pointer;flex-shrink:0">»</button>
        <span id="wl-title" style="color:#58a6ff;font-size:12px;font-weight:bold;letter-spacing:.5px;flex:1;white-space:nowrap;overflow:hidden">📋 WATCHLIST</span>
      </div>
      <div id="wl-add-row" style="display:flex;gap:6px;padding:8px 10px;border-bottom:1px solid #21262d;flex-shrink:0">
        <input id="wl-add-input" placeholder="Symbol e.g. RELIANCE" style="flex:1;background:#0d1117;border:1px solid #30363d;border-radius:5px;color:#e6edf3;padding:5px 7px;font-size:11px;outline:none">
        <button id="wl-add-btn" style="background:#238636;border:none;border-radius:5px;color:#fff;font-size:12px;font-weight:bold;padding:5px 9px;cursor:pointer">+</button>
      </div>
      <div id="wl-body" style="flex:1;overflow-y:auto;padding:6px 8px"></div>`;
        document.body.appendChild(wl);

        function setCollapsed(c) {
          collapsed = c;
          localStorage.setItem(LS_COLLAPSED, c ? '1' : '0');
          wl.style.width = c ? '34px' : width + 'px';
          document.getElementById('wl-add-row').style.display = c ? 'none' : '';
          document.getElementById('wl-body').style.display = c ? 'none' : '';
          document.getElementById('wl-title').style.display = c ? 'none' : '';
          document.getElementById('wl-collapse-btn').textContent = c ? '«' : '»';
        }
        setCollapsed(collapsed);
        document.getElementById('wl-collapse-btn').onclick = () => setCollapsed(!collapsed);

        function saveSyms() { localStorage.setItem(LS_SYMS, JSON.stringify(syms)); }

        function renderRows(ltpMap) {
          const body = document.getElementById('wl-body');
          if (!syms.length) {
            body.innerHTML = '<div style="color:#8b949e;font-size:11px;text-align:center;padding:16px">Symbol add karo upar se</div>';
            return;
          }
          ltpMap = ltpMap || {};
          body.innerHTML = syms.map(s => {
            const q = ltpMap[s];
            const ltp = q && q.ltp != null ? q.ltp.toFixed(2) : '—';
            return `<div style="display:flex;align-items:center;gap:6px;padding:5px 6px;margin-bottom:3px;background:#0d1117;border:1px solid #21262d;border-radius:6px">
          <span style="flex:1;color:#e6edf3;font-size:11.5px;font-weight:bold;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${s}</span>
          <span style="color:#adbac7;font-size:11.5px;min-width:54px;text-align:right">${ltp}</span>
          <button data-sym="${s}" class="wl-rm" style="background:none;border:none;color:#8b949e;font-size:12px;cursor:pointer;padding:0 2px">✕</button>
        </div>`;
          }).join('');
          body.querySelectorAll('.wl-rm').forEach(btn => {
            btn.onclick = () => {
              syms = syms.filter(s => s !== btn.getAttribute('data-sym'));
              saveSyms(); renderRows(window._wlLastLtp);
            };
          });
        }

        async function fetchLtp() {
          if (!syms.length || collapsed) { renderRows({}); return; }
          try {
            const r = await fetch('/api/positions-ltp?syms=' + syms.map(encodeURIComponent).join(','));
            const j = await r.json();
            window._wlLastLtp = j.ltp_map || {};
            renderRows(window._wlLastLtp);
          } catch (e) { renderRows(window._wlLastLtp); }
        }

        document.getElementById('wl-add-btn').onclick = () => {
          const inp = document.getElementById('wl-add-input');
          const v = inp.value.trim().toUpperCase();
          if (!v) return;
          if (!syms.includes(v)) { syms.push(v); saveSyms(); renderRows(window._wlLastLtp); fetchLtp(); }
          inp.value = '';
        };
        document.getElementById('wl-add-input').addEventListener('keydown', e => {
          if (e.key === 'Enter') document.getElementById('wl-add-btn').click();
        });

        // resize via left-edge splitter drag
        const splitter = document.getElementById('wl-splitter');
        let dragging = false;
        splitter.addEventListener('mousedown', e => { dragging = true; e.preventDefault(); });
        document.addEventListener('mousemove', e => {
          if (!dragging || collapsed) return;
          width = Math.max(200, Math.min(560, window.innerWidth - e.clientX));
          wl.style.width = width + 'px';
        });
        document.addEventListener('mouseup', () => {
          if (dragging) { dragging = false; localStorage.setItem(LS_WIDTH, width); }
        });

        renderRows({});
        fetchLtp();
        setInterval(fetchLtp, 5000);
      })();
    })();

    async function closePosition(tSym, entrySide, qty, mode, source, strategy, btnId) {
      const btn = document.getElementById(btnId);
      const modeNow = mode || 'paper';   // position ke apne mode me close (live→real order, paper→log)
      if (!confirm(`${entrySide === 'BUY' ? 'SELL' : 'BUY'} ${qty} ${tSym}\nClose karein? (${modeNow.toUpperCase()})`)) return;
      if (btn) { btn.disabled = true; btn.textContent = '...'; }
      try {
        let r = await fetch('/api/close-position', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ t_sym: tSym, entry_side: entrySide, qty: qty, mode: modeNow, source: source || '', strategy: strategy || '' })
        });
        let j = await r.json();
        if (j.ok) {
          flash('✅ ' + j.msg, '#3fb950');
          const el = document.getElementById('ord-open'); if (el) el.dataset.fp = '';  // force rebuild
          setTimeout(() => { if (activeTab === 'orders') ordersRender(); }, 800);
        } else {
          flash('❌ ' + j.msg, '#f85149');
          if (btn) { btn.disabled = false; btn.textContent = entrySide === 'BUY' ? 'SELL ✕' : 'BUY ✕'; }
        }
      } catch (e) {
        flash('❌ Network error', '#f85149');
        if (btn) { btn.disabled = false; btn.textContent = 'ERR'; }
      }
    }

    async function closePositionGroup(groupId, mode) {
      const modeNow = mode || 'paper';
      if (!confirm(`Hedge group ${groupId}\nDono legs ek sath close karein? (${modeNow.toUpperCase()})`)) return;
      flash('⏳ Closing group...', '#8b949e');
      try {
        const r = await fetch('/api/close-position-group', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ group_id: groupId, mode: modeNow })
        });
        const j = await r.json();
        flash(j.ok ? '✅ Group closed: ' + j.msg : '❌ ' + j.msg, j.ok ? '#3fb950' : '#f85149');
        const el = document.getElementById('ord-open'); if (el) el.dataset.fp = '';
        setTimeout(() => { if (activeTab === 'orders') ordersRender(); }, 800);
      } catch (e) {
        flash('❌ Network error', '#f85149');
      }
    }

    // Book-close: position ko ledger se hatao — koi real Dhan order nahi jaata.
    // Rejected/phantom live positions ke liye (jo Dhan pe asal me the hi nahi).
    async function bookClose(tSym, entrySide, qty, entryPrice, mode, source, strategy) {
      if (!confirm(`🗑 ${tSym}\nBook se hata dein? (pnl 0)\n\n⚠️ Koi REAL order nahi jaayega — sirf ledger saaf hoga.\nSirf un positions ke liye jo Dhan pe asal me nahi hain (rejected/phantom).`)) return;
      try {
        const r = await fetch('/api/orders/book-close', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ t_sym: tSym, entry_side: entrySide, qty: qty, entry_price: entryPrice, mode: mode || 'paper', source: source || '', strategy: strategy || '' })
        });
        const j = await r.json();
        if (j.ok) {
          flash('✅ ' + j.msg, '#8b949e');
          const el = document.getElementById('ord-open'); if (el) el.dataset.fp = '';
          setTimeout(() => { if (activeTab === 'orders') ordersRender(); }, 500);
        } else flash('❌ ' + j.msg, '#f85149');
      } catch (e) { flash('❌ Network error', '#f85149'); }
    }

