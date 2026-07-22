// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    function toggleGlobalTrailingInfo() {
      const slType = document.getElementById('risk-global-default-sl-type').value;
      const infoEl = document.getElementById('global-sl-trailing-info');
      if (infoEl) {
        infoEl.style.display = slType === 'trailing_pt' ? 'block' : 'none';
      }
    }

    function _sltpToggleCandleDir(which) {
      const typeEl = document.getElementById(`edit-sltp-${which}-type`);
      const dirEl = document.getElementById(`edit-sltp-${which}-dir`);
      const valInput = document.getElementById(`edit-sltp-${which}`);

      if (dirEl) {
        dirEl.style.display = typeEl.value === 'candle_close' ? '' : 'none';
      }

      if (which === 'sl') {
        const infoEl = document.getElementById('sl-trailing-info');
        if (infoEl) {
          infoEl.style.display = typeEl.value === 'trailing_pt' ? 'block' : 'none';
        }
      }

      if (typeEl.value === 'trailing_pt') {
        valInput.placeholder = 'gap:step (e.g., 10:1)';
      } else if (typeEl.value === 'candle_close') {
        valInput.placeholder = 'price (e.g., 25000)';
      } else {
        valInput.placeholder = 'value';
      }
    }

    function openSlTpModal(id, sl, tp, slType, tpType, entryPx, slCandle, tpCandle) {
      document.getElementById('edit-sltp-id').value = id;
      // Legacy candle-close checkboxes were removed from the modal (the
      // "Candle Close" option in the SL/TP Type dropdown replaced them).
      // Guard the refs so a missing element never crashes the modal open. (Task 2)
      const _slCandleEl = document.getElementById('edit-sltp-sl-candle');
      if (_slCandleEl) _slCandleEl.checked = !!slCandle;
      const _tpCandleEl = document.getElementById('edit-sltp-tp-candle');
      if (_tpCandleEl) _tpCandleEl.checked = !!tpCandle;

      function splitCandle(val, dirElId, valElId) {
        if (val && val.includes(':')) {
          const [dir, num] = val.split(':');
          const dEl = document.getElementById(dirElId);
          if (dEl) dEl.value = dir;
          document.getElementById(valElId).value = num;
        } else {
          document.getElementById(valElId).value = val || '';
        }
      }

      document.getElementById('edit-sltp-sl-type').value = slType || 'pct';
      document.getElementById('edit-sltp-tp-type').value = tpType || 'pct';
      splitCandle(slType === 'candle_close' ? sl : null, 'edit-sltp-sl-dir', 'edit-sltp-sl');
      splitCandle(tpType === 'candle_close' ? tp : null, 'edit-sltp-tp-dir', 'edit-sltp-tp');
      if (slType !== 'candle_close') document.getElementById('edit-sltp-sl').value = sl || '';
      if (tpType !== 'candle_close') document.getElementById('edit-sltp-tp').value = tp || '';

      if (entryPx) {
        const g = RISK_CFG.global || {};
        const s1 = g.trailing_step_band_1 != null && g.trailing_step_band_1 !== "" ? g.trailing_step_band_1 : "1.0";
        const s2 = g.trailing_step_band_2 != null && g.trailing_step_band_2 !== "" ? g.trailing_step_band_2 : "2.50";
        const s3 = g.trailing_step_band_3 != null && g.trailing_step_band_3 !== "" ? g.trailing_step_band_3 : "5.0";
        const s4 = g.trailing_step_band_4 != null && g.trailing_step_band_4 !== "" ? g.trailing_step_band_4 : "10.0";

        let minStep = 1.0;
        if (entryPx <= 50) minStep = parseFloat(s1);
        else if (entryPx <= 100) minStep = parseFloat(s2);
        else if (entryPx <= 500) minStep = parseFloat(s3);
        else minStep = parseFloat(s4);

        const infoSL = document.getElementById('sl-trailing-info');
        if (infoSL) {
          infoSL.innerHTML = `⚠️ <b>Trailing Points Steps (premium):</b><br/>
        • Premium ₹0 to ₹50: step ₹${s1}<br/>
        • Premium ₹50 to ₹100: step ₹${s2}<br/>
        • Premium ₹100 to ₹500: step ₹${s3}<br/>
        • Premium Above ₹500: step ₹${s4}<br/>
        💡 <b>Current Position Step: ₹${minStep}</b> (based on entry price ₹${entryPx})`;
        }
      }

      _sltpToggleCandleDir('sl');
      _sltpToggleCandleDir('tp');
      document.getElementById('edit-sltp-modal').style.display = 'flex';
    }

    async function saveSlTp() {
      const id = document.getElementById('edit-sltp-id').value;
      const slType = document.getElementById('edit-sltp-sl-type').value;
      const tpType = document.getElementById('edit-sltp-tp-type').value;
      let sl = document.getElementById('edit-sltp-sl').value;
      let tp = document.getElementById('edit-sltp-tp').value;
      if (slType === 'candle_close' && sl) sl = document.getElementById('edit-sltp-sl-dir').value + ':' + sl;
      if (tpType === 'candle_close' && tp) tp = document.getElementById('edit-sltp-tp-dir').value + ':' + tp;

      const _slCandleEl = document.getElementById('edit-sltp-sl-candle');
      const _tpCandleEl = document.getElementById('edit-sltp-tp-candle');
      const slCandle = _slCandleEl ? _slCandleEl.checked : false;
      const tpCandle = _tpCandleEl ? _tpCandleEl.checked : false;

      try {
        const res = await fetch('/api/orders/update-sl-tp', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            id: id,
            sl_type: slType,
            sl_val: sl,
            tp_type: tpType,
            tp_val: tp,
            sl_candle_close: slCandle,
            tp_candle_close: tpCandle
          })
        });
        const data = await res.json();
        if (data.status === 'success') {
          document.getElementById('edit-sltp-modal').style.display = 'none';
          const el = document.getElementById('ord-open'); if (el) el.dataset.fp = '';
          ordersRender();
        } else {
          alert("Error: " + data.message);
        }
      } catch (e) {
        alert("Error saving SL/TP.");
      }
    }

    // Note modal (2026-07-02 redesign) — id-only lookup instead of passing
    // URL-encoded note/imgs through onclick (removes escaping fragility and lets
    // the modal show full trade metadata + navigate Prev/Next across trades).
    // Old 2- and 3-arg call sites still work — the extra args are just ignored.
    function _noteModalTradeById(id) {
      const d = window._lastOrdersData || {};
      const all = (d.details || []).concat(d.open || []);
      return all.find(t => String(t.id) === String(id));
    }

    function _noteModalBuildNavList() {
      const d = window._lastOrdersData || {};
      const all = (d.details || []).concat(d.open || []);
      // Chronological order (exit time if completed, else entry time) so Next/Prev
      // moves through the day in a predictable sequence regardless of whatever
      // sort the table itself currently has applied.
      return all.slice().sort((a, b) => {
        const ta = a.exit_time || a.entry_time || '';
        const tb = b.exit_time || b.entry_time || '';
        return ta < tb ? -1 : ta > tb ? 1 : 0;
      }).map(t => t.id);
    }

    function _noteModalMetaHtml(t) {
      if (!t) return '';
      const isOpen = t.exit_price == null || t.exit_price === 0 && !t.exit_time;
      const g = t._gross != null ? t._gross : (t.pnl || 0);
      const n = t._net != null ? t._net : g;
      const tx = t._tax != null ? t._tax : 0;
      const pts = t.entry === 'BUY' ? (t.exit_price || 0) - (t.entry_price || 0) : (t.entry_price || 0) - (t.exit_price || 0);
      const nc = n > 0 ? '#3fb950' : (n < 0 ? '#f85149' : '#e6edf3');
      const ptsC = pts > 0 ? '#3fb950' : (pts < 0 ? '#f85149' : '#8b949e');
      const dur = t.exit_time ? _durFmt(t.entry_time, t.exit_time) : '—';
      const item = (label, val, color) => `<div><div style="color:#6e7681;font-size:9.5px;text-transform:uppercase">${label}</div><div style="color:${color || '#adbac7'};font-weight:600;margin-top:1px">${val}</div></div>`;
      return `<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
      <div style="font-size:14px;font-weight:700;color:#e6edf3">${t.sym || '—'}
        <span style="font-size:10px;font-weight:600;color:${t.entry === 'BUY' ? '#3fb950' : '#f85149'};margin-left:6px">${t.entry || ''}</span></div>
      ${isOpen ? '<span style="font-size:10px;color:#d29922;font-weight:600">⏳ OPEN</span>' : ''}
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px 12px">
      ${item('P&L (Net)', (n >= 0 ? '+' : '') + Math.round(n), nc)}
      ${item('Points', (pts >= 0 ? '+' : '') + pts.toFixed(2), ptsC)}
      ${item('Duration', dur)}
      ${item('Tax', '−' + Math.round(tx), '#f85149')}
      ${item('Entry Time', t.entry_time || '—')}
      ${item('Exit Time', t.exit_time || (isOpen ? 'running' : '—'))}
      ${item('Qty', t.qty || '—')}
      ${item('Entry Px', Number(t.entry_price || 0).toFixed(2))}
    </div>`;
    }

    function openNoteModal(id) {
      const t = _noteModalTradeById(id);
      const note = (t && (t.tags || []).find(tg => tg.startsWith('NOTE:'))) ? t.tags.find(tg => tg.startsWith('NOTE:')).slice(5) : '';
      const imgs = t ? _imgTagsOf(t) : [];

      window._noteModalNavList = _noteModalBuildNavList();
      window._noteModalNavIdx = window._noteModalNavList.findIndex(x => String(x) === String(id));

      document.getElementById('edit-note-id').value = id;
      document.getElementById('edit-note-text').value = note;
      document.getElementById('edit-note-file').value = '';
      document.getElementById('edit-note-meta').innerHTML = _noteModalMetaHtml(t);
      _renderNoteImgs(id, imgs);
      document.getElementById('edit-note-modal').style.display = 'flex';
    }

    function noteModalNav(dir) {
      const list = window._noteModalNavList || [];
      if (!list.length) return;
      let idx = (window._noteModalNavIdx == null ? 0 : window._noteModalNavIdx) + dir;
      if (idx < 0) idx = list.length - 1;
      if (idx >= list.length) idx = 0;
      openNoteModal(list[idx]);   // rebuilds the nav list fresh, but idx position carries through findIndex
    }

    function _renderNoteImgs(id, imgs) {
      const el = document.getElementById('edit-note-imgs');
      if (!imgs || !imgs.length) { el.innerHTML = ''; return; }
      const urls = imgs.map(fn => `/api/orders/note-image/${id}/${fn}`);
      el.innerHTML = imgs.map((fn, index) => {
        const escapedUrls = JSON.stringify(urls).replace(/"/g, '&quot;');
        return `
    <div style="position:relative">
      <a href="javascript:void(0)" onclick="openImageViewer(${escapedUrls}, ${index})">
        <img src="/api/orders/note-image/${id}/${fn}" style="width:70px;height:70px;object-fit:cover;border-radius:6px;border:1px solid #30363d">
      </a>
      <span onclick="deleteNoteImg(${id},'${fn}')" style="position:absolute;top:-6px;right:-6px;background:#f85149;border-radius:50%;width:16px;height:16px;font-size:10px;line-height:16px;text-align:center;cursor:pointer;color:#fff">✕</span>
    </div>`;
      }).join('');
    }

    async function _refreshNoteImgs(id) {
      try {
        const r = await fetch('/api/orders?date=' + (document.getElementById('ord-date') || {}).value);
        const d = await r.json();
        const all = (d.details || []).concat(d.open || []);
        const t = all.find(x => String(x.id) === String(id));
        const imgs = t ? (t.tags || []).filter(tg => tg.startsWith('IMG:')).map(tg => tg.slice(4)) : [];
        _renderNoteImgs(id, imgs);
        ordersRender();
      } catch (e) { }
    }

    async function deleteNoteImg(id, fn) {
      await fetch('/api/orders/delete-image', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id, filename: fn }) });
      await _refreshNoteImgs(id);
    }

    // Clipboard paste → image upload in the Edit Note modal (item E). When the note
    // modal is open, pasting (Ctrl+V) an image from the clipboard uploads it right
    // away to the current note and shows the thumbnail — same endpoint as the file
    // picker. Non-image pastes (plain text into the textarea) are left untouched.
    document.addEventListener('paste', async (e) => {
      const modal = document.getElementById('edit-note-modal');
      if (!modal || modal.style.display !== 'flex') return;
      const items = (e.clipboardData && e.clipboardData.items) || [];
      const files = [];
      for (const it of items) {
        if (it.kind === 'file' && it.type && it.type.startsWith('image/')) {
          const f = it.getAsFile();
          if (f) files.push(f);
        }
      }
      if (!files.length) return;          // let normal text paste through
      e.preventDefault();
      const id = document.getElementById('edit-note-id').value;
      if (!id) return;
      const fd = new FormData();
      fd.append('id', id);
      files.forEach((f, i) => fd.append('images', f, (f.name || `pasted_${Date.now()}_${i}.png`)));
      try {
        flash('⏳ Uploading pasted image…', '#8b949e');
        const ir = await fetch('/api/orders/upload-image', { method: 'POST', body: fd });
        const ij = await ir.json();
        if (ij.status !== 'success') { alert('Paste upload error: ' + (ij.message || '')); return; }
        await _refreshNoteImgs(id);
        flash('✅ Image pasted', '#3fb950');
      } catch (err) { alert('Paste upload failed.'); }
    });

    async function saveNote() {
      const id = document.getElementById('edit-note-id').value;
      const note = document.getElementById('edit-note-text').value;
      const fileInput = document.getElementById('edit-note-file');

      try {
        const res = await fetch('/api/orders/update-note', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: id, note: note })
        });
        const data = await res.json();
        if (data.status !== 'success') { alert("Error: " + data.message); return; }

        if (fileInput.files && fileInput.files.length) {
          const fd = new FormData();
          fd.append('id', id);
          for (const f of fileInput.files) fd.append('images', f);
          const ir = await fetch('/api/orders/upload-image', { method: 'POST', body: fd });
          const ij = await ir.json();
          if (ij.status !== 'success') { alert("Image upload error: " + ij.message); }
        }

        document.getElementById('edit-note-modal').style.display = 'none';
        ordersRender(); // Refresh orders
        if (typeof calendarRender === 'function') calendarRender(true); // Refresh calendar trades
      } catch (e) {
        alert("Error saving Note.");
      }
    }

    // ── IMAGE LIGHTBOX VIEWER ──
    let currentViewerUrls = [];
    let currentViewerIndex = 0;

    function openImageViewer(urls, index) {
      currentViewerUrls = urls;
      currentViewerIndex = index;
      document.getElementById('image-viewer-modal').style.display = 'flex';
      updateImageViewer();
      document.addEventListener('keydown', handleViewerKeyDown);
    }

    function closeImageViewer(event) {
      if (event) {
        event.stopPropagation();
      }
      document.getElementById('image-viewer-modal').style.display = 'none';
      document.removeEventListener('keydown', handleViewerKeyDown);
    }

    function updateImageViewer() {
      if (!currentViewerUrls || !currentViewerUrls.length) return;
      if (currentViewerIndex < 0) {
        currentViewerIndex = currentViewerUrls.length - 1;
      } else if (currentViewerIndex >= currentViewerUrls.length) {
        currentViewerIndex = 0;
      }
      const img = document.getElementById('image-viewer-img');
      img.src = currentViewerUrls[currentViewerIndex];
      const counter = document.getElementById('image-viewer-counter');
      counter.textContent = `${currentViewerIndex + 1} / ${currentViewerUrls.length}`;
      const prevBtn = document.getElementById('image-viewer-prev');
      const nextBtn = document.getElementById('image-viewer-next');
      if (currentViewerUrls.length <= 1) {
        prevBtn.style.display = 'none';
        nextBtn.style.display = 'none';
      } else {
        prevBtn.style.display = 'block';
        nextBtn.style.display = 'block';
      }
    }

    function navigateImageViewer(dir) {
      currentViewerIndex += dir;
      updateImageViewer();
    }

    function handleViewerKeyDown(e) {
      if (e.key === 'ArrowLeft') {
        navigateImageViewer(-1);
      } else if (e.key === 'ArrowRight') {
        navigateImageViewer(1);
      } else if (e.key === 'Escape') {
        closeImageViewer();
      }
    }

    let pnlChartInstance = null;
    window.renderPnlGraph = function(tradesList) {
      if (!tradesList) {
        if (window._lastTradesList) tradesList = window._lastTradesList;
        else tradesList = window.currentCalendarTrades || [];
      } else {
        window._lastTradesList = tradesList;
      }
      
      const toggle = document.getElementById('pnl-graph-toggle');
      if (!toggle) return;
      const mode = toggle.value; // 'instrument' | 'symbol' | 'exit_reason'

      const aggregated = {};

      tradesList.forEach(t => {
        const sym = t.sym || t.symbol || 'UNKNOWN';
        let key = sym;
        if (mode === 'exit_reason') {
          // group by the reason PREFIX (before ':') so DEFAULT_TSL_SL:-1600 etc collapse
          key = (t.exit_reason || '').split(':')[0].trim() || 'unknown';
        } else if (mode === 'instrument') {
          if (sym.includes('-')) {
            key = sym.split('-')[0];
          } else {
            const match = sym.match(/^[a-z&]+/i);
            if (match) {
              key = match[0].toUpperCase();
            } else {
              key = sym;
            }
          }
        }
        
        let net = 0;
        let p = parseFloat(t.points);
        const ep = parseFloat(t.entry_price) || 0;
        const xp = parseFloat(t.exit_price) || 0;
        const qty = parseFloat(t.qty) || Number(t.q) || 0;
        const entrySide = (t.entry || t.side || '').toUpperCase();
        let gross = 0;
        if (entrySide === 'BUY' || entrySide === 'LONG') gross = (xp - ep) * qty;
        if (entrySide === 'SELL' || entrySide === 'SHORT') gross = (ep - xp) * qty;
        let tax = 0;
        if (typeof window.calcCharges === 'function') tax = window.calcCharges(ep, xp, qty, entrySide) || 0;
        net = gross - tax;
        
        if (!aggregated[key]) aggregated[key] = { grossGain: 0, grossLoss: 0, net: 0, count: 0, lot: null };
        if (aggregated[key].lot == null && t.lot_size) aggregated[key].lot = t.lot_size;   // lot size (const per instrument)
        if (net >= 0) {
          aggregated[key].grossGain += net;
        } else {
          aggregated[key].grossLoss += Math.abs(net);
        }
        aggregated[key].net += net;
        aggregated[key].count++;
      });
      
      const labels = Object.keys(aggregated);
      // Sort by net profit descending (highest profit on the left)
      labels.sort((a, b) => aggregated[b].net - aggregated[a].net);
      
      const gainVals = labels.map(l => aggregated[l].grossGain);
      const lossVals = labels.map(l => aggregated[l].grossLoss);
      
      const ctx = document.getElementById('pnlGraphCanvas');
      if (!ctx) return;
      
      if (pnlChartInstance) {
        pnlChartInstance.destroy();
      }
      
      pnlChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: labels,
          datasets: [
            {
              label: 'Gross Gain (₹)',
              data: gainVals,
              backgroundColor: 'rgba(63, 185, 80, 0.8)',
              borderColor: '#3fb950',
              borderWidth: 1,
              stack: 'Stack 0',
            },
            {
              label: 'Gross Loss (₹)',
              data: lossVals,
              backgroundColor: 'rgba(248, 81, 73, 0.8)',
              borderColor: '#f85149',
              borderWidth: 1,
              stack: 'Stack 0',
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          onClick: (e, elements) => {
            if (elements && elements.length > 0) {
              const index = elements[0].index;
              const label = pnlChartInstance.data.labels[index];
              const toggle = document.getElementById('pnl-graph-toggle');
              const mode = toggle ? toggle.value : 'instrument';
              
              if (window.calPnlGraphFilter === label && window.calPnlGraphMode === mode) {
                window.calPnlGraphFilter = null; // deselect
              } else {
                window.calPnlGraphFilter = label;
                window.calPnlGraphMode = mode;
              }
              if (typeof window.updateCalSelectedDateBadge === 'function') window.updateCalSelectedDateBadge();
              if (typeof window.renderPointsPerTradeTable === 'function') window.renderPointsPerTradeTable();
              // `tableCard` ka guard tha, uske `.closest('.tv-card')` ka NAHI —
              // aur null wahi se aata hai: tbody DOM me hai par uska `.tv-card`
              // wrapper hamesha nahi hota (2026-07-17 bell: "Uncaught TypeError:
              // Cannot read properties of null (reading 'scrollIntoView')").
              // Ek non-null cheez ka bacha lena uski parent ka bach jaana nahi hai.
              const tableCard = document.getElementById('cal-points-per-trade-tbody');
              const card = tableCard && tableCard.closest('.tv-card');
              if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              mode: 'index',
              intersect: false,
              callbacks: {
                label: function(context) {
                  return context.dataset.label + ': ₹ ' + context.raw.toFixed(2);
                },
                footer: function(tooltipItems) {
                  const key = tooltipItems[0].label;
                  const count = aggregated[key].count;
                  const net = aggregated[key].net;
                  const lot = aggregated[key].lot;
                  return (lot ? 'Lot size: ' + lot + '\n' : '')
                    + 'Total Trades: ' + count + '\nNet P&L: ₹ ' + net.toFixed(2);
                }
              }
            }
          },
          scales: {
            x: {
              stacked: true,
              ticks: { display: false },
              grid: { display: false }
            },
            y: {
              stacked: true,
              ticks: { color: '#8b949e', font: {size: 10} },
              grid: { color: '#30363d' },
              border: { display: false }
            }
          }
        }
      });
    };
  
