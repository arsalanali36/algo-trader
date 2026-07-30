// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
//
// 2026-07-28 REDESIGN — Quick Order is now an OPTION-CHAIN panel (Sensibull-style):
// one ATM±12 chain drives everything. Two tabs: Order (single OR multi-leg) and
// Trigger. Hover a strike → B/S buttons appear right beside it → click to add a leg.
// Single leg = instant marketable order; 2+ legs = basket (net credit + hedged
// margin shown live). The old +/-3 offset + big CE/PE boxes + manual straddle
// leg-builder are gone; Auto-straddle (9:20 / alert) is preserved in a collapsible.
// Firing reuses existing money paths: /api/manual-order (single), /api/chain/fire-basket
// (multi → flex-straddle spine), /api/triggers (arm). Chain data + live LTP: /api/option-chain.
    // ── QUICK ORDER FLOATING PANEL ───────────────────────────────────────────────
    (function () {
      const LOT_SIZES = { NIFTY: 65, BANKNIFTY: 30 };  // fallback; overwritten by API
      let qoSym = 'NIFTY', qoMode = 'paper', qoBroker = 'dhan';
      let qoTab = 'order';                 // 'order' | 'trigger'
      let qoExpiry = 'near';               // near | nextmonth | YYYY-MM-DD
      window.qoTab = qoTab;

      // chain state (from /api/option-chain) + current leg selection
      // qoChain.rows = [{strike, off_ce, off_pe, ce:{ltp,oi,iv,delta,sym}, pe:{...}}]
      window.qoChain = { atm: null, step: null, lot: null, spot: null, rows: [] };
      // selection keyed "STRIKE|CE" / "STRIKE|PE" -> 'B' | 'S'
      window.qoSel = {};

      // fetch real lot sizes from scrip master
      fetch('/api/lot-sizes').then(r => r.json()).then(d => {
        LOT_SIZES.NIFTY = d.NIFTY || 65;
        LOT_SIZES.BANKNIFTY = d.BANKNIFTY || 30;
        updateQtyHint();
      }).catch(() => { });

      // one-time style block for the chain (hover-reveal B/S)
      if (!document.getElementById('qo-chain-style')) {
        const st = document.createElement('style');
        st.id = 'qo-chain-style';
        st.textContent = `
#qo-chain .qo-crow{position:relative}
#qo-chain .qo-crow .qo-bs{opacity:0;pointer-events:none;transition:opacity .08s}
#qo-chain .qo-crow:hover .qo-bs,#qo-chain .qo-crow.hassel .qo-bs{opacity:1;pointer-events:auto}
#qo-chain .qo-crow:hover{background:#161b22 !important}
#qo-chain::-webkit-scrollbar{width:8px}
#qo-chain::-webkit-scrollbar-thumb{background:#30363d;border-radius:4px}
#qo-chain::-webkit-scrollbar-track{background:#0d1117}`;
        document.head.appendChild(st);
      }

      const panel = document.createElement('div');
      panel.id = 'qo-panel';
      panel.innerHTML = `
<div id="qo-header" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;cursor:grab">
  <span style="color:#58a6ff;font-size:12px;font-weight:bold;letter-spacing:1px">QUICK ORDER</span>
  <span id="qo-close" style="color:#8b949e;font-size:16px;cursor:pointer;line-height:1">&#x2715;</span>
</div>
<div style="display:flex;background:#0d1117;border-radius:8px;padding:3px;margin-bottom:11px;gap:4px">
  <button id="qo-tab-order"   onclick="qoSetTab('order')"   style="flex:1;padding:7px 0;border:1px solid #1f6feb;border-radius:6px;font-size:11px;font-weight:bold;cursor:pointer;background:#0d1117;color:#58a6ff">&#129513; Order <span style="color:#565f6a;font-weight:400">(1 / multi)</span></button>
  <button id="qo-tab-trigger" onclick="qoSetTab('trigger')" style="flex:1;padding:7px 0;border:1px solid #30363d;border-radius:6px;font-size:11px;font-weight:bold;cursor:pointer;background:#161b22;color:#8b949e">&#127919; Trigger</button>
</div>
<div style="display:flex;gap:10px;margin-bottom:9px">
  <div>
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">MODE</div>
    <div style="display:flex;background:#0d1117;border-radius:7px;padding:3px;gap:3px">
      <button id="qo-paper" onclick="qoSetMode('paper')" style="padding:5px 9px;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:#d29922;color:#0d1117">PAPER</button>
      <button id="qo-live"  onclick="qoSetMode('live')"  style="padding:5px 9px;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:transparent;color:#8b949e">LIVE</button>
    </div>
  </div>
  <div>
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">ACCOUNT</div>
    <div style="display:flex;background:#0d1117;border-radius:7px;padding:3px;gap:3px">
      <button id="qo-broker-dhan" onclick="qoSetBroker('dhan')" style="padding:5px 9px;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:#1f6feb;color:#fff">DHAN</button>
      <button id="qo-broker-kite" onclick="qoSetBroker('kite')" style="padding:5px 9px;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:transparent;color:#8b949e">ZERODHA</button>
    </div>
  </div>
  <div style="flex:1;min-width:0">
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">INDEX</div>
    <div style="display:flex;background:#0d1117;border-radius:7px;padding:3px;gap:3px">
      <button id="qo-sym-NIFTY"     onclick="qoSetSym('NIFTY')"     style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:#1f6feb22;color:#58a6ff">NIFTY</button>
      <button id="qo-sym-BANKNIFTY" onclick="qoSetSym('BANKNIFTY')" style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:transparent;color:#8b949e">BNF</button>
    </div>
  </div>
</div>
<div style="display:flex;gap:10px;margin-bottom:9px;align-items:flex-end">
  <div style="flex:1;min-width:0">
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">EXPIRY</div>
    <select id="qo-expiry" onchange="qoSetExpiry(this.value)" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:6px 7px;font-size:11px;cursor:pointer;outline:none">
      <option value="near">Near (weekly)</option>
      <option value="nextmonth">Next month</option>
    </select>
  </div>
  <div style="width:84px">
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">LOTS</div>
    <div style="display:flex;align-items:center;gap:5px">
      <input id="qo-lots" type="number" value="1" min="1" oninput="updateQtyHint();qoSummary()" style="width:42px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:7px 4px;font-size:13px;text-align:center;outline:none">
      <span style="font-size:10px;color:#6e7681;white-space:nowrap"><span id="qo-qty-num" style="color:#adbac7;font-weight:bold">65</span>q</span>
    </div>
  </div>
</div>
<div id="qo-modectl" style="margin-bottom:9px"></div>
<!-- CHAIN -->
<div style="display:grid;grid-template-columns:30px 1fr 58px 1fr 30px;background:#161b22;border-top:1px solid #21262d;border-bottom:1px solid #21262d;font-size:9px;color:#8b949e;text-transform:uppercase;letter-spacing:.02em;padding:5px 0">
  <div style="text-align:center">&Delta;</div>
  <div style="text-align:right;padding-right:8px">Call LTP&middot;OI</div>
  <div style="text-align:center">Strike</div>
  <div style="text-align:left;padding-left:8px">Put OI&middot;LTP</div>
  <div style="text-align:center">&Delta;</div>
</div>
<div id="qo-chain" style="max-height:290px;overflow-y:auto;font-family:monospace;font-size:11.5px">
  <div style="color:#8b949e;font-size:11px;text-align:center;padding:26px">chain load ho raha...</div>
</div>
<!-- SELECTED LEGS + ACTION -->
<div style="border-top:1px solid #21262d;padding-top:9px;margin-top:2px">
  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
    <span style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px">SELECTED LEGS</span>
    <span id="qo-legcount" style="color:#565f6a;font-size:10px"></span>
    <span onclick="qoClearLegs()" id="qo-clear" style="margin-left:auto;font-size:9px;color:#6e7681;cursor:pointer;display:none">clear all</span>
  </div>
  <div id="qo-legs" style="display:flex;flex-wrap:wrap;gap:6px;min-height:24px"></div>
  <div id="qo-netrow" style="display:none;gap:18px;margin-top:9px;align-items:center">
    <div><div style="font-size:9px;color:#6e7681">Net credit</div><div id="qo-cr" style="font-size:14px;font-weight:bold;color:#3fb950">&#8377;0</div></div>
    <div><div style="font-size:9px;color:#6e7681">Margin</div><div id="qo-mg" style="font-size:14px;font-weight:bold;color:#e6edf3">&#8377;0</div></div>
  </div>
  <button id="qo-act" onclick="qoAct()" style="width:100%;margin-top:10px;padding:11px;border:none;border-radius:6px;color:#fff;font-size:13px;font-weight:bold;cursor:pointer;background:#238636;opacity:.5">Strike select karo</button>
  <div id="qo-hint" style="text-align:center;color:#565f6a;font-size:9px;margin-top:6px"></div>
</div>
<!-- AUTO STRADDLE (preserved) -->
<div style="border-top:1px solid #21262d;margin-top:10px;padding-top:9px">
  <div onclick="qoAutoToggle()" style="display:flex;align-items:center;gap:6px;cursor:pointer">
    <span id="qo-auto-caret" style="color:#8b949e;font-size:10px">&#9656;</span>
    <span style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px">&#129651; AUTO STRADDLE &middot; 9:20 / alert (paper)</span>
  </div>
  <div id="qo-auto-body" style="display:none;margin-top:8px">
    <div style="display:flex;gap:8px;margin-bottom:8px">
      <div style="flex:1"><div style="font-size:9px;color:#6e7681;margin-bottom:4px">LOTS</div><input id="qo-strad-lots" type="number" value="1" min="1" onchange="qoStradCfgSave()" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:6px;font-size:12px;text-align:center;outline:none;box-sizing:border-box"></div>
      <div style="flex:1"><div style="font-size:9px;color:#3fb950;margin-bottom:4px">TARGET (pt)</div><input id="qo-strad-tp" type="number" value="30" step="1" onchange="qoStradCfgSave()" style="width:100%;background:#0d1117;border:1px solid #1a7f37;border-radius:6px;color:#3fb950;padding:6px;font-size:12px;text-align:center;outline:none;box-sizing:border-box"></div>
      <div style="flex:1"><div style="font-size:9px;color:#f85149;margin-bottom:4px">SL (pt)</div><input id="qo-strad-sl" type="number" value="30" step="1" onchange="qoStradCfgSave()" style="width:100%;background:#0d1117;border:1px solid #5c1a1f;border-radius:6px;color:#f85149;padding:6px;font-size:12px;text-align:center;outline:none;box-sizing:border-box"></div>
    </div>
    <label style="display:flex;align-items:center;gap:7px;font-size:11px;color:#adbac7;margin-bottom:5px;cursor:pointer"><input type="checkbox" id="qo-strad-920" onchange="qoStradCfgSave()"> 9:20 auto &middot; NIFTY + BANKNIFTY (roz)</label>
    <label style="display:flex;align-items:center;gap:7px;font-size:11px;color:#adbac7;margin-bottom:5px;cursor:pointer"><input type="checkbox" id="qo-strad-alert" onchange="qoStradCfgSave()"> Alert pe auto (straddle spike/crush/gamma)</label>
    <label style="display:flex;align-items:center;gap:6px;font-size:11px;color:#adbac7;margin-bottom:9px;cursor:pointer;flex-wrap:wrap"><input type="checkbox" id="qo-strad-hedge" onchange="qoStradCfgSave()"> &#128737; auto-hedge &mdash; sasti OTM wing (&le; &#8377;<input id="qo-strad-hedgemax" type="number" value="2" step="0.5" min="0.5" onchange="qoStradCfgSave()" style="width:42px;background:#0d1117;border:1px solid #30363d;border-radius:4px;color:#e6edf3;padding:3px;font-size:11px;text-align:center;outline:none">)</label>
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:6px">ACTIVE / TODAY &middot; <span id="qo-strad-sym-lbl">NIFTY</span></div>
    <div id="qo-strad-list" style="font-size:11px;color:#8b949e">&mdash;</div>
  </div>
</div>
<div id="qo-status" style="font-size:11px;color:#8b949e;text-align:center;min-height:16px;margin-top:8px">Mode: PAPER</div>`;

      Object.assign(panel.style, {
        position: 'fixed', top: '16px', right: '16px', width: '470px',
        maxHeight: 'calc(100vh - 32px)', overflowY: 'auto', boxSizing: 'border-box',
        background: '#161b22', border: '1px solid #30363d', borderRadius: '12px',
        padding: '16px', fontFamily: 'monospace', zIndex: '9999',
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
      fab.onmouseenter = () => { fab.style.background = '#388bfd'; fab.style.transform = 'scale(1.1)'; fab.style.boxShadow = '0 6px 20px rgba(31, 111, 235, 0.4)'; };
      fab.onmouseleave = () => { fab.style.background = '#1f6feb'; fab.style.transform = 'scale(1)'; fab.style.boxShadow = '0 4px 16px rgba(0,0,0,0.5)'; };
      fab.onmousedown = () => { fab.style.transform = 'scale(0.95)'; };
      fab.onmouseup = () => { fab.style.transform = 'scale(1.1)'; };
      fab.onclick = () => { window.qoToggle(); };
      document.body.appendChild(fab);

      window.qoToggle = () => {
        const isHidden = panel.style.display === 'none';
        panel.style.display = isHidden ? '' : 'none';
        const f = document.getElementById('qo-fab');
        if (f) f.style.display = isHidden ? 'none' : 'flex';
        if (panel.style.display !== 'none') { qoLoadChain(); qoLoadExpiries(); qoRefreshStraddles(); }
      };

      // ── WATCH PANEL ────────────────────────────────────────────────────────────
      // Floating panel — RSI candidates jo entry ke paas hain
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
            const zStyle = {
              OVERSOLD: { bg: 'rgba(63,185,80,0.12)', border: '#3fb950', badge: 'CE ZONE', bc: '#3fb950' },
              NEAR_OS: { bg: 'rgba(210,153,34,0.10)', border: '#d29922', badge: 'CE SOON', bc: '#d29922' },
              OVERBOUGHT: { bg: 'rgba(248,81,73,0.12)', border: '#f85149', badge: 'PE ZONE', bc: '#f85149' },
              NEAR_OB: { bg: 'rgba(210,153,34,0.10)', border: '#d29922', badge: 'PE SOON', bc: '#d29922' },
              NEUTRAL: { bg: 'transparent', border: '#21262d', badge: '', bc: '#555' },
            };
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

      document.getElementById('qo-close').onclick = () => {
        panel.style.display = 'none';
        const f = document.getElementById('qo-fab');
        if (f) f.style.display = 'flex';
      };

      const _qoInr = n => '₹' + Math.round(n).toLocaleString('en-IN');

      // ── CHAIN ──────────────────────────────────────────────────────────────────
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
      window.qoSetSym = s => {
        qoSym = s;
        ['NIFTY', 'BANKNIFTY'].forEach(sym => {
          const b = document.getElementById('qo-sym-' + sym);
          if (!b) return;
          const sel = sym === s;
          b.style.background = sel ? '#1f6feb22' : 'transparent';
          b.style.color = sel ? '#58a6ff' : '#8b949e';
        });
        window.qoSel = {};                 // strikes differ per index → clear selection
        window.qoChain.rows = [];
        updateQtyHint(); qoSummary();
        qoLoadExpiries(); qoLoadChain();
        qoStradCfgLoad(); qoRefreshStraddles();
      };
      window.qoSetExpiry = v => { qoExpiry = v || 'near'; window.qoSel = {}; qoSummary(); qoLoadChain(); };

      window.updateQtyHint = () => {
        const ls = LOT_SIZES[qoSym] || 65;
        const lots = parseInt(document.getElementById('qo-lots')?.value) || 1;
        const el = document.getElementById('qo-qty-num');
        if (el) el.textContent = lots * ls;
      };

      window.qoLoadExpiries = async () => {
        const sel = document.getElementById('qo-expiry'); if (!sel) return;
        try {
          const j = await (await fetch('/api/option-expiries?symbol=' + qoSym)).json();
          const exps = (j && j.expiries) || [];
          let html = '<option value="near">Near (weekly)</option><option value="nextmonth">Next month</option>';
          if (exps.length) {
            html += '<option disabled>──── specific expiry ────</option>'
                  + exps.map(e => `<option value="${e.date}">${e.label}${e.monthly ? ' · monthly' : ''}</option>`).join('');
          }
          sel.innerHTML = html;
          const valid = ['near', 'nextmonth'].concat(exps.map(e => e.date));
          if (valid.indexOf(qoExpiry) >= 0) sel.value = qoExpiry;
          else { qoExpiry = 'near'; sel.value = 'near'; }
        } catch (e) { }
      };

      let _qoChainTimer = null, _qoChainTok = 0;
      window.qoLoadChain = async () => {
        if (document.hidden) return;   // background tab skip (feed pause doesn't apply — chain uses lake+REST, not the WS feed)
        if (panel.style.display === 'none') return;
        const box = document.getElementById('qo-chain'); if (!box) return;
        const tok = ++_qoChainTok;
        try {
          const j = await (await fetch('/api/option-chain', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: qoSym, n: (qoSym === 'BANKNIFTY' ? 35 : 25), expiry: qoExpiry })
          })).json();
          if (tok !== _qoChainTok) return;                 // stale — newer load already fired
          if (!j.ok) { box.innerHTML = `<div style="color:#8b949e;font-size:11px;text-align:center;padding:26px">${j.msg || 'chain nahi mila'}</div>`; return; }
          window.qoChain = { atm: j.atm, step: j.step, lot: j.lot, spot: j.spot,
                             snap_dt: j.snap_dt, rows: j.rows || [] };
          qoRenderChain();
          qoSummary();
        } catch (e) { box.innerHTML = '<div style="color:#f85149;font-size:11px;text-align:center;padding:26px">chain load fail</div>'; }
      };

      const _n2 = v => (v == null || isNaN(v)) ? '—' : (+v).toFixed(2);
      const _oiK = v => (v == null || isNaN(v)) ? '' : (Math.abs(v) >= 100000 ? (v / 100000).toFixed(1) + 'L' : Math.round(v / 1000) + 'k');
      function _bsBtns(K, ot, ltp) {
        const idB = window.qoSel[K + '|' + ot] === 'B', idS = window.qoSel[K + '|' + ot] === 'S';
        return `<span class="qo-bs" style="display:inline-flex;gap:2px">`
          + `<button onclick="qoChainClick(${K},'${ot}','B')" style="width:19px;height:18px;border-radius:3px;border:1px solid ${idB ? '#3fb950' : '#30363d'};background:${idB ? '#12341f' : '#0d1117'};color:#3fb950;font-size:9px;font-weight:700;cursor:pointer">B</button>`
          + `<button onclick="qoChainClick(${K},'${ot}','S')" style="width:19px;height:18px;border-radius:3px;border:1px solid ${idS ? '#f85149' : '#30363d'};background:${idS ? '#3d1518' : '#0d1117'};color:#f85149;font-size:9px;font-weight:700;cursor:pointer">S</button></span>`;
      }
      window.qoRenderChain = () => {
        const box = document.getElementById('qo-chain'); if (!box) return;
        const C = window.qoChain, atm = C.atm, spot = C.spot;
        // max OI for bar scaling
        let maxoi = 1;
        C.rows.forEach(r => { maxoi = Math.max(maxoi, r.ce && r.ce.oi || 0, r.pe && r.pe.oi || 0); });
        let html = '';
        C.rows.forEach(r => {
          const K = r.strike, ce = r.ce || {}, pe = r.pe || {};
          const hassel = (window.qoSel[K + '|CE'] || window.qoSel[K + '|PE']) ? ' hassel' : '';
          const isAtm = (K === atm);
          const cw = Math.min(100, (ce.oi || 0) / maxoi * 100), pw = Math.min(100, (pe.oi || 0) / maxoi * 100);
          const ceSel = window.qoSel[K + '|CE'], peSel = window.qoSel[K + '|PE'];
          const cd = ce.delta != null ? (+ce.delta).toFixed(2) : '', pd = pe.delta != null ? (+pe.delta).toFixed(2) : '';
          html += `<div class="qo-crow${hassel}" style="display:grid;grid-template-columns:30px 1fr 58px 1fr 30px;align-items:center;background:${isAtm ? '#12161d' : 'transparent'};border-bottom:1px solid #12161d">
  <div style="text-align:center;color:#565f6a;font-size:9px">${cd}</div>
  <div style="position:relative;display:flex;align-items:center;justify-content:flex-end;gap:6px;padding:4px">
    <div style="position:absolute;top:3px;bottom:3px;right:44px;width:${cw}%;max-width:calc(100% - 46px);background:#f8514916;border-radius:2px"></div>
    <span style="position:relative;color:${ceSel ? '#e6edf3' : '#adb6c0'}">${_n2(ce.ltp)} <span style="color:#565f6a;font-size:9px">${_oiK(ce.oi)}</span></span>
    ${_bsBtns(K, 'CE', ce.ltp)}</div>
  <div style="text-align:center;padding:3px 0;background:${isAtm ? '#1f6feb18' : 'transparent'};border-radius:4px"><div style="color:${isAtm ? '#e3b341' : '#c9d1d9'};font-weight:600;font-size:12px">${K}</div><div style="color:#565f6a;font-size:8px">${ce.iv != null ? (+ce.iv).toFixed(1) : ''}</div></div>
  <div style="position:relative;display:flex;align-items:center;gap:6px;padding:4px">
    ${_bsBtns(K, 'PE', pe.ltp)}
    <div style="position:absolute;top:3px;bottom:3px;left:44px;width:${pw}%;max-width:calc(100% - 46px);background:#3fb95016;border-radius:2px"></div>
    <span style="position:relative;color:${peSel ? '#e6edf3' : '#adb6c0'}"><span style="color:#565f6a;font-size:9px">${_oiK(pe.oi)}</span> ${_n2(pe.ltp)}</span></div>
  <div style="text-align:center;color:#565f6a;font-size:9px">${pd}</div>
</div>`;
          if (isAtm && spot != null) {
            html += `<div style="height:0;border-top:1px dashed #e3b341;position:relative"><span style="position:absolute;right:8px;top:-7px;background:#161b22;color:#e3b341;font-size:8px;padding:0 4px">spot ${(+spot).toFixed(0)}</span></div>`;
          }
        });
        // freshness note
        if (C.snap_dt) html += `<div style="font-size:8px;color:#484f58;text-align:right;padding:3px 8px">OI/IV/&Delta; @ ${String(C.snap_dt).slice(11, 16)} · LTP live</div>`;
        box.innerHTML = html;
      };

      window.qoChainClick = (K, ot, side) => {
        const key = K + '|' + ot;
        window.qoSel[key] = (window.qoSel[key] === side) ? undefined : side;
        if (!window.qoSel[key]) delete window.qoSel[key];
        qoRenderChain(); qoSummary();
      };
      window.qoClearLegs = () => { window.qoSel = {}; qoRenderChain(); qoSummary(); };

      // selected legs → chips + net/margin preview + action button label
      const _selList = () => {
        // [{K, ot, side, off}] using signed offset from the chain row
        const out = [];
        Object.keys(window.qoSel).forEach(key => {
          const [ks, ot] = key.split('|');
          const K = parseInt(ks), side = window.qoSel[key];
          const row = (window.qoChain.rows || []).find(r => r.strike === K);
          if (!row) return;
          const off = ot === 'CE' ? row.off_ce : row.off_pe;
          out.push({ K, ot, side, off });
        });
        return out;
      };
      let _qoNetTok = 0;
      window.qoSummary = () => {
        const legs = _selList();
        const box = document.getElementById('qo-legs');
        const clear = document.getElementById('qo-clear');
        if (box) box.innerHTML = legs.length ? legs.map(l => {
          const col = l.side === 'B' ? '#3fb950' : '#f85149';
          return `<span style="display:inline-flex;align-items:center;gap:5px;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:3px 8px;font-size:10px;color:#e6edf3"><b style="color:${col}">${l.side === 'B' ? 'BUY' : 'SELL'}</b> ${l.K} ${l.ot} <span onclick="qoChainClick(${l.K},'${l.ot}','${l.side}')" style="color:#6e7681;cursor:pointer;font-size:11px">&#x2715;</span></span>`;
        }).join('') : '<span style="color:#565f6a;font-size:10px">row pe hover &rarr; strike ke paas B / S dabao</span>';
        const lc = document.getElementById('qo-legcount'); if (lc) lc.textContent = legs.length ? `(${legs.length} leg${legs.length > 1 ? 's' : ''})` : '';
        if (clear) clear.style.display = legs.length ? '' : 'none';
        // net credit + margin: any selection (single or multi). Reuse the straddle
        // preview (signed=true) — same batched-LTP + basket-margin engine.
        const netrow = document.getElementById('qo-netrow');
        const lots = parseInt(document.getElementById('qo-lots')?.value) || 1;
        if (netrow) netrow.style.display = legs.length ? 'flex' : 'none';
        if (legs.length) {
          const tok = ++_qoNetTok;
          fetch('/api/auto-straddle/preview', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: qoSym, lots, signed: true, expiry: qoExpiry,
              legs: legs.map(l => ({ side: l.side === 'B' ? 'BUY' : 'SELL', opt_type: l.ot, offset: l.off })) })
          }).then(r => r.json()).then(d => {
            if (tok !== _qoNetTok) return;
            const cr = document.getElementById('qo-cr'), mg = document.getElementById('qo-mg');
            if (cr && d.ok && d.net_credit_total != null) {
              cr.innerHTML = `${d.net_credit_total >= 0 ? '+' : ''}${_qoInr(d.net_credit_total)}` + (d.net_credit != null ? ` <span style="color:#565f6a;font-size:10px">(${d.net_credit.toFixed(1)}pt)</span>` : '');
              cr.style.color = d.net_credit_total >= 0 ? '#3fb950' : '#f85149';
            } else if (cr) { cr.textContent = '—'; }
            if (mg) mg.innerHTML = (d.ok && d.margin != null) ? `<b>${_qoInr(d.margin)}</b>` : '—';
          }).catch(() => { });
        }
        // action button
        const act = document.getElementById('qo-act'), hint = document.getElementById('qo-hint');
        if (!act) return;
        if (qoTab === 'order') {
          if (legs.length === 1) {
            const only = legs[0].side;
            act.textContent = (only === 'B' ? 'BUY ' : 'SELL ') + legs[0].K + ' ' + legs[0].ot;
            act.style.background = only === 'S' ? '#f85149' : '#238636';
            if (hint) hint.textContent = '1 leg = turant marketable order';
          } else if (legs.length > 1) {
            act.textContent = `Place ${legs.length} legs`;
            act.style.background = '#f0883e';
            if (hint) hint.textContent = 'multi-leg basket → Payoff Orders tab me';
          } else {
            act.textContent = 'Strike select karo'; act.style.background = '#238636';
            if (hint) hint.textContent = 'row pe hover → B (buy) / S (sell)';
          }
        } else {
          act.textContent = legs.length ? `Arm ${legs.length} leg trigger` : 'Strike select karo';
          act.style.background = '#8957e5';
          if (hint) hint.textContent = 'level cross → ye legs auto-fire (RMS-gated)';
        }
        act.style.opacity = legs.length ? '1' : '.5';
      };

      // ── TABS + mode-specific controls ────────────────────────────────────────
      window.qoTrigDir = null;
      window._qoTrigSpot = null;
      let _qoTrigTimer = null, _qoStradTimer = null;

      window.qoSetTab = tab => {
        qoTab = tab; window.qoTab = tab;
        const to = document.getElementById('qo-tab-order'), tt = document.getElementById('qo-tab-trigger');
        if (to) { to.style.background = tab === 'order' ? '#0d1117' : '#161b22'; to.style.borderColor = tab === 'order' ? '#1f6feb' : '#30363d'; to.style.color = tab === 'order' ? '#58a6ff' : '#8b949e'; }
        if (tt) { tt.style.background = tab === 'trigger' ? '#0d1117' : '#161b22'; tt.style.borderColor = tab === 'trigger' ? '#1f6feb' : '#30363d'; tt.style.color = tab === 'trigger' ? '#58a6ff' : '#8b949e'; }
        qoRenderModeCtl();
        qoSummary();
        if (tab === 'trigger') { qoRefreshTriggers(); if (!_qoTrigTimer) _qoTrigTimer = setInterval(qoRefreshTriggers, 2000); }
        else { clearInterval(_qoTrigTimer); _qoTrigTimer = null; }
      };

      window.qoRenderModeCtl = () => {
        const c = document.getElementById('qo-modectl'); if (!c) return;
        if (qoTab === 'order') {
          c.innerHTML = `<div style="display:flex;gap:8px">
  <div style="flex:1"><div style="font-size:9px;color:#6e7681;margin-bottom:4px">TARGET (pt) <span style="color:#565f6a">opt</span></div><input id="qo-ord-tp" type="number" placeholder="—" step="1" style="width:100%;background:#0d1117;border:1px solid #1a7f37;border-radius:6px;color:#3fb950;padding:6px;font-size:12px;text-align:center;outline:none;box-sizing:border-box"></div>
  <div style="flex:1"><div style="font-size:9px;color:#6e7681;margin-bottom:4px">SL (pt) <span style="color:#565f6a">opt</span></div><input id="qo-ord-sl" type="number" placeholder="—" step="1" style="width:100%;background:#0d1117;border:1px solid #5c1a1f;border-radius:6px;color:#f85149;padding:6px;font-size:12px;text-align:center;outline:none;box-sizing:border-box"></div>
</div>`;
        } else {
          c.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">
    <span style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px">TRIGGER LEVEL</span>
    <span id="qo-trig-spot" style="font-size:10px;color:#58a6ff">spot —</span></div>
  <input id="qo-trig-level" type="number" step="0.05" placeholder="e.g. 24300" oninput="qoTrigDirAuto()" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:8px;font-size:14px;outline:none;box-sizing:border-box;margin-bottom:6px">
  <div style="display:flex;gap:6px;margin-bottom:6px">
    <button id="qo-dir-above" onclick="qoSetDir('above')" style="flex:1;padding:7px 0;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#8b949e;font-size:10px;font-weight:bold;cursor:pointer">&#9650; upar (&#8805;)</button>
    <button id="qo-dir-below" onclick="qoSetDir('below')" style="flex:1;padding:7px 0;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#8b949e;font-size:10px;font-weight:bold;cursor:pointer">&#9660; neeche (&#8804;)</button></div>
  <div style="display:flex;gap:8px">
    <div style="flex:1"><div style="font-size:9px;color:#6e7681;margin-bottom:4px">SL (pt)</div><input id="qo-trig-sl" type="number" step="0.5" value="20" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:6px;font-size:12px;outline:none;box-sizing:border-box"></div>
    <div style="flex:1"><div style="font-size:9px;color:#6e7681;margin-bottom:4px">TARGET (pt)</div><input id="qo-trig-tp" type="number" step="0.5" value="30" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:6px;font-size:12px;outline:none;box-sizing:border-box"></div></div>`;
          qoRefreshTriggers();
        }
      };

      window.qoSetDir = dir => {
        window.qoTrigDir = dir;
        const a = document.getElementById('qo-dir-above'), b = document.getElementById('qo-dir-below');
        if (a) { const on = dir === 'above'; a.style.background = on ? '#238636' : '#21262d'; a.style.borderColor = on ? '#3fb950' : '#30363d'; a.style.color = on ? '#fff' : '#8b949e'; }
        if (b) { const on = dir === 'below'; b.style.background = on ? '#da3633' : '#21262d'; b.style.borderColor = on ? '#f85149' : '#30363d'; b.style.color = on ? '#fff' : '#8b949e'; }
      };
      window.qoTrigDirAuto = () => {
        if (window.qoTrigDir) return;
        const lvl = parseFloat(document.getElementById('qo-trig-level')?.value);
        const spot = window._qoTrigSpot;
        if (!lvl || spot == null) return;
        const dir = lvl > spot ? 'above' : 'below';
        const a = document.getElementById('qo-dir-above'), b = document.getElementById('qo-dir-below');
        if (a) { const on = dir === 'above'; a.style.borderColor = on ? '#3fb950' : '#30363d'; a.style.color = on ? '#3fb950' : '#8b949e'; }
        if (b) { const on = dir === 'below'; b.style.borderColor = on ? '#f85149' : '#30363d'; b.style.color = on ? '#f85149' : '#8b949e'; }
      };

      // ── ACTION — Order (single/basket) or Trigger (arm) ──────────────────────
      window.qoAct = async () => {
        const legs = _selList();
        if (!legs.length) return;
        const st = document.getElementById('qo-status');
        const lots = parseInt(document.getElementById('qo-lots')?.value) || 1;
        if (qoTab === 'trigger') return qoArmTrigger(legs, lots, st);
        if (legs.length === 1) return qoFireSingle(legs[0], lots, st);
        return qoFireBasket(legs, lots, st);
      };

      async function qoFireSingle(leg, lots, st) {
        const side = leg.side === 'B' ? 'BUY' : 'SELL';
        if (qoMode === 'live' && !confirm(`LIVE ${side} ${lots}L ${leg.K} ${leg.ot} — real order! Confirm?`)) return;
        if (st) { st.textContent = `${side} ${leg.K} ${leg.ot}…`; st.style.color = '#d29922'; }
        try {
          const r = await fetch('/api/manual-order', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: qoSym, side, opt_type: leg.ot, lots,
              strike_offset: leg.off, mode: qoMode, broker: qoBroker, order_type: 'MARKET' })
          });
          const j = await r.json();
          if (st) { st.textContent = j.msg || (j.ok ? 'Done' : 'Error'); st.style.color = j.ok ? '#3fb950' : '#f85149'; }
          if (j.ok) { window.qoSel = {}; qoRenderChain(); qoSummary(); }
        } catch (e) { if (st) { st.textContent = 'Network error'; st.style.color = '#f85149'; } }
      }

      async function qoFireBasket(legs, lots, st) {
        const tp = parseFloat(document.getElementById('qo-ord-tp')?.value) || 30;
        const sl = parseFloat(document.getElementById('qo-ord-sl')?.value) || 30;
        const desc = legs.map(l => (l.side === 'B' ? 'B' : 'S') + ' ' + l.K + l.ot).join(', ');
        if (!confirm(`${qoSym} ${legs.length}-leg basket — ${lots} lot (paper)\n${desc}\ntarget ${tp} / SL ${sl}?`)) return;
        if (st) { st.textContent = 'basket place ho raha…'; st.style.color = '#d29922'; }
        try {
          const r = await fetch('/api/chain/fire-basket', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: qoSym, lots, tp_pt: tp, sl_pt: sl, expiry: qoExpiry,
              legs: legs.map(l => ({ side: l.side === 'B' ? 'BUY' : 'SELL', opt_type: l.ot, offset: l.off })) })
          });
          const j = await r.json();
          if (st) { st.textContent = j.msg || (j.ok ? 'placed' : 'fail'); st.style.color = j.ok ? '#3fb950' : '#f85149'; }
          if (j.ok) { window.qoSel = {}; qoRenderChain(); qoSummary(); qoRefreshStraddles(); }
        } catch (e) { if (st) { st.textContent = 'fail: ' + e; st.style.color = '#f85149'; } }
      }

      async function qoArmTrigger(legs, lots, st) {
        const level = parseFloat(document.getElementById('qo-trig-level')?.value);
        if (!level || level <= 0) { if (st) { st.textContent = 'Level daalo (NIFTY price)'; st.style.color = '#f85149'; } return; }
        let dir = window.qoTrigDir;
        if (!dir && window._qoTrigSpot != null) dir = level > window._qoTrigSpot ? 'above' : 'below';
        const slPt = parseFloat(document.getElementById('qo-trig-sl')?.value);
        const tpPt = parseFloat(document.getElementById('qo-trig-tp')?.value);
        if (st) { st.textContent = 'Arming…'; st.style.color = '#d29922'; }
        let armed = 0, fail = '';
        for (const l of legs) {
          try {
            const r = await fetch('/api/triggers', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ symbol: qoSym, level, direction: dir, opt_type: l.ot,
                side: l.side === 'B' ? 'BUY' : 'SELL', lots, offset: l.off, mode: qoMode, broker: qoBroker,
                sl_pt: isFinite(slPt) ? slPt : 20, tp_pt: isFinite(tpPt) ? tpPt : 30 })
            });
            const j = await r.json();
            if (j.ok) armed++; else fail = j.msg || 'fail';
          } catch (e) { fail = 'network'; }
        }
        if (st) {
          st.textContent = armed ? `Armed ${armed} leg${armed > 1 ? 's' : ''}` : (fail || 'fail');
          st.style.color = armed ? '#3fb950' : '#f85149';
        }
        if (armed) {
          window.qoSel = {}; qoRenderChain(); qoSummary();
          const lv = document.getElementById('qo-trig-level'); if (lv) lv.value = '';
          window.qoTrigDir = null; qoRefreshTriggers();
        }
      }

      async function qoRefreshTriggers() {
        if (document.hidden) return;
        if (panel.style.display === 'none') return;
        if (qoTab !== 'trigger') return;
        try {
          const r = await fetch('/api/triggers?symbol=' + encodeURIComponent(qoSym));
          const j = await r.json();
          const spot = (j.spot || {})[qoSym];
          if (spot != null) window._qoTrigSpot = spot;
          const se = document.getElementById('qo-trig-spot');
          if (se) se.textContent = (window._qoTrigSpot != null) ? (qoSym + ' ' + window._qoTrigSpot.toFixed(1)) : 'spot —';
          qoTrigDirAuto();
        } catch (e) { }
      }

      window.qoCancelTrigger = async tid => {
        try { await fetch('/api/triggers/' + encodeURIComponent(tid), { method: 'DELETE' }); qoRefreshTriggers(); }
        catch (e) { }
      };

      // ── TRIGGERS + CHART — armed levels drawn on a NIFTY line-chart (Orders tab) ──
      let _qoTcTimer = null, _qoTcCandles = null, _qoTcChart = null, _qoTcSeries = null, _qoTcLines = [];
      function qoDestroyTcChart() {
        if (_qoTcChart) { try { _qoTcChart.remove(); } catch (e) { } }
        _qoTcChart = null; _qoTcSeries = null; _qoTcLines = [];
        const el = document.getElementById('qo-tc-chart'); if (el) el.innerHTML = '';
      }
      window.qoOpenTrigChart = () => {
        let m = document.getElementById('qo-tc-modal');
        if (!m) {
          m = document.createElement('div');
          m.id = 'qo-tc-modal';
          Object.assign(m.style, { position: 'fixed', inset: '0', background: 'rgba(1,4,9,0.72)', zIndex: '10000', display: 'flex', alignItems: 'center', justifyContent: 'center' });
          m.innerHTML = `
<div style="width:920px;max-width:95vw;background:#161b22;border:1px solid #30363d;border-radius:12px;padding:16px;font-family:monospace;box-shadow:0 8px 40px rgba(0,0,0,.6)">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <span style="color:#58a6ff;font-size:13px;font-weight:bold;letter-spacing:1px">&#127919; TRIGGERS + CHART &middot; <span id="qo-tc-sym">NIFTY</span></span>
    <div style="display:flex;align-items:center;gap:10px">
      <button onclick="qoTcReset()" title="Zoom/pan reset" style="background:#21262d;border:1px solid #30363d;border-radius:5px;color:#adbac7;font-size:11px;font-weight:bold;padding:4px 10px;cursor:pointer">&#8635; Reset View</button>
      <span onclick="qoCloseTrigChart()" style="color:#8b949e;font-size:18px;cursor:pointer;line-height:1">&#x2715;</span>
    </div>
  </div>
  <div id="qo-tc-chart" style="background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:8px;margin-bottom:6px;min-height:452px"></div>
  <div style="font-size:9px;color:#484f58;text-align:center;margin-bottom:12px">NIFTY 1-min candles &middot; blue = live spot &middot; green &#9650; upar-cross &middot; red &#9660; neeche-cross &middot; zoom/pan preserve rehta hai</div>
  <div id="qo-tc-list" style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px"></div>
</div>`;
          m.addEventListener('click', e => { if (e.target === m) qoCloseTrigChart(); });
          document.body.appendChild(m);
        }
        document.getElementById('qo-tc-sym').textContent = qoSym;
        m.style.display = 'flex';
        _qoTcCandles = null;
        qoDestroyTcChart();
        qoLoadTrigChart();
        clearInterval(_qoTcTimer); _qoTcTimer = setInterval(qoLoadTrigChart, 6000);
      };
      window.qoCloseTrigChart = () => {
        const m = document.getElementById('qo-tc-modal'); if (m) m.style.display = 'none';
        clearInterval(_qoTcTimer); _qoTcTimer = null;
        qoDestroyTcChart();
      };
      async function qoLoadTrigChart() {
        const chartEl = document.getElementById('qo-tc-chart'); if (!chartEl) return;
        try {
          if (!_qoTcCandles) {
            const cr = await fetch('/api/trade-chart-underlying-data?trad_sym=' + encodeURIComponent(qoSym)).then(r => r.json()).catch(() => ({}));
            _qoTcCandles = (cr && cr.candles) || [];
          }
          const tr = await fetch('/api/triggers?symbol=' + encodeURIComponent(qoSym)).then(r => r.json()).catch(() => ({}));
          const triggers = (tr && tr.triggers) || [];
          const spot = ((tr && tr.spot) || {})[qoSym];
          qoRenderTrigChart(_qoTcCandles, triggers, spot);
        } catch (e) { }
      }
      function qoRenderTrigChart(candles, triggers, spot) {
        const chartEl = document.getElementById('qo-tc-chart');
        const listEl = document.getElementById('qo-tc-list');
        if (listEl) {
          if (!triggers.length) {
            listEl.innerHTML = '<div style="grid-column:1/-1;font-size:11px;color:#6e7681;text-align:center;padding:10px">Koi armed trigger nahi — Quick Order se arm karo</div>';
          } else {
            listEl.innerHTML = triggers.map(t => {
              const above = t.direction === 'above';
              const armed = t.status === 'armed';
              const col = armed ? (above ? '#3fb950' : '#f85149') : (t.status === 'fired' ? '#58a6ff' : '#d29922');
              const arrow = above ? '▲' : '▼', cmp = above ? '≥' : '≤';
              const dist = (t.dist != null && armed) ? `<span style="color:#d29922">${Math.abs(t.dist).toFixed(0)} pts door</span>` : `<span style="color:${col}">${t.status}</span>`;
              return `<div style="background:#0d1117;border:1px solid #21262d;border-left:3px solid ${col};border-radius:0 6px 6px 0;padding:7px 9px">
                <div style="display:flex;justify-content:space-between;align-items:center">
                  <span style="font-size:12px;font-weight:bold;color:#e6edf3">${t.symbol} ${cmp} ${(+t.level).toFixed(0)} <span style="color:${col}">${arrow}</span></span>
                  <span onclick="qoCancelTrigger('${t.id}');qoLoadTrigChart()" title="Cancel" style="color:#8b949e;font-size:13px;cursor:pointer">&#x2715;</span>
                </div>
                <div style="font-size:9px;color:#8b949e;margin-top:3px">${t.side} ${t.lots}L ${t.opt_type} (off ${t.offset}) &middot; ${(t.mode || '').toUpperCase()}/${(t.broker || '').toUpperCase()} &middot; ${dist}</div>
              </div>`;
            }).join('');
          }
        }
        if (!chartEl) return;
        if (!window.LightweightCharts) { chartEl.innerHTML = '<div style="font-size:12px;color:#6e7681;text-align:center;padding:180px 0">chart library load nahi hui</div>'; return; }
        if (!_qoTcChart) {
          const ohlc = (candles || []).filter(c => c && c.time != null && c.close != null)
            .map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }));
          if (!ohlc.length) { chartEl.innerHTML = '<div style="font-size:12px;color:#6e7681;text-align:center;padding:180px 0">Chart data nahi — market band ya data missing</div>'; return; }
          chartEl.innerHTML = '';
          chartEl.style.height = '440px';
          _qoTcChart = LightweightCharts.createChart(chartEl, {
            width: chartEl.clientWidth || 800, height: 440,
            layout: { background: { color: '#0d1117' }, textColor: '#8b949e' },
            grid: { vertLines: { color: '#161b22' }, horzLines: { color: '#161b22' } },
            timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#30363d' },
            rightPriceScale: { borderColor: '#30363d' },
            crosshair: { mode: 0 }
          });
          _qoTcSeries = _qoTcChart.addCandlestickSeries({ upColor: '#3fb950', downColor: '#f85149', borderVisible: false, wickUpColor: '#3fb950', wickDownColor: '#f85149' });
          _qoTcSeries.setData(ohlc);
          _qoTcChart.timeScale().fitContent();
        }
        if (!_qoTcSeries) return;
        _qoTcLines.forEach(l => { try { _qoTcSeries.removePriceLine(l); } catch (e) { } });
        _qoTcLines = [];
        if (spot != null) _qoTcLines.push(_qoTcSeries.createPriceLine({ price: +spot, color: '#58a6ff', lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: 'spot' }));
        (triggers || []).forEach(t => {
          const lv = +t.level; if (isNaN(lv)) return;
          const above = t.direction === 'above';
          const armed = t.status === 'armed';
          const col = armed ? (above ? '#3fb950' : '#f85149') : '#8b949e';
          _qoTcLines.push(_qoTcSeries.createPriceLine({ price: lv, color: col, lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: lv.toFixed(0) + ' ' + (above ? '▲' : '▼') }));
        });
      }
      window.qoTcReset = () => { if (_qoTcChart) { try { _qoTcChart.timeScale().fitContent(); } catch (e) { } } };

      // ── AUTO STRADDLE (9:20 / alert) — preserved, collapsible ────────────────
      window.qoAutoToggle = () => {
        const b = document.getElementById('qo-auto-body'), c = document.getElementById('qo-auto-caret');
        if (!b) return;
        const open = b.style.display === 'none';
        b.style.display = open ? '' : 'none';
        if (c) c.innerHTML = open ? '&#9662;' : '&#9656;';
        if (open) { qoStradCfgLoad(); qoRefreshStraddles(); if (!_qoStradTimer) _qoStradTimer = setInterval(qoRefreshStraddles, 4000); }
        else { clearInterval(_qoStradTimer); _qoStradTimer = null; }
      };
      window.qoRefreshStraddles = async () => {
        if (document.hidden) return;
        const box = document.getElementById('qo-strad-list'); if (!box) return;
        const lbl = document.getElementById('qo-strad-sym-lbl'); if (lbl) lbl.textContent = qoSym || 'NIFTY';
        try {
          const d = await (await fetch('/api/auto-straddle/list')).json();
          const rows = d.straddles || [];
          if (!rows.length) { box.innerHTML = '<span style="color:#484f58">koi straddle nahi</span>'; return; }
          box.innerHTML = rows.map(s => {
            const open = s.status === 'open', pl = s.profit_pt;
            const plc = pl == null ? '#8b949e' : (pl >= 0 ? '#3fb950' : '#f85149');
            const stc = s.status === 'target' ? '#3fb950' : (s.status === 'sl' ? '#f85149' : (open ? '#58a6ff' : '#8b949e'));
            return `<div style="background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:6px 8px;margin-bottom:5px">
              <div style="display:flex;justify-content:space-between;align-items:center"><span style="font-weight:bold;color:#adbac7">${s.symbol} <span style="color:#6e7681;font-weight:400">${s.source || ''}</span></span><span style="font-size:9px;font-weight:bold;color:${stc}">${(s.status || '').toUpperCase()}</span></div>
              <div style="font-size:10px;color:#8b949e;margin-top:2px">credit ${Math.round(s.entry_credit)}${s.live_credit != null ? (' → ' + Math.round(s.live_credit)) : ''} ${pl != null ? `<span style="color:${plc};font-weight:bold">(${pl >= 0 ? '+' : ''}${pl}pt)</span>` : ''}</div>
              <div style="display:flex;gap:6px;margin-top:5px"><button onclick="window.open('/straddle-chart?id=${s.id}','_blank')" style="flex:1;padding:4px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#adbac7;font-size:10px;cursor:pointer">📈 Chart</button>${open ? `<button onclick="qoCloseStraddle('${s.id}')" style="flex:1;padding:4px;background:#3d1418;border:1px solid #5c1a1f;border-radius:5px;color:#f85149;font-size:10px;cursor:pointer">✕ Close</button>` : ''}</div>
            </div>`;
          }).join('');
        } catch (e) { box.innerHTML = '<span style="color:#f85149">list fail</span>'; }
      };
      window.qoCloseStraddle = async (id) => {
        if (!confirm('Straddle ke dono leg square off karein?')) return;
        try { await fetch('/api/auto-straddle/close', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) }); } catch (e) { }
        qoRefreshStraddles();
      };
      window.qoStradCfgLoad = async () => {
        try {
          const c = (await (await fetch('/api/auto-straddle/config')).json()).cfg || {};
          const sym = qoSym || 'NIFTY';
          const a = document.getElementById('qo-strad-920'); if (a) a.checked = !!c.enabled_920;
          const b = document.getElementById('qo-strad-alert'); if (b) b.checked = !!c.enabled_alert;
          const ps = (c.per_symbol || {})[sym] || {};
          const tp = document.getElementById('qo-strad-tp'); if (tp) tp.value = ps.tp_pt != null ? ps.tp_pt : (c.tp_pt || 30);
          const sl = document.getElementById('qo-strad-sl'); if (sl) sl.value = ps.sl_pt != null ? ps.sl_pt : (c.sl_pt || 30);
          const lo = document.getElementById('qo-strad-lots'); if (lo && c.lots) lo.value = c.lots;
          const hg = c.hedge || {};
          const he = document.getElementById('qo-strad-hedge'); if (he) he.checked = hg.enabled !== false;
          const hm = document.getElementById('qo-strad-hedgemax'); if (hm && ps.hedge_max_premium != null) hm.value = ps.hedge_max_premium;
          const lbl = document.getElementById('qo-strad-sym-lbl'); if (lbl) lbl.textContent = sym;
        } catch (e) { }
      };
      window.qoStradCfgSave = async () => {
        const g = id => document.getElementById(id);
        try {
          await fetch('/api/auto-straddle/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
            symbol: qoSym || 'NIFTY',
            enabled_920: !!g('qo-strad-920')?.checked, enabled_alert: !!g('qo-strad-alert')?.checked,
            lots: parseInt(g('qo-strad-lots')?.value) || 1, tp_pt: parseFloat(g('qo-strad-tp')?.value) || 30, sl_pt: parseFloat(g('qo-strad-sl')?.value) || 30,
            hedge_enabled: !!g('qo-strad-hedge')?.checked, hedge_max_premium: parseFloat(g('qo-strad-hedgemax')?.value) || 2,
          }) });
        } catch (e) { }
      };

      // initial paint
      qoRenderModeCtl();
      qoSetMode('paper'); qoSetBroker('dhan');

      // chain auto-refresh (Order + Trigger both use it); skips when hidden/closed
      _qoChainTimer = setInterval(qoLoadChain, 4000);
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
