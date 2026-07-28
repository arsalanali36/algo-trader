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
<div style="display:flex;background:#0d1117;border-radius:8px;padding:3px;margin-bottom:14px;gap:3px">
  <button id="qo-tab-instant" onclick="qoSetTab('instant')" style="flex:1;padding:7px 0;border:none;border-radius:6px;font-size:11px;font-weight:bold;cursor:pointer;background:#21262d;color:#e6edf3">&#9889; Instant</button>
  <button id="qo-tab-trigger" onclick="qoSetTab('trigger')" style="flex:1;padding:7px 0;border:none;border-radius:6px;font-size:11px;font-weight:bold;cursor:pointer;background:transparent;color:#8b949e">&#127919; Trigger</button>
  <button id="qo-tab-straddle" onclick="qoSetTab('straddle')" style="flex:1;padding:7px 0;border:none;border-radius:6px;font-size:11px;font-weight:bold;cursor:pointer;background:transparent;color:#8b949e">&#129651; Straddle</button>
</div>
<div style="display:flex;gap:10px;margin-bottom:14px">
  <div style="flex:1">
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">MODE</div>
    <div style="display:flex;background:#0d1117;border-radius:7px;padding:3px;gap:3px">
      <button id="qo-paper" onclick="qoSetMode('paper')" style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:#d29922;color:#0d1117">PAPER</button>
      <button id="qo-live"  onclick="qoSetMode('live')"  style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:transparent;color:#8b949e">LIVE</button>
    </div>
  </div>
  <div style="flex:1">
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">ACCOUNT</div>
    <div style="display:flex;background:#0d1117;border-radius:7px;padding:3px;gap:3px">
      <button id="qo-broker-dhan" onclick="qoSetBroker('dhan')" style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:#1f6feb;color:#fff">DHAN</button>
      <button id="qo-broker-kite" onclick="qoSetBroker('kite')" style="flex:1;padding:5px 0;border:none;border-radius:5px;font-size:10px;font-weight:bold;cursor:pointer;background:transparent;color:#8b949e">ZERODHA</button>
    </div>
  </div>
</div>
<div style="border-top:1px solid #21262d;padding-top:12px">
<div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">INDEX</div>
<div style="display:flex;gap:6px;margin-bottom:14px">
  <button id="qo-sym-NIFTY"     onclick="qoSetSym('NIFTY')"     style="flex:1;padding:7px 0;border:1px solid #1f6feb;border-radius:6px;background:#1f6feb22;color:#58a6ff;font-size:12px;font-weight:bold;cursor:pointer">NIFTY</button>
  <button id="qo-sym-BANKNIFTY" onclick="qoSetSym('BANKNIFTY')" style="flex:1;padding:7px 0;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#8b949e;font-size:12px;font-weight:bold;cursor:pointer">BANKNIFTY</button>
</div>
</div>
<div id="qo-lotsrow" style="display:flex;gap:12px;margin-bottom:12px;align-items:flex-end">
  <div style="flex:1.4;min-width:0">
    <div id="qo-trig-block" style="display:none">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">
        <span style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px">TRIGGER LEVEL</span>
        <span id="qo-trig-spot" style="font-size:10px;color:#58a6ff">spot —</span>
      </div>
      <input id="qo-trig-level" type="number" step="0.05" placeholder="e.g. 24300" oninput="qoTrigDirAuto()" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:8px;font-size:14px;outline:none;box-sizing:border-box">
    </div>
    <div id="qo-price-block">
      <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">LIMIT PRICE <span style="color:#484f58;font-weight:400">(blank=LTP)</span></div>
      <input id="qo-price" type="number" step="0.05" placeholder="auto (LTP)" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:8px;font-size:14px;outline:none;box-sizing:border-box">
    </div>
  </div>
  <div style="flex:1;min-width:0">
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:5px">LOTS</div>
    <div style="display:flex;align-items:center;gap:6px">
      <input id="qo-lots" type="number" value="3" min="1" oninput="updateQtyHint()" style="width:44px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:8px 4px;font-size:14px;text-align:center;outline:none">
      <span style="font-size:11px;color:#6e7681;white-space:nowrap"><span id="qo-qty-num" style="color:#adbac7;font-weight:bold">195</span> qty<span id="qo-ls" style="display:none">65</span></span>
    </div>
  </div>
</div>
<div id="qo-trig-dir" style="display:none;margin-bottom:12px">
  <div style="display:flex;gap:6px">
    <button id="qo-dir-above" onclick="qoSetDir('above')" style="flex:1;padding:7px 0;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#8b949e;font-size:10px;font-weight:bold;cursor:pointer">&#9650; upar (&#8805;)</button>
    <button id="qo-dir-below" onclick="qoSetDir('below')" style="flex:1;padding:7px 0;border:1px solid #30363d;border-radius:6px;background:#21262d;color:#8b949e;font-size:10px;font-weight:bold;cursor:pointer">&#9660; neeche (&#8804;)</button>
  </div>
</div>
<div id="qo-atmhdr" style="border-top:1px solid #21262d;padding-top:12px;font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:6px">STRIKE OFFSET (ATM)</div>
<div id="qo-atm-row" style="display:flex;gap:3px;margin-bottom:12px">
  ${[-3, -2, -1, 0, 1, 2, 3].map(v => `<button onclick="qoSetAtm(${v})" data-atm="${v}" style="flex:1;padding:5px 0;border:1px solid ${v === 0 ? '#1f6feb' : '#30363d'};border-radius:5px;background:${v === 0 ? '#1f6feb' : '#21262d'};color:${v === 0 ? '#fff' : '#8b949e'};font-size:10px;cursor:pointer">${v === 0 ? 'ATM' : v > 0 ? '+' + v : v}</button>`).join('')}
</div>
<div id="qo-leghdr" style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:6px">OPTION &middot; SELECT LEG</div>
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
<div id="qo-instant-actions">
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:6px">
  <button onclick="qoOrder('BUY')"  style="padding:11px;background:#3fb950;border:none;border-radius:6px;color:#fff;font-size:14px;font-weight:bold;cursor:pointer">BUY <span id="qo-buy-leg" style="font-size:11px;opacity:.85">CE</span></button>
  <button onclick="qoOrder('SELL')" style="padding:11px;background:#f85149;border:none;border-radius:6px;color:#fff;font-size:14px;font-weight:bold;cursor:pointer">SELL <span id="qo-sell-leg" style="font-size:11px;opacity:.85">CE</span></button>
</div>
<div id="qo-leghint" style="font-size:9px;color:#6e7681;text-align:center;margin-bottom:8px">jo <b id="qo-leg-hint" style="color:#adbac7">CE</b> select hai usi pe BUY/SELL chalega</div>
</div>
<div id="qo-trigger-actions" style="display:none">
<div style="display:flex;gap:6px;margin-bottom:8px">
  <div style="flex:1">
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:4px">SL (pt)</div>
    <input id="qo-trig-sl" type="number" step="0.5" value="20" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:7px;font-size:13px;outline:none;box-sizing:border-box">
  </div>
  <div style="flex:1">
    <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:4px">TARGET (pt)</div>
    <input id="qo-trig-tp" type="number" step="0.5" value="30" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:7px;font-size:13px;outline:none;box-sizing:border-box">
  </div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:6px">
  <button onclick="qoArm('BUY')"  style="padding:11px;background:#238636;border:none;border-radius:6px;color:#fff;font-size:13px;font-weight:bold;cursor:pointer">Arm BUY <span id="qo-arm-buy-leg" style="font-size:11px;opacity:.85">CE</span></button>
  <button onclick="qoArm('SELL')" style="padding:11px;background:#da3633;border:none;border-radius:6px;color:#fff;font-size:13px;font-weight:bold;cursor:pointer">Arm SELL <span id="qo-arm-sell-leg" style="font-size:11px;opacity:.85">CE</span></button>
</div>
<div style="font-size:9px;color:#6e7681;text-align:center;margin-bottom:8px">fire = marketable-limit &middot; RMS-gated &middot; auto SL/target &middot; armed list → Orders &middot; 🎯 Triggers</div>
</div>
<div id="qo-straddle-block" style="display:none">
  <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:6px">STRADDLE / MULTI-LEG (paper) &middot; target/SL = combined NET credit</div>
  <div style="display:flex;gap:8px;margin-bottom:10px">
    <div style="flex:1"><div style="font-size:9px;color:#6e7681;margin-bottom:4px">LOTS</div><input id="qo-strad-lots" type="number" value="1" min="1" onchange="qoStradCfgSave();qoStradPreview()" style="width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;padding:7px;font-size:13px;text-align:center;outline:none;box-sizing:border-box"></div>
    <div style="flex:1"><div style="font-size:9px;color:#3fb950;margin-bottom:4px">TARGET (pt)</div><input id="qo-strad-tp" type="number" value="30" step="1" onchange="qoStradCfgSave()" style="width:100%;background:#0d1117;border:1px solid #1a7f37;border-radius:6px;color:#3fb950;padding:7px;font-size:13px;text-align:center;outline:none;box-sizing:border-box"></div>
    <div style="flex:1"><div style="font-size:9px;color:#f85149;margin-bottom:4px">SL (pt)</div><input id="qo-strad-sl" type="number" value="30" step="1" onchange="qoStradCfgSave()" style="width:100%;background:#0d1117;border:1px solid #5c1a1f;border-radius:6px;color:#f85149;padding:7px;font-size:13px;text-align:center;outline:none;box-sizing:border-box"></div>
  </div>
  <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:4px 0 3px;margin-bottom:10px">
    <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 10px;gap:6px">
      <span style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.5px;flex:1">LEGS &mdash; SELL/BUY strike +/&minus;</span>
      <button onclick="qoStradGexWalls()" title="SELL ko GEX call-wall (CE) / put-wall (PE) pe set karo" style="font-size:9px;padding:3px 7px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#8b949e;cursor:pointer;white-space:nowrap">&#128202; GEX</button>
      <button onclick="qoStradAtmReset()" title="SELL wapas ATM pe" style="font-size:9px;padding:3px 7px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#8b949e;cursor:pointer;white-space:nowrap">&#9678; ATM</button>
      <label style="display:flex;align-items:center;gap:4px;font-size:10px;color:#8b949e;cursor:pointer" title="dono BUY wings ka offset barabar rakho"><input type="checkbox" id="qo-strad-sym" checked onchange="qoStradSym()" style="accent-color:#1f6feb;width:12px;height:12px"> sym</label>
    </div>
    <div id="qo-strad-legs"></div>
    <div id="qo-strad-warn" style="display:none;padding:2px 10px 5px;font-size:10px;color:#d29922"></div>
    <div style="display:flex;justify-content:space-between;padding:7px 10px 3px;border-top:1px dashed #30363d;margin-top:3px">
      <span style="font-size:10px;color:#8b949e">Net credit &times; <span id="qo-strad-lotslbl">1</span> lot</span>
      <span id="qo-strad-net" style="font-size:12px;color:#3fb950">&mdash;</span>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;padding:0 10px 7px">
      <span id="qo-strad-marlbl" style="font-size:10px;color:#d29922">&#128176; Margin (hedged)</span>
      <span style="text-align:right"><span id="qo-strad-mar" style="font-size:12px;color:#e6edf3">&mdash;</span><span id="qo-strad-marlot" style="font-size:9px;color:#8b949e;display:block"></span></span>
    </div>
  </div>
  <button onclick="qoSellStraddle()" style="width:100%;padding:11px;background:#f85149;border:none;border-radius:6px;color:#fff;font-size:14px;font-weight:bold;cursor:pointer;margin-bottom:6px">Place selected legs <span style="font-size:11px;opacity:.85">(<span id="qo-strad-legcount">4</span>-leg)</span></button>
  <div style="font-size:9px;color:#6e7681;text-align:center;margin-bottom:8px">&#9745; leg = banegi &middot; SELL/BUY +/&minus; strike &middot; GEX=walls, ATM=reset &middot; RMS-gated &middot; PAPER</div>
  <div style="border-top:1px solid #21262d;padding-top:10px;font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:6px">AUTO (paper) &middot; ATM+hedge auto-pick</div>
  <label style="display:flex;align-items:center;gap:7px;font-size:11px;color:#adbac7;margin-bottom:5px;cursor:pointer"><input type="checkbox" id="qo-strad-920" onchange="qoStradCfgSave()"> 9:20 auto &middot; NIFTY + BANKNIFTY (roz)</label>
  <label style="display:flex;align-items:center;gap:7px;font-size:11px;color:#adbac7;margin-bottom:5px;cursor:pointer"><input type="checkbox" id="qo-strad-alert" onchange="qoStradCfgSave()"> Alert pe auto (straddle spike/crush/gamma)</label>
  <label style="display:flex;align-items:center;gap:6px;font-size:11px;color:#adbac7;margin-bottom:10px;cursor:pointer;flex-wrap:wrap"><input type="checkbox" id="qo-strad-hedge" onchange="qoStradCfgSave()"> &#128737; auto-hedge &mdash; sasti OTM wing (&le; &#8377;<input id="qo-strad-hedgemax" type="number" value="2" step="0.5" min="0.5" onchange="qoStradCfgSave()" style="width:42px;background:#0d1117;border:1px solid #30363d;border-radius:4px;color:#e6edf3;padding:3px;font-size:11px;text-align:center;outline:none">)</label>
  <div style="font-size:9px;color:#6e7681;font-weight:600;letter-spacing:.6px;margin-bottom:6px">ACTIVE / TODAY</div>
  <div id="qo-strad-list" style="font-size:11px;color:#8b949e">&mdash;</div>
</div>
<div id="qo-status" style="font-size:11px;color:#8b949e;text-align:center;min-height:16px">Mode: PAPER</div>`;

      Object.assign(panel.style, {
        position: 'fixed', top: '16px', right: '16px', width: '288px',
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
        if (window.feedPaused || document.hidden) return;   // background tab — skip Dhan LTP poll (load-trim)
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
        // straddle tab has NO CE/PE LTP box → skip the slow Dhan option-LTP fetch (instant switch);
        // just reload the per-index target/SL + label
        if (window.qoTab === 'straddle') { qoStradCfgLoad(); window.qoStradState.prev = {}; qoStradRenderLegs(); qoStradPreview(); }
        else { qoRefreshLtp(); }
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
        ['qo-buy-leg', 'qo-sell-leg', 'qo-leg-hint', 'qo-arm-buy-leg', 'qo-arm-sell-leg'].forEach(id => { const e = document.getElementById(id); if (e) { e.textContent = type; e.style.color = id === 'qo-leg-hint' ? (type === 'CE' ? '#3fb950' : '#f85149') : ''; } });
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

      // ── PRICE TRIGGER MODE ───────────────────────────────────────────────
      window.qoTab = 'instant';
      window.qoTrigDir = null;       // null = auto-suggest from spot vs level
      window._qoTrigSpot = null;
      let _qoTrigTimer = null;

      window.qoSetTab = tab => {
        window.qoTab = tab;
        const ti = document.getElementById('qo-tab-instant'), tt = document.getElementById('qo-tab-trigger'), ts = document.getElementById('qo-tab-straddle');
        if (ti) { ti.style.background = tab === 'instant' ? '#21262d' : 'transparent'; ti.style.color = tab === 'instant' ? '#e6edf3' : '#8b949e'; }
        if (tt) { tt.style.background = tab === 'trigger' ? '#1f6feb' : 'transparent'; tt.style.color = tab === 'trigger' ? '#fff' : '#8b949e'; }
        if (ts) { ts.style.background = tab === 'straddle' ? '#f85149' : 'transparent'; ts.style.color = tab === 'straddle' ? '#fff' : '#8b949e'; }
        const show = (id, on) => { const e = document.getElementById(id); if (e) e.style.display = on ? '' : 'none'; };
        const strad = tab === 'straddle';
        show('qo-trig-block', tab === 'trigger');
        show('qo-trig-dir', tab === 'trigger');
        show('qo-trigger-actions', tab === 'trigger');
        show('qo-instant-actions', tab === 'instant');
        show('qo-price-block', tab === 'instant');    // trigger fires marketable — no limit box
        show('qo-straddle-block', strad);
        // straddle sells BOTH ATM legs with its own lots — hide the leg/lots/offset UI
        show('qo-lotsrow', !strad);
        show('qo-atmhdr', !strad);
        show('qo-atm-row', !strad);
        show('qo-leghdr', !strad);
        show('qo-ltp-box', !strad);
        show('qo-leghint', !strad);
        const st = document.getElementById('qo-status');
        if (st && tab === 'trigger') { st.textContent = 'Trigger mode — level + direction do'; st.style.color = '#8b949e'; }
        if (st && strad) { st.textContent = 'Straddle — SELL ATM CE+PE, combined 30/30 (paper)'; st.style.color = '#8b949e'; }
        if (tab === 'trigger') { qoRefreshTriggers(); if (!_qoTrigTimer) _qoTrigTimer = setInterval(qoRefreshTriggers, 2000); }
        else { clearInterval(_qoTrigTimer); _qoTrigTimer = null; }
        if (strad) { qoStradCfgLoad(); qoStradRenderLegs(); qoStradPreview(); qoRefreshStraddles(); if (!window._qoStradTimer) window._qoStradTimer = setInterval(() => { qoRefreshStraddles(); qoStradPreview(); }, 3000); }
        else { clearInterval(window._qoStradTimer); window._qoStradTimer = null; }
      };

      const _qoInr = n => '₹' + Math.round(n).toLocaleString('en-IN');

      // ── Straddle / multi-leg builder (paper) — Quick Order "Straddle" tab ──
      // Each leg has its own checkbox (include/exclude); BUY wings pick strike by
      // OTM offset. Preview shows per-leg LTP + net credit + real hedged margin.
      window.qoStradState = {
        sym: true,
        atm: null, step: null,   // from last preview (scrip master) — strike computed client-side
        legs: [
          { key: 'ceS', side: 'SELL', ot: 'CE', off: 0, on: true },
          { key: 'peS', side: 'SELL', ot: 'PE', off: 0, on: true },
          { key: 'ceB', side: 'BUY',  ot: 'CE', off: 2, on: true },
          { key: 'peB', side: 'BUY',  ot: 'PE', off: 2, on: true },
        ],
        prev: {},   // key -> {strike, ltp}
      };
      const _qsStrike = l => {   // strike from ATM+step (instant on +/-, off-market safe) — CE up, PE down, both sides
        const S = window.qoStradState;
        if (S.atm == null || S.step == null) return (S.prev[l.key] || {}).strike || null;
        return l.ot === 'CE' ? S.atm + l.off * S.step : S.atm - l.off * S.step;
      };
      const _qsLeg = k => window.qoStradState.legs.find(l => l.key === k);
      const _qsEnabled = () => window.qoStradState.legs.filter(l => l.on);
      const _qsSpec = () => _qsEnabled().map(l => ({ side: l.side, opt_type: l.ot, offset: l.off }));

      window.qoStradRenderLegs = () => {
        const box = document.getElementById('qo-strad-legs'); if (!box) return;
        const S = window.qoStradState, pv = S.prev || {};
        box.innerHTML = S.legs.map(l => {
          const p = pv[l.key] || {}, on = l.on;
          const col = l.side === 'SELL' ? '#f85149' : '#3fb950';
          const stk = _qsStrike(l);
          const offlbl = l.off === 0 ? 'ATM' : ((l.ot === 'CE' ? '+' : '−') + l.off);
          const strike = stk != null ? (stk + ' ' + l.ot) : (l.ot + ' ' + offlbl);
          // last preview's LTP only applies if it was for THIS strike (else stale after a +/-)
          const ltp = (p.ltp != null && p.ltp > 0 && (p.strike == null || stk == null || p.strike === stk)) ? p.ltp.toFixed(2) : '—';
          // every leg (SELL + BUY) gets +/- steppers now — SELL can move to GEX walls, BUY sets the wing
          const steps = (
            `<button ${on ? '' : 'disabled'} onclick="qoStradStep('${l.key}',-1)" style="width:20px;height:20px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#e6edf3;cursor:pointer;font-size:12px;line-height:1;opacity:${on ? 1 : .35}">−</button>` +
            `<span style="font-size:10px;color:#8b949e;width:30px;text-align:center">${offlbl}</span>` +
            `<button ${on ? '' : 'disabled'} onclick="qoStradStep('${l.key}',1)" style="width:20px;height:20px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#e6edf3;cursor:pointer;font-size:12px;line-height:1;opacity:${on ? 1 : .35}">+</button>`
          );
          return `<div style="display:flex;align-items:center;justify-content:space-between;padding:5px 10px;${l.side === 'BUY' ? 'background:#1b2028;' : ''}opacity:${on ? 1 : .4}">
            <span style="display:flex;align-items:center;gap:7px">
              <input type="checkbox" ${on ? 'checked' : ''} onchange="qoStradToggle('${l.key}')" style="accent-color:${col};width:14px;height:14px;cursor:pointer">
              <span style="font-size:10px;font-weight:bold;color:${col};width:28px">${l.side}</span>
              <span style="font-size:12px;color:#e6edf3">${strike}</span></span>
            <span style="display:flex;align-items:center;gap:7px">${steps}
              <span style="font-size:12px;font-weight:bold;color:#c9d1d9;width:50px;text-align:right">${ltp}</span></span></div>`;
        }).join('');
        const lots = parseInt(document.getElementById('qo-strad-lots')?.value) || 1;
        const lc = document.getElementById('qo-strad-legcount'); if (lc) lc.textContent = _qsEnabled().length;
        const ll = document.getElementById('qo-strad-lotslbl'); if (ll) ll.textContent = lots;
        // naked-side warning (a SELL with no matching BUY wing on that side)
        const en = _qsEnabled();
        const has = (side, ot) => en.some(l => l.side === side && l.ot === ot);
        const legOf = (side, ot) => en.find(l => l.side === side && l.ot === ot);
        const callNaked = has('SELL', 'CE') && !has('BUY', 'CE');
        const putNaked = has('SELL', 'PE') && !has('BUY', 'PE');
        // BUY wing must sit FURTHER OTM than the SELL it protects (off_buy > off_sell) — else no loss cap
        const wingIn = ['CE', 'PE'].some(ot => { const s = legOf('SELL', ot), b = legOf('BUY', ot); return s && b && b.off <= s.off; });
        const w = document.getElementById('qo-strad-warn');
        const ml = document.getElementById('qo-strad-marlbl');
        if (callNaked || putNaked) {
          if (w) { w.style.display = 'block'; w.textContent = '⚠️ ek side ka hedge wing off hai → naked SELL, margin bahut zyada'; }
          if (ml) { ml.innerHTML = '💰 Margin (naked side!)'; ml.style.color = '#f85149'; }
        } else if (wingIn) {
          if (w) { w.style.display = 'block'; w.textContent = '⚠️ BUY wing SELL ke barabar/andar hai → loss cap nahi hoga, wing ko aur OTM karo'; }
          if (ml) { ml.innerHTML = '💰 Margin (wing inside!)'; ml.style.color = '#f85149'; }
        } else {
          if (w) w.style.display = 'none';
          if (ml) { ml.innerHTML = '💰 Margin (hedged)'; ml.style.color = '#d29922'; }
        }
      };
      window.qoStradToggle = k => { const l = _qsLeg(k); if (l) l.on = !l.on; qoStradRenderLegs(); qoStradPreviewNow(); };
      window.qoStradStep = (k, d) => {
        const S = window.qoStradState, l = _qsLeg(k); if (!l) return;
        const minOff = l.side === 'SELL' ? 0 : 1;   // SELL can sit at ATM (0); BUY wing stays >=1
        l.off = Math.max(minOff, Math.min(20, l.off + d));
        if (S.sym && l.side === 'BUY') { const o = _qsLeg(l.key === 'ceB' ? 'peB' : 'ceB'); if (o) o.off = l.off; }
        qoStradRenderLegs(); qoStradPreviewNow();
      };
      window.qoStradSym = () => {
        const S = window.qoStradState;
        S.sym = !!document.getElementById('qo-strad-sym')?.checked;
        if (S.sym) { const c = _qsLeg('ceB'), p = _qsLeg('peB'); if (c && p) p.off = c.off; }
        qoStradRenderLegs(); qoStradPreviewNow();
      };
      // Snap SELL legs back to ATM (off 0) — the "auto ATM" button.
      window.qoStradAtmReset = () => {
        window.qoStradState.legs.forEach(l => { if (l.side === 'SELL') l.off = 0; });
        qoStradRenderLegs(); qoStradPreviewNow();
      };
      // Sell at GEX walls: SELL CE -> call-wall, SELL PE -> put-wall (from /api/gex latest snap).
      window.qoStradGexWalls = async () => {
        const S = window.qoStradState;
        if (S.atm == null || S.step == null) { try { await qoStradPreview(false); } catch (e) {} }
        if (S.atm == null || S.step == null) { alert('ATM/step abhi nahi mila — thoda ruk ke phir'); return; }
        const sym = qoSym || 'NIFTY';
        try {
          const g = await (await fetch('/api/gex?latest=1&underlying=' + encodeURIComponent(sym))).json();
          const snap = (g && g.snap) || (g && g.cw != null ? g : null);
          const cw = snap && snap.cw, pw = snap && snap.pw;
          if (cw == null || pw == null) { alert('GEX walls abhi nahi mile (data window ke bahar?)'); return; }
          const ceOff = Math.max(0, Math.min(20, Math.round((cw - S.atm) / S.step)));
          const peOff = Math.max(0, Math.min(20, Math.round((S.atm - pw) / S.step)));
          const ce = _qsLeg('ceS'), pe = _qsLeg('peS');
          if (ce) ce.off = ceOff; if (pe) pe.off = peOff;
          qoStradRenderLegs(); qoStradPreviewNow();
        } catch (e) { alert('GEX fetch fail'); }
      };
      // Fast update (net+LTP) on every change; a full preview (with margin) trails once you stop.
      window.qoStradPreviewNow = () => {
        clearTimeout(window._qsFastT); window._qsFastT = setTimeout(() => qoStradPreview(true), 150);
        clearTimeout(window._qsFullT); window._qsFullT = setTimeout(() => qoStradPreview(false), 650);
      };
      window.qoStradPreview = async (quick) => {
        if (document.hidden) return;
        const spec = _qsSpec();
        const net = document.getElementById('qo-strad-net'), mar = document.getElementById('qo-strad-mar'), marlot = document.getElementById('qo-strad-marlot');
        if (!spec.length) { if (net) net.textContent = '—'; if (mar) mar.textContent = '—'; if (marlot) marlot.textContent = ''; return; }
        // latest-wins token: a newer change (or its trailing full preview) supersedes this one
        window._qsTok = (window._qsTok || 0) + 1; const tok = window._qsTok;
        const sym = qoSym || 'NIFTY';
        const lots = parseInt(document.getElementById('qo-strad-lots')?.value) || 1;
        if (quick && mar && mar.textContent !== '—' && mar.textContent !== '') mar.innerHTML = '<span style="color:#6e7681">…</span>';   // margin recomputing
        try {
          const d = await (await fetch('/api/auto-straddle/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: sym, lots, legs: spec, quick: !!quick }) })).json();
          if (tok !== window._qsTok) return;   // stale — a newer preview already fired
          if (d.ok) {
            if (d.atm != null) window.qoStradState.atm = d.atm;
            if (d.step != null) window.qoStradState.step = d.step;
            // response legs are in the SAME order as the enabled spec → zip back to keys
            const en = _qsEnabled(), pv = {};
            (d.legs || []).forEach((rl, i) => { if (en[i]) pv[en[i].key] = { strike: rl.strike, ltp: rl.ltp }; });
            window.qoStradState.prev = pv;
            if (net) net.innerHTML = d.net_credit_total != null ? (`<b>${_qoInr(d.net_credit_total)}</b>` + (d.net_credit != null ? ` <span style="color:#6e7681">(${d.net_credit.toFixed(1)}pt)</span>` : '')) : '—';
            if (net) net.style.color = (d.net_credit_total >= 0) ? '#3fb950' : '#f85149';
            if (!quick) {   // margin only on the full (trailing) preview — the fast path skips the slow Kite call
              if (mar) mar.innerHTML = d.margin != null ? `<b>${_qoInr(d.margin)}</b>` : '—';
              if (marlot) marlot.textContent = d.margin_lot != null ? (_qoInr(d.margin_lot) + ' / lot') : '';
            }
            qoStradRenderLegs();
          } else {
            // off-market / no live spot → keep last net+margin (don't wipe with an error);
            // strikes still update from atm/step, LTP shows — for changed legs
            if (net && (net.textContent === '—' || net.textContent === '')) net.textContent = d.msg || 'spot —';
            qoStradRenderLegs();
          }
        } catch (e) { qoStradRenderLegs(); }
      };

      window.qoSellStraddle = async () => {
        const sym = qoSym || 'NIFTY';   // closure var (qoSetSym sets this), NOT window.qoSym
        const spec = _qsSpec();
        if (!spec.length) { alert('kam se kam ek leg select karo'); return; }
        const lots = parseInt(document.getElementById('qo-strad-lots')?.value) || 1;
        const tp = parseFloat(document.getElementById('qo-strad-tp')?.value) || 30;
        const sl = parseFloat(document.getElementById('qo-strad-sl')?.value) || 30;
        const st = document.getElementById('qo-status');
        const desc = spec.map(l => l.side[0] + ' ' + l.ot + (l.side === 'BUY' ? ((l.ot === 'CE' ? '+' : '−') + l.offset) : '')).join(', ');
        if (!confirm(`${sym} ${spec.length}-leg — ${lots} lot\n${desc}\ntarget −${tp} / SL +${sl} (paper)?`)) return;
        if (st) { st.textContent = 'legs place ho rahe…'; st.style.color = '#d29922'; }
        try {
          const d = await (await fetch('/api/auto-straddle/fire', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: sym, lots, tp_pt: tp, sl_pt: sl, legs: spec }) })).json();
          if (st) { st.textContent = d.msg || (d.ok ? 'placed' : 'fail'); st.style.color = d.ok ? '#3fb950' : '#f85149'; }
          qoRefreshStraddles();
        } catch (e) { if (st) { st.textContent = 'fail: ' + e; st.style.color = '#f85149'; } }
      };
      window.qoRefreshStraddles = async () => {
        if (document.hidden) return;   // background tab — skip poll (load-trim)
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
        try { await fetch('/api/auto-straddle/close', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) }); } catch (e) {}
        qoRefreshStraddles();
      };
      window.qoStradCfgLoad = async () => {
        try {
          const c = (await (await fetch('/api/auto-straddle/config')).json()).cfg || {};
          const sym = qoSym || 'NIFTY';
          const a = document.getElementById('qo-strad-920'); if (a) a.checked = !!c.enabled_920;
          const b = document.getElementById('qo-strad-alert'); if (b) b.checked = !!c.enabled_alert;
          const ps = (c.per_symbol || {})[sym] || {};   // per-index target/SL (NIFTY 30/30, BANKNIFTY 60/60)
          const tp = document.getElementById('qo-strad-tp'); if (tp) tp.value = ps.tp_pt != null ? ps.tp_pt : (c.tp_pt || 30);
          const sl = document.getElementById('qo-strad-sl'); if (sl) sl.value = ps.sl_pt != null ? ps.sl_pt : (c.sl_pt || 30);
          const lo = document.getElementById('qo-strad-lots'); if (lo && c.lots) lo.value = c.lots;
          const hg = c.hedge || {};
          const he = document.getElementById('qo-strad-hedge'); if (he) he.checked = hg.enabled !== false;
          const hm = document.getElementById('qo-strad-hedgemax'); if (hm && ps.hedge_max_premium != null) hm.value = ps.hedge_max_premium;  // per-index (NIFTY ~2, BNF ~5)
          const lbl = document.getElementById('qo-strad-sym-lbl'); if (lbl) lbl.textContent = sym;
        } catch (e) {}
      };
      window.qoStradCfgSave = async () => {
        const g = id => document.getElementById(id);
        try {
          await fetch('/api/auto-straddle/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
            symbol: qoSym || 'NIFTY',   // tp/sl saved PER-INDEX
            enabled_920: !!g('qo-strad-920')?.checked, enabled_alert: !!g('qo-strad-alert')?.checked,
            lots: parseInt(g('qo-strad-lots')?.value) || 1, tp_pt: parseFloat(g('qo-strad-tp')?.value) || 30, sl_pt: parseFloat(g('qo-strad-sl')?.value) || 30,
            hedge_enabled: !!g('qo-strad-hedge')?.checked, hedge_max_premium: parseFloat(g('qo-strad-hedgemax')?.value) || 2,
          }) });
        } catch (e) {}
      };

      window.qoSetDir = dir => {
        window.qoTrigDir = dir;
        const a = document.getElementById('qo-dir-above'), b = document.getElementById('qo-dir-below');
        if (a) { const on = dir === 'above'; a.style.background = on ? '#238636' : '#21262d'; a.style.borderColor = on ? '#3fb950' : '#30363d'; a.style.color = on ? '#fff' : '#8b949e'; }
        if (b) { const on = dir === 'below'; b.style.background = on ? '#da3633' : '#21262d'; b.style.borderColor = on ? '#f85149' : '#30363d'; b.style.color = on ? '#fff' : '#8b949e'; }
      };

      // auto-hint direction from typed level vs live spot (until user picks one)
      window.qoTrigDirAuto = () => {
        if (window.qoTrigDir) return;   // user chose explicitly — respect it
        const lvl = parseFloat(document.getElementById('qo-trig-level')?.value);
        const spot = window._qoTrigSpot;
        if (!lvl || spot == null) return;
        const dir = lvl > spot ? 'above' : 'below';
        const a = document.getElementById('qo-dir-above'), b = document.getElementById('qo-dir-below');
        if (a) { const on = dir === 'above'; a.style.borderColor = on ? '#3fb950' : '#30363d'; a.style.color = on ? '#3fb950' : '#8b949e'; }
        if (b) { const on = dir === 'below'; b.style.borderColor = on ? '#f85149' : '#30363d'; b.style.color = on ? '#f85149' : '#8b949e'; }
      };

      window.qoArm = async side => {
        const st = document.getElementById('qo-status');
        const level = parseFloat(document.getElementById('qo-trig-level')?.value);
        if (!level || level <= 0) { st.textContent = 'Level daalo (NIFTY price)'; st.style.color = '#f85149'; return; }
        const optType = window._qoOptType || 'CE';
        const lots = parseInt(document.getElementById('qo-lots').value) || 1;
        let dir = window.qoTrigDir;
        if (!dir && window._qoTrigSpot != null) dir = level > window._qoTrigSpot ? 'above' : 'below';
        const slPt = parseFloat(document.getElementById('qo-trig-sl')?.value);
        const tpPt = parseFloat(document.getElementById('qo-trig-tp')?.value);
        st.textContent = 'Arming...'; st.style.color = '#d29922';
        try {
          const r = await fetch('/api/triggers', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: qoSym, level, direction: dir, opt_type: optType, side, lots, offset: qoAtm, mode: qoMode, broker: qoBroker,
              sl_pt: isFinite(slPt) ? slPt : 20, tp_pt: isFinite(tpPt) ? tpPt : 30 })
          });
          const j = await r.json();
          st.textContent = j.msg || (j.ok ? 'Armed' : 'Error');
          st.style.color = j.ok ? '#3fb950' : '#f85149';
          if (j.ok) {
            document.getElementById('qo-trig-level').value = '';
            window.qoTrigDir = null;
            ['qo-dir-above', 'qo-dir-below'].forEach(id => { const e = document.getElementById(id); if (e) e.style.background = '#21262d'; });
            qoRefreshTriggers();
          }
        } catch (e) { st.textContent = 'Network error'; st.style.color = '#f85149'; }
      };

      async function qoRefreshTriggers() {
        if (document.hidden) return;                   // background tab — skip poll (load-trim)
        if (panel.style.display === 'none') return;   // panel band — server ko poll mat karo
        try {
          const r = await fetch('/api/triggers?symbol=' + encodeURIComponent(qoSym));
          const j = await r.json();
          const spot = (j.spot || {})[qoSym];
          if (spot != null) window._qoTrigSpot = spot;
          const se = document.getElementById('qo-trig-spot');
          if (se) se.textContent = (window._qoTrigSpot != null) ? (qoSym + ' ' + window._qoTrigSpot.toFixed(1)) : 'spot —';
          qoTrigDirAuto();
          const box = document.getElementById('qo-armed-list');
          if (!box) return;
          const rows = (j.triggers || []);
          if (!rows.length) { box.innerHTML = '<div style="font-size:10px;color:#6e7681;text-align:center;padding:6px">Koi armed trigger nahi</div>'; return; }
          box.innerHTML = rows.map(t => {
            const above = t.direction === 'above';
            const armed = t.status === 'armed';
            const col = armed ? (above ? '#3fb950' : '#f85149') : (t.status === 'fired' ? '#58a6ff' : '#d29922');
            const arrow = above ? '▲' : '▼';
            const cmp = above ? '≥' : '≤';
            const dist = (t.dist != null && armed) ? `<span style="color:#d29922">${Math.abs(t.dist).toFixed(0)} pts door</span>` : '';
            const statusTxt = armed ? '' : ` &middot; <span style="color:${col}">${t.status}</span>`;
            const res = (t.result && !armed) ? `<div style="font-size:9px;color:#8b949e;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${(t.result || '').replace(/"/g, '&quot;')}">${t.result}</div>` : '';
            return `<div style="background:#0d1117;border:1px solid #21262d;border-left:3px solid ${col};border-radius:0 6px 6px 0;padding:6px 8px;margin-bottom:4px">
              <div style="display:flex;justify-content:space-between;align-items:center">
                <span style="font-size:11px;font-weight:bold;color:#e6edf3">${t.symbol} ${cmp} ${(+t.level).toFixed(0)} <span style="color:${col}">${arrow}</span></span>
                <span onclick="qoCancelTrigger('${t.id}')" title="Cancel" style="color:#8b949e;font-size:13px;cursor:pointer;padding:0 2px">&#x2715;</span>
              </div>
              <div style="font-size:9px;color:#8b949e;margin-top:2px">${t.side} ${t.lots}L ${t.opt_type} (off ${t.offset}) &middot; ${(t.mode || '').toUpperCase()}/${(t.broker || '').toUpperCase()} ${dist}${statusTxt}</div>
              ${res}
            </div>`;
          }).join('');
        } catch (e) { /* keep last render */ }
      }

      window.qoCancelTrigger = async tid => {
        try { await fetch('/api/triggers/' + encodeURIComponent(tid), { method: 'DELETE' }); qoRefreshTriggers(); }
        catch (e) { }
      };

      // ── TRIGGERS + CHART — armed levels drawn on a NIFTY line-chart ────────
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
        } catch (e) { /* keep last render */ }
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
        // Chart + candles ek hi baar bante hain. Har 6s refresh pe SIRF level/spot
        // lines update hoti hain — setData/fitContent kabhi nahi, warna user ka
        // zoom/pan har cycle reset ho jaata (default trade_chart wala hi fix).
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
          if (document.hidden) return;   // background tab — skip watchlist LTP poll (load-trim)
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

