// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // ── Master Prompt: give this to DeepSeek/any AI so it writes engine-ready code ──
    const SCRIPT_MASTER_PROMPT = `You are writing a backtest strategy for my custom Python engine (NOT TradingView, NOT backtrader). Output ONLY one self-contained Python file — no prose.

CONTRACT — implement EITHER of these (evaluate is simplest):

(A) def evaluate(df, cfg, pos):
    # df: pandas DataFrame, oldest->newest, columns: time, open, high, low, close, volume
    #     (df is the history UP TO AND INCLUDING the current bar; last row = current bar)
    # cfg: dict of params (read with cfg.get('x', default))
    # pos: 'LONG' | 'SHORT' | None  (your current position)
    # RETURN exactly one of: 'BUY' | 'SELL' | 'EXIT' | None
    # Use df.iloc[-1] for the current bar, df.iloc[-2] for the previous. Guard len(df).
    ...

(B) def backtest(df, cfg):
    # full control; RETURN (trades, df, plot_spec) where trades is a list of dicts:
    #   {entry_time, entry_price, side('Long'/'Short'), exit_time, exit_price, exit_reason}
    # plot_spec = None, OR draw indicator lines on the chart ONLY via the engine helper
    # (do NOT invent your own plot_spec shape — the chart will ignore it):
    #   from _CHARTING import spec as chspec
    #   df['bb_upper'] = ...; df['bb_lower'] = ...   # compute as DataFrame columns
    #   plot_spec = chspec.build_plot_spec(df, indicators=[
    #       {"name":"BB Upper","series":df["bb_upper"],"type":"line","color":"#FF69B4"},
    #       {"name":"BB Lower","series":df["bb_lower"],"type":"line","color":"#FF69B4"},
    #   ])
    #   # each "series" is a pandas Series aligned with df; "color" is a hex string.
    #   # The chart draws candles + buy/sell/exit markers itself — only add indicator LINES here.

RULES:
- Pure pandas/numpy only (plus 'from _CHARTING import spec as chspec' for plot_spec). No network, no file I/O, no extra pip installs.
- To draw indicators on the chart you MUST use def backtest + chspec.build_plot_spec — the evaluate() path cannot draw lines.
- Intraday only: the engine force-exits every position at 15:15 IST and blocks entries after — do NOT hold overnight.
- Optional config header (first lines, as comments) to set defaults:
    # symbol: NIFTY        (NIFTY/BANKNIFTY = index; anything else = equity)
    # timeframe: 5m        (1m/3m/5m/15m/30m)
    # qty: 1
- Keep it simple — fewer conditions = more robust.

Strategy to implement: <DESCRIBE YOUR STRATEGY HERE>`;

    const SCRIPT_DSL_HELP = `DSL Rule-block (lightest option — runs via the built-in rule engine). Paste lines like:

// --- Entry ---
bb_window = 20
bb_std = 2
entry_long = c_close < c_lower
entry_short = allow_short and c_close > c_upper
// --- Exit ---
exit_long = c_high >= c_upper
exit_short = c_low <= c_lower
sl_pct = 0.5
tp_pct = 0
// --- Other ---
symbol = NIFTY
timeframe = 5m

Variables available in entry_*/exit_* expressions:
  c_open c_close c_high c_low  c_sma c_upper c_lower  c_ema_52 c_ema_100  c_atr c_body_pct
  entry_price entry_candle_high entry_candle_low  allow_short  abs()
Params: bb_window bb_std ema_52_period ema_100_period atr_len sl_pct tp_pct allow_short max_trades_per_day symbol timeframe`;

    function scriptShowMasterPrompt() {
      let el = document.getElementById('script-mp-modal');
      if (el) el.remove();
      el = document.createElement('div');
      el.id = 'script-mp-modal';
      el.style.cssText = 'position:fixed;inset:0;background:#000000cc;z-index:9999;display:flex;align-items:center;justify-content:center;padding:24px';
      el.addEventListener('click', e => { if (e.target === el) el.remove(); });
      el.innerHTML = `
    <div style="background:#0d1117;border:1px solid #30363d;border-radius:10px;max-width:760px;width:100%;max-height:88vh;overflow:auto;padding:20px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <span style="font-size:15px;font-weight:700;color:#e6edf3">📋 Script Contract — DeepSeek/AI ko yeh do</span>
        <button onclick="document.getElementById('script-mp-modal').remove()" style="background:#21262d;border:1px solid #30363d;color:#8b949e;border-radius:6px;padding:5px 12px;cursor:pointer">✕ Close</button>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:8px">
        <button class="btn btn-blue" style="padding:5px 12px;font-size:12px" onclick="_scriptCopyText(SCRIPT_MASTER_PROMPT, this)">📋 Copy Python Master Prompt</button>
        <button class="btn btn-gray" style="padding:5px 12px;font-size:12px" onclick="_scriptCopyText(SCRIPT_DSL_HELP, this)">📋 Copy DSL Cheatsheet</button>
      </div>
      <div style="font-size:11px;color:#8b949e;margin-bottom:6px">Python (powerful, any logic):</div>
      <pre style="white-space:pre-wrap;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px;font-size:11px;color:#adbac7;line-height:1.5;margin:0 0 12px">${SCRIPT_MASTER_PROMPT.replace(/</g, '&lt;')}</pre>
      <div style="font-size:11px;color:#8b949e;margin-bottom:6px">DSL Rules (lightweight, no .py file):</div>
      <pre style="white-space:pre-wrap;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px;font-size:11px;color:#adbac7;line-height:1.5;margin:0">${SCRIPT_DSL_HELP.replace(/</g, '&lt;')}</pre>
    </div>`;
      document.body.appendChild(el);
    }
    function _scriptCopyText(text, btn) {
      navigator.clipboard.writeText(text).then(() => {
        const o = btn.textContent; btn.textContent = '✅ Copied!';
        setTimeout(() => btn.textContent = o, 1800);
      });
    }

    async function pineSave() {
      const code = document.getElementById('pine-code').value.trim();
      if (!code) { document.getElementById('pine-save-msg').textContent = '⚠️ Kuch paste karo pehle'; return; }
      const lang = _scriptCurrentLang();
      const name = document.getElementById('script-name').value.trim();
      if (lang !== 'pine' && !name) {
        document.getElementById('pine-save-msg').style.color = '#d29922';
        document.getElementById('pine-save-msg').textContent = '⚠️ Python/DSL ke liye strategy name zaroori hai';
        return;
      }
      // Confirm the language before committing (user explicitly wanted to be asked)
      if (!confirm(`Yeh "${lang.toUpperCase()}" code save kar rahe hain` + (lang === 'pine' ? ' (reference-only, run nahi hoga).' : ' (dropdown me run-able banega).') + `\n\nLanguage sahi hai? Galat ho to Cancel karke pill change karo.`)) return;
      const msg = document.getElementById('pine-save-msg');
      msg.style.color = '#8b949e'; msg.textContent = 'Saving...';
      const desc = document.getElementById('pine-desc').value.trim();
      const r = await fetch('/api/pine/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code, desc, lang, name }) });
      const d = await r.json();
      // upload pending images
      const files = document.getElementById('pine-img-input').files;
      if (files && files.length) {
        const fd = new FormData();
        for (const f of files) fd.append('images', f);
        await fetch('/api/pine/images/' + d.version, { method: 'POST', body: fd });
      }
      msg.style.color = '#3fb950';
      msg.textContent = `✅ v${d.strat_version || d.version} saved — "${d.name}" [${(d.lang || lang).toUpperCase()}]` + (d.script_id ? ` — dropdown me "${d.script_id}" se run karo` : '');
      document.getElementById('pine-code').value = '';
      document.getElementById('pine-desc').value = '';
      document.getElementById('script-name').value = '';
      document.getElementById('script-file-input').value = '';
      _scriptLang = null; _scriptLangAuto = 'pine'; _scriptPaintPills();
      document.getElementById('pine-img-input').value = '';
      document.getElementById('pine-img-preview').innerHTML = '';
      setTimeout(() => msg.textContent = '', 6000);
      pineLoadLatest();
      pineLoadHistory();
    }

    // ── Image preview strip (before save) ────────────────────────────────────────
    let _pinePreviewFiles = [];   // mutable list before save

    function pineImgPreview(files) {
      _pinePreviewFiles = Array.from(files);
      _pineRenderPreview();
    }

    function _pineRenderPreview() {
      const wrap = document.getElementById('pine-img-preview');
      wrap.innerHTML = '';
      _pinePreviewFiles.forEach((f, i) => {
        const url = URL.createObjectURL(f);
        const div = document.createElement('div');
        div.style.cssText = 'position:relative;width:80px;height:60px;border-radius:4px;overflow:hidden;border:1px solid #30363d;flex-shrink:0';
        div.innerHTML = `
      <img src="${url}" style="width:100%;height:100%;object-fit:cover;cursor:pointer;display:block" onclick="_pineLbShowBlobs(${i})">
      <button onclick="_pineRemovePreview(${i})" title="Remove"
        style="position:absolute;top:2px;right:2px;width:18px;height:18px;background:#f85149cc;border:none;
               border-radius:50%;color:#fff;font-size:11px;line-height:1;cursor:pointer;padding:0;
               display:flex;align-items:center;justify-content:center">✕</button>`;
        wrap.appendChild(div);
      });
      // sync file input with current list
      const dt = new DataTransfer();
      _pinePreviewFiles.forEach(f => dt.items.add(f));
      document.getElementById('pine-img-input').files = dt.files;
    }

    function _pineRemovePreview(idx) {
      _pinePreviewFiles.splice(idx, 1);
      _pineRenderPreview();
    }

    function pineImgDrop(e) {
      e.preventDefault();
      document.getElementById('pine-img-drop').style.borderColor = '#30363d';
      const incoming = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
      _pinePreviewFiles = [..._pinePreviewFiles, ...incoming];
      _pineRenderPreview();
    }

    // ── Lightbox ─────────────────────────────────────────────────────────────────
    let _lb = { urls: [], idx: 0, version: null, blobUrls: [] };

    function _pineGetOrCreateLb() {
      let el = document.getElementById('pine-lb');
      if (el) return el;
      el = document.createElement('div');
      el.id = 'pine-lb';
      el.style.cssText = 'position:fixed;inset:0;background:#000000dd;z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px';
      el.addEventListener('click', e => { if (e.target === el) _pineLbClose(); });
      el.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;max-width:90vw">
      <button onclick="_pineLbNav(-1)" style="background:#21262d;border:1px solid #30363d;color:#e6edf3;border-radius:50%;width:38px;height:38px;font-size:20px;cursor:pointer;flex-shrink:0;line-height:1">‹</button>
      <div style="position:relative">
        <img id="pine-lb-img" src="" style="max-width:80vw;max-height:72vh;border-radius:8px;object-fit:contain;display:block;background:#0d1117">
        <button id="pine-lb-del" onclick="_pineLbDelete()" style="position:absolute;top:8px;right:8px;background:#f8514988;border:1px solid #f85149;color:#fff;border-radius:4px;padding:3px 10px;font-size:12px;cursor:pointer">🗑 Delete</button>
      </div>
      <button onclick="_pineLbNav(1)"  style="background:#21262d;border:1px solid #30363d;color:#e6edf3;border-radius:50%;width:38px;height:38px;font-size:20px;cursor:pointer;flex-shrink:0;line-height:1">›</button>
    </div>
    <div id="pine-lb-counter" style="color:#8b949e;font-size:13px"></div>
    <div id="pine-lb-thumbs" style="display:flex;gap:6px;flex-wrap:wrap;justify-content:center;max-width:90vw"></div>
    <div style="display:flex;gap:10px;margin-top:4px">
      <label id="pine-lb-add-lbl" style="background:#21262d;border:1px solid #30363d;color:#8b949e;border-radius:4px;padding:5px 14px;font-size:12px;cursor:pointer" onmouseover="this.style.color='#e6edf3'" onmouseout="this.style.color='#8b949e'">
        ➕ Add Images<input type="file" multiple accept="image/*" style="display:none" onchange="_pineLbUpload(this.files)">
      </label>
      <button onclick="_pineLbClose()" style="background:#21262d;border:1px solid #30363d;color:#8b949e;border-radius:4px;padding:5px 14px;font-size:12px;cursor:pointer" onmouseover="this.style.color='#e6edf3'" onmouseout="this.style.color='#8b949e'">✕ Close</button>
    </div>`;
      document.body.appendChild(el);
      return el;
    }

    function _pineLbRender() {
      const img = document.getElementById('pine-lb-img');
      const counter = document.getElementById('pine-lb-counter');
      const thumbs = document.getElementById('pine-lb-thumbs');
      const delBtn = document.getElementById('pine-lb-del');
      const addLbl = document.getElementById('pine-lb-add-lbl');
      const urls = _lb.urls;
      if (!urls.length) {
        img.src = ''; counter.textContent = 'Koi image nahi';
        thumbs.innerHTML = ''; if (delBtn) delBtn.style.display = 'none';
        return;
      }
      _lb.idx = Math.max(0, Math.min(_lb.idx, urls.length - 1));
      img.src = urls[_lb.idx];
      counter.textContent = `${_lb.idx + 1} / ${urls.length}`;
      if (delBtn) delBtn.style.display = _lb.version ? 'block' : 'none';
      if (addLbl) addLbl.style.display = _lb.version ? 'inline-flex' : 'none';
      thumbs.innerHTML = urls.map((u, i) =>
        `<img src="${u}" onclick="_lb.idx=${i};_pineLbRender()"
      style="width:54px;height:40px;object-fit:cover;border-radius:4px;cursor:pointer;
             border:2px solid ${i === _lb.idx ? '#58a6ff' : '#30363d'};transition:border-color 0.15s">`
      ).join('');
    }

    async function pineOpenImages(version) {
      const r = await fetch('/api/pine/images/' + version);
      const urls = await r.json();
      _lb = { urls, idx: 0, version, blobUrls: [] };
      _pineGetOrCreateLb().style.display = 'flex';
      _pineLbRender();
    }

    function _pineLbShowBlobs(startIdx) {
      const files = document.getElementById('pine-img-input').files;
      const blobUrls = Array.from(files).map(f => URL.createObjectURL(f));
      _lb = { urls: blobUrls, idx: startIdx, version: null, blobUrls };
      _pineGetOrCreateLb().style.display = 'flex';
      _pineLbRender();
    }

    function _pineLbNav(dir) {
      if (!_lb.urls.length) return;
      _lb.idx = (_lb.idx + dir + _lb.urls.length) % _lb.urls.length;
      _pineLbRender();
    }

    async function _pineLbDelete() {
      if (!_lb.version || !_lb.urls.length) return;
      const url = _lb.urls[_lb.idx];
      const fname = url.split('/').pop();
      if (!confirm('Is image ko delete karein?')) return;
      await fetch(`/api/pine/images/${_lb.version}/${fname}`, { method: 'DELETE' });
      _lb.urls.splice(_lb.idx, 1);
      _lb.idx = Math.max(0, _lb.idx - 1);
      _pineLbRender();
      _pineLoadImgCount(_lb.version);
    }

    async function _pineLbUpload(files) {
      if (!_lb.version || !files.length) return;
      const fd = new FormData();
      for (const f of files) fd.append('images', f);
      const r = await fetch('/api/pine/images/' + _lb.version, { method: 'POST', body: fd });
      const d = await r.json();
      _lb.urls = [..._lb.urls, ...d.urls];
      _lb.idx = _lb.urls.length - d.urls.length;
      _pineLbRender();
      _pineLoadImgCount(_lb.version);
    }

    function _pineLbClose() {
      const el = document.getElementById('pine-lb');
      if (el) el.style.display = 'none';
      if (_lb.version) _pineLoadImgCount(_lb.version);
    }

    function pineEditStart(version) {
      document.getElementById('pine-view-' + version).style.display = 'none';
      document.getElementById('pine-edit-' + version).style.display = 'block';
      document.getElementById('pine-desc-' + version).focus();
    }

    function pineEditCancel(version) {
      document.getElementById('pine-edit-' + version).style.display = 'none';
      document.getElementById('pine-view-' + version).style.display = 'flex';
    }

    async function pineDescSave(version) {
      const desc = document.getElementById('pine-desc-' + version).value.trim();
      await fetch('/api/pine/desc', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version, desc }) });
      // update view inline without full reload
      const viewEl = document.getElementById('pine-view-' + version);
      viewEl.querySelector('span:first-child').textContent = desc || 'Add notes...';
      viewEl.querySelector('span:first-child').style.color = desc ? '#e6edf3' : '#8b949e';
      pineEditCancel(version);
      flash('✅ Saved');
    }

    async function pineLoadVersion(version) {
      const r = await fetch(`/api/pine/code/${version}`);
      if (!r.ok) { flash('Code not found for this version'); return; }
      const code = await r.text();
      document.getElementById('pine-code').value = code;
      document.getElementById('pine-code').scrollIntoView({ behavior: 'smooth', block: 'center' });
      flash('Code loaded — edit kar ke Save Version dabao');
    }

    async function pineDelete(version) {
      if (!confirm(`v${version} permanently delete karein? Yeh undo nahi hoga.`)) return;
      const r = await fetch(`/api/pine/delete/${version}`, { method: 'DELETE' });
      const d = await r.json();
      if (d.ok) {
        flash('Version deleted');
        pineLoadHistory();
        pineLoadLatest();
      } else {
        flash('Delete failed');
      }
    }

    async function pineCopyCode(version) {
      const btn = event.target;
      const orig = btn.textContent;
      try {
        const r = await fetch(`/api/pine/code/${version}`);
        if (!r.ok) { flash('❌ Code not found for this version'); return; }
        const code = await r.text();
        // HTTP pe clipboard API blocked hoti hai — textarea fallback use karo
        const ta = document.createElement('textarea');
        ta.value = code;
        ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
        document.body.appendChild(ta);
        ta.focus(); ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        btn.textContent = '✅ Copied!';
        btn.style.color = '#3fb950';
        btn.style.borderColor = '#3fb950';
        setTimeout(() => { btn.textContent = orig; btn.style.color = '#8b949e'; btn.style.borderColor = '#30363d'; }, 2000);
      } catch (e) {
        flash('❌ Copy failed: ' + e.message);
      }
    }

    function openScannerModal() {
      document.getElementById('scanner-modal').style.display = 'flex';
      document.getElementById('scanner-results').innerHTML = '';
    }

    async function runScannerAPI() {
      const loader = document.getElementById('scanner-loader');
      const resultsDiv = document.getElementById('scanner-results');
      loader.style.display = 'block';
      resultsDiv.innerHTML = '';

      try {
        const res = await fetch('/api/scanner/run', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'success') {
          if (data.results && data.results.length > 0) {
            let html = '<table style="width:100%; border-collapse: collapse; color: #e6edf3;">';
            html += '<tr style="border-bottom: 1px solid #30363d; color: #8b949e; text-align: left;"><th style="padding: 8px;">Symbol</th><th style="padding: 8px;">Date</th><th style="padding: 8px;">Close</th><th style="padding: 8px;">EMA52</th></tr>';
            data.results.forEach(row => {
              html += `<tr style="border-bottom: 1px solid #30363d;"><td style="padding: 8px;">${row.Symbol}</td><td style="padding: 8px;">${row.Date}</td><td style="padding: 8px; color: #3fb950;">${row.Close}</td><td style="padding: 8px;">${row.EMA52}</td></tr>`;
            });
            html += '</table>';
            resultsDiv.innerHTML = html;
          } else {
            resultsDiv.innerHTML = '<p style="color: #8b949e; text-align: center;">No stocks found crossing above 52-EMA today.</p>';
          }
        } else {
          resultsDiv.innerHTML = `<p style="color: #f85149; text-align: center;">Error: ${data.message}</p>`;
        }
      } catch (err) {
        resultsDiv.innerHTML = `<p style="color: #f85149; text-align: center;">Request failed: ${err}</p>`;
      } finally {
        loader.style.display = 'none';
      }
    }

    function openBulkModal() {
      document.getElementById('bulk-modal').style.display = 'flex';
      document.getElementById('bulk-step1').style.display = 'block';
      document.getElementById('bulk-step2').style.display = 'none';
      document.getElementById('bulk-symbols').value = '';
      document.getElementById('bulk-msg').innerHTML = '';
      autoFillStrategyDesc();
    }

    let _bulkPreviewData = [];

    async function loadBulkPreview() {
      const symbols = document.getElementById('bulk-symbols').value;
      if (!symbols.trim()) {
        document.getElementById('bulk-msg').innerHTML = '<span style="color: #f85149">Please enter at least one symbol.</span>';
        return;
      }

      document.getElementById('bulk-loader').style.display = 'block';
      document.getElementById('bulk-loader').innerText = 'Loading Prices... ⏳';
      document.getElementById('bulk-msg').innerHTML = '';

      try {
        const res = await fetch('/api/bulk-preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbols: symbols })
        });
        const data = await res.json();

        if (data.status === 'success' && data.data && data.data.length > 0) {
          _bulkPreviewData = data.data;
          const tbody = document.getElementById('bulk-preview-body');
          tbody.innerHTML = '';

          _bulkPreviewData.forEach((item, idx) => {
            // default qty 1, sl 2.0%
            item.qty = 1;
            item.sl_pct = 2.0;

            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid #21262d';

            const formatMoney = (val) => val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

            tr.innerHTML = `
          <td style="padding: 8px; color: #e6edf3; font-weight: 600;">${item.sym}</td>
          <td style="padding: 8px; color: #e6edf3; text-align: right;">${formatMoney(item.ltp)}</td>
          <td style="padding: 8px; text-align: center;">
            <input type="number" id="bulk-q-${idx}" value="${item.qty}" min="1" oninput="updateBulkMath(${idx})" style="width: 60px; padding: 4px; background: #0d1117; border: 1px solid #30363d; border-radius: 4px; color: #c9d1d9; text-align: center;">
          </td>
          <td style="padding: 8px; text-align: center;">
            <input type="number" id="bulk-sl-${idx}" value="${item.sl_pct}" min="0" step="0.1" oninput="updateBulkMath(${idx})" style="width: 60px; padding: 4px; background: #0d1117; border: 1px solid #30363d; border-radius: 4px; color: #c9d1d9; text-align: center;">%
            <div id="bulk-sl-math-${idx}" style="font-size: 11px; color: #f85149; margin-top: 4px; white-space: nowrap;"></div>
          </td>
          <td style="padding: 8px; color: #e6edf3; text-align: right; font-weight: bold;" id="bulk-cap-${idx}">
            ${formatMoney(item.ltp * item.qty)}
          </td>
        `;
            tbody.appendChild(tr);
            setTimeout(() => updateBulkMath(idx), 0); // initialize math
          });

          updateBulkGrandTotal();
          document.getElementById('bulk-step1').style.display = 'none';
          document.getElementById('bulk-step2').style.display = 'block';
        } else {
          document.getElementById('bulk-msg').innerHTML = `<span style="color: #f85149">❌ ${data.message || 'No symbols found.'}</span>`;
        }
      } catch (err) {
        document.getElementById('bulk-msg').innerHTML = `<span style="color: #f85149">❌ Request failed: ${err}</span>`;
      } finally {
        document.getElementById('bulk-loader').style.display = 'none';
        document.getElementById('bulk-loader').innerText = 'Placing Trades... ⏳';
      }
    }

    function updateBulkMath(idx) {
      const item = _bulkPreviewData[idx];
      const qEl = document.getElementById(`bulk-q-${idx}`);
      const slEl = document.getElementById(`bulk-sl-${idx}`);

      const qty = parseInt(qEl.value) || 0;
      const sl = parseFloat(slEl.value) || 0;

      item.qty = qty;
      item.sl_pct = sl;

      const cap = item.ltp * qty;
      document.getElementById(`bulk-cap-${idx}`).innerText = cap.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

      if (sl > 0 && item.ltp > 0) {
        const slPx = item.ltp * (1 - (sl / 100));
        const loss = (item.ltp - slPx) * qty;
        document.getElementById(`bulk-sl-math-${idx}`).innerText = `Sell @ ${slPx.toFixed(1)} (-${Math.round(loss)})`;
      } else {
        document.getElementById(`bulk-sl-math-${idx}`).innerText = '';
      }

      updateBulkGrandTotal();
    }

    function updateBulkGrandTotal() {
      const total = _bulkPreviewData.reduce((acc, item) => acc + (item.ltp * (item.qty || 0)), 0);
      document.getElementById('bulk-grand-total').innerText = total.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    const STRATEGY_MAP = {
      "52_Week_Breakout": { tf: "1D", ind: "EMA(52)", desc: "Price closing above 52-week EMA with high volume" },
      "Bollinger_Bounce": { tf: "15", ind: "BB(20,2)", desc: "Bounce from lower Bollinger Band with bullish candle" },
      "VWAP_Reversal": { tf: "5", ind: "VWAP,EMA(9)", desc: "Reversing towards VWAP after morning extreme" },
      "Price_Action": { tf: "1D", ind: "", desc: "Pure price action or horizontal support breakout" },
      "Custom": { tf: "1D", ind: "", desc: "" }
    };

    function autoFillStrategyDesc() {
      const strategyName = document.getElementById('bulk-strategy').value;
      const reasonEl = document.getElementById('bulk-reason');
      if (STRATEGY_MAP[strategyName] && STRATEGY_MAP[strategyName].desc) {
        reasonEl.value = STRATEGY_MAP[strategyName].desc;
      } else if (!STRATEGY_MAP[strategyName]) {
        // If it's a completely custom typed strategy, don't clear the user's reason
      }
    }

    let _editStratOld = "";
    function editStrategyLabel(btn, ev) {
      ev.preventDefault();
      ev.stopPropagation();
      let oldStrat = btn.getAttribute('data-strat');
      _editStratOld = oldStrat;
      let parts = oldStrat.split(' | ');
      let name = parts[0];
      let desc = parts.slice(1).join(' | ');

      document.getElementById('edit-strat-name').value = name;
      document.getElementById('edit-strat-desc').value = desc;
      document.getElementById('edit-strat-modal').style.display = 'flex';
    }

    async function saveStrategyLabel() {
      let newName = document.getElementById('edit-strat-name').value.trim();
      let newDesc = document.getElementById('edit-strat-desc').value.trim();
      if (!newName) return;

      let newStrat = newName;
      if (newDesc) newStrat += " | " + newDesc;

      if (newStrat === _editStratOld) {
        document.getElementById('edit-strat-modal').style.display = 'none';
        return;
      }

      document.getElementById('edit-strat-modal').style.display = 'none';
      try {
        let r = await fetch('/api/orders/rename-strategy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ old_strategy: _editStratOld, new_strategy: newStrat })
        });
        let j = await r.json();
        if (j.status === "success") {
          ordersRender();
          flash("Strategy updated!", "#3fb950");
        } else {
          alert("Failed to update: " + j.message);
        }
      } catch (e) {
        alert("Error: " + e);
      }
    }

    async function submitBulkOrders() {
      const strategyName = document.getElementById('bulk-strategy').value;
      const reason = document.getElementById('bulk-reason').value;

      const mapped = STRATEGY_MAP[strategyName] || { tf: "1D", ind: "" };
      let tf = mapped.tf;
      let ind = mapped.ind;

      let finalFilterName = strategyName;
      if (reason.trim()) {
        finalFilterName += ` | ${reason.trim()}`;
      }

      const productType = document.getElementById('bulk-product-type').value;
      const trades = _bulkPreviewData.filter(x => x.qty > 0).map(x => ({
        sym: x.sym,
        sec_id: x.sec_id,
        ltp: x.ltp,
        qty: x.qty,
        sl_pct: x.sl_pct,
        product_type: productType
      }));

      if (trades.length === 0) {
        document.getElementById('bulk-msg').innerHTML = '<span style="color: #f85149">No valid trades to place.</span>';
        return;
      }

      document.getElementById('bulk-loader').style.display = 'block';
      document.getElementById('bulk-msg').innerHTML = '';

      try {
        const res = await fetch('/api/bulk-order', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filter_name: finalFilterName, tf: tf, ind: ind, trades: trades })
        });
        const data = await res.json();

        if (data.status === 'success') {
          document.getElementById('bulk-msg').innerHTML = `<span style="color: #3fb950">✅ ${data.message}</span>`;
          setTimeout(() => {
            document.getElementById('bulk-modal').style.display = 'none';
            switchTab('orders'); // Jump to orders tab
          }, 1500);
        } else {
          document.getElementById('bulk-msg').innerHTML = `<span style="color: #f85149">❌ Error: ${data.message}</span>`;
        }
      } catch (err) {
        document.getElementById('bulk-msg').innerHTML = `<span style="color: #f85149">❌ Request failed: ${err}</span>`;
      } finally {
        document.getElementById('bulk-loader').style.display = 'none';
      }
    }


