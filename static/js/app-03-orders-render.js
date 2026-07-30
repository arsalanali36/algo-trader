// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // Open-Positions group collapse state persists across page reloads (localStorage).
    // Default = open; the user's manual collapse survives F5 (both the render's
    // fingerprint-guarded rebuild AND a full page reload emit `open` from here).
    // Keyed by the stable grpId ('grp_<sanitized-strat>' / 'grp_blocked').
    function _grpCollapsed() {
      try { return JSON.parse(localStorage.getItem('ord_grp_collapsed') || '{}'); } catch (_) { return {}; }
    }
    function _grpOpenAttr(key) { return _grpCollapsed()[key] ? '' : 'open'; }
    window._grpToggleSave = function (key, isOpen) {
      const m = _grpCollapsed();
      if (isOpen) delete m[key]; else m[key] = 1;
      try { localStorage.setItem('ord_grp_collapsed', JSON.stringify(m)); } catch (_) { }
    };

    // PDF me na aayein. Globals render ke time set hote hain (renderCachedOrders end).
    function exportCompletedPdf(btn) {
      try {
        if (!window.jspdf || !window.jspdf.jsPDF) {
          alert('PDF library load nahi hui (internet check karo — jsPDF CDN se aata hai).');
          return;
        }
        const rows = window._completedData || [];
        if (!rows.length) { alert('Is din koi completed trade nahi hai — export ke liye kuch nahi.'); return; }
        const date = window._completedDate || '';
        const { jsPDF } = window.jspdf;
        const doc = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' });

        // Header
        doc.setFontSize(15); doc.setTextColor(20);
        doc.text('Completed Trades — P&L Report', 40, 40);
        doc.setFontSize(10); doc.setTextColor(90);
        doc.text('Date: ' + (date || '—') + '     Generated: ' + new Date().toLocaleString(), 40, 58);

        // Source summary line (STRATEGY / MANUAL / WEBHOOK)
        const st = window._completedSrcTotals || {};
        const sumParts = [];
        ['STRATEGY', 'MANUAL', 'WEBHOOK'].forEach(s => {
          if (st[s] && (st[s].n || st[s].opn))
            sumParts.push(s + ': ' + (st[s].net >= 0 ? '+' : '') + Math.round(st[s].net) +
              ' (' + st[s].n + ' trades, ' + st[s].w + 'W/' + (st[s].n - st[s].w) + 'L)');
        });
        if (sumParts.length) { doc.setFontSize(9); doc.setTextColor(60); doc.text(sumParts.join('     '), 40, 74); }

        // Body + totals
        let tg = 0, ttx = 0, tn = 0, tpts = 0;
        const body = rows.map(t => {
          const pts = (t.entry === 'BUY') ? (t.exit_price || 0) - (t.entry_price || 0)
            : (t.entry_price || 0) - (t.exit_price || 0);
          tg += (t._gross || 0); ttx += (t._tax || 0); tn += (t._net || 0); tpts += pts;
          return [
            t.sym || '',
            t.entry || '',
            t.qty || 0,
            (t.entry_price != null ? Number(t.entry_price).toFixed(2) : ''),
            (t.exit_price != null ? Number(t.exit_price).toFixed(2) : ''),
            (pts >= 0 ? '+' : '') + pts.toFixed(2),
            Math.round(t._gross || 0),
            '-' + Math.round(t._tax || 0),
            Math.round(t._net || 0),
            (t.strategy || t.source || '')
          ];
        });
        const foot = [['TOTAL', '', '', '', '',
          (tpts >= 0 ? '+' : '') + tpts.toFixed(2),
          Math.round(tg), '-' + Math.round(ttx), Math.round(tn), '']];

        doc.autoTable({
          startY: sumParts.length ? 88 : 72,
          head: [['Symbol', 'Side', 'Qty', 'Entry', 'Exit', 'Points', 'Gross', 'Tax', 'Net', 'Strategy']],
          body: body,
          foot: foot,
          styles: { fontSize: 8, cellPadding: 3, overflow: 'linebreak' },
          headStyles: { fillColor: [31, 111, 235], textColor: 255 },
          footStyles: { fillColor: [235, 235, 235], textColor: 20, fontStyle: 'bold' },
          columnStyles: {
            2: { halign: 'right' }, 3: { halign: 'right' }, 4: { halign: 'right' },
            5: { halign: 'right' }, 6: { halign: 'right' }, 7: { halign: 'right' }, 8: { halign: 'right' }
          },
          didParseCell: function (d) {
            if (d.section === 'body' && (d.column.index === 5 || d.column.index === 8)) {
              const v = parseFloat(d.cell.raw);
              if (!isNaN(v)) d.cell.styles.textColor = v >= 0 ? [22, 120, 60] : [200, 40, 40];
            }
          }
        });

        doc.save('completed_trades_' + (date || 'report') + '.pdf');
        if (btn) { const o = btn.textContent; btn.textContent = '✓ Saved'; setTimeout(() => { btn.textContent = o; }, 1500); }
      } catch (e) {
        console.error('exportCompletedPdf failed:', e);
        alert('PDF export fail: ' + (e && e.message ? e.message : e));
      }
    }

    function downloadCsvBlob(filename, csvContent) {
      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      if (navigator.msSaveBlob) { // IE 10+
        navigator.msSaveBlob(blob, filename);
      } else {
        const link = document.createElement("a");
        if (link.download !== undefined) {
          const url = URL.createObjectURL(blob);
          link.setAttribute("href", url);
          link.setAttribute("download", filename);
          link.style.visibility = 'hidden';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
        }
      }
    }

    function exportCompletedCsv(btn) {
      const d = window._lastOrdersData || {};
      const det = d.details || [];
      if (!det.length) {
        alert('Export karne ke liye koi trades nahi hain.');
        return;
      }

      // Replace 'tags' column with separate columns for Source, Mode, Strategy, and Broker
      let colsToExport = [];
      COMPLETED_COLS_DEF.forEach(c => {
        if (c.id === 'chart' || c.id === 'actions') return;
        if (c.id === 'tags') {
          // 'strategy' now has its own dedicated column (flows through the else branch);
          // tags only explodes to Source/Mode/Broker to avoid a duplicate Strategy col.
          colsToExport.push({ id: 'source', l: 'Source' });
          colsToExport.push({ id: 'mode', l: 'Mode' });
          colsToExport.push({ id: 'broker', l: 'Broker' });
        } else {
          colsToExport.push(c);
        }
      });

      let csvLines = [];
      // Header
      csvLines.push(colsToExport.map(c => `"${c.l.replace(/"/g, '""')}"`).join(','));

      // Rows
      det.forEach(t => {
        const row = colsToExport.map(c => {
          let val = '';
          const g = t._gross || 0, tx = t._tax || 0, n = t._net || 0;
          const pts = t.entry === 'BUY' ? (t.exit_price || 0) - (t.entry_price || 0) : (t.entry_price || 0) - (t.exit_price || 0);
          const inv = t.qty * (t.entry_price || 0);
          const retPct = inv > 0 ? ((n / inv) * 100).toFixed(2) + '%' : '—';

          switch (c.id) {
            case 'date': val = t.entry_date || ''; break;
            case 'symbol': val = t.sym || ''; break;
            case 'instrument': val = _instrOf(t); break;
            case 'source': val = t.source || ''; break;
            case 'mode': val = t.mode || ''; break;
            case 'strategy': val = regLabel(t.strategy || t.strat || t.strategy_type || ''); break;
            case 'broker': val = t.broker || ''; break;
            case 'side': val = t.entry || ''; break;
            case 'entry_px': val = t.entry_price || 0; break;
            case 'exit_px': val = t.exit_price || 0; break;
            case 'entry_time': val = t.entry_time || ''; break;
            case 'exit_time': val = t.exit_time || ''; break;
            case 'exit_reason': val = t.exit_reason || ''; break;
            case 'duration': val = _durFmt(t.entry_time, t.exit_time) || ''; break;
            case 'qty': val = t.qty || 0; break;
            case 'points': val = pts.toFixed(2); break;
            case 'gross': val = Math.round(g); break;
            case 'tax': val = Math.round(tx); break;
            case 'net': val = Math.round(n); break;
            case 'ret_pct': val = retPct; break;
            case 'run_up':
              let max_val = '—';
              if (t.tags) {
                let max_ltp = null;
                t.tags.forEach(tg => { if (tg.startsWith('MAX_LTP:')) max_ltp = parseFloat(tg.split(':')[1]); });
                if (max_ltp !== null && t.entry_price > 0) {
                  let pnl = (max_ltp - t.entry_price) * t.qty;
                  if (t.entry === 'SELL') {
                    let min_ltp = null;
                    t.tags.forEach(tg => { if (tg.startsWith('MIN_LTP:')) min_ltp = parseFloat(tg.split(':')[1]); });
                    pnl = (t.entry_price - min_ltp) * t.qty;
                  }
                  if (pnl > 0) max_val = Math.round(pnl);
                }
              }
              val = max_val;
              break;
            case 'run_down':
              let min_val = '—';
              if (t.tags) {
                let min_ltp = null;
                t.tags.forEach(tg => { if (tg.startsWith('MIN_LTP:')) min_ltp = parseFloat(tg.split(':')[1]); });
                if (min_ltp !== null && t.entry_price > 0) {
                  let pnl = (min_ltp - t.entry_price) * t.qty;
                  if (t.entry === 'SELL') {
                    let max_ltp = null;
                    t.tags.forEach(tg => { if (tg.startsWith('MAX_LTP:')) max_ltp = parseFloat(tg.split(':')[1]); });
                    pnl = (t.entry_price - max_ltp) * t.qty;
                  }
                  if (pnl < 0) min_val = Math.round(pnl);
                }
              }
              val = min_val;
              break;
            case 'cumulative': val = t._cumulative != null ? Math.round(t._cumulative) : ''; break;
            default: val = t[c.id] || ''; break;
          }
          return `"${String(val).replace(/"/g, '""')}"`;
        }).join(',');
        csvLines.push(row);
      });

      const csvContent = csvLines.join('\n');
      const filename = `completed_trades_${window._completedDate || new Date().toISOString().slice(0, 10)}.csv`;
      downloadCsvBlob(filename, csvContent);
    }

    function exportCalPointsCsv(btn) {
      let tradesList = [...(window.currentCalendarTrades || [])];
      if (window.calSelectedDateFilter) {
        tradesList = tradesList.filter(t => (t.exit_date || t.entry_date) === window.calSelectedDateFilter);
      }

      if (!tradesList.length) {
        alert('Export karne ke liye koi trades nahi hain.');
        return;
      }

      // Sort them descending
      tradesList.sort((a, b) => {
        const da = a.entry_date + ' ' + (a.entry_time || '00:00');
        const db = b.entry_date + ' ' + (b.entry_time || '00:00');
        return db.localeCompare(da);
      });

      // Replace 'tags' column with separate columns for Source, Mode, Strategy, and Broker
      let colsToExport = [];
      COMPLETED_COLS_DEF.forEach(c => {
        if (c.id === 'chart' || c.id === 'actions') return;
        if (c.id === 'tags') {
          // 'strategy' now has its own dedicated column (flows through the else branch);
          // tags only explodes to Source/Mode/Broker to avoid a duplicate Strategy col.
          colsToExport.push({ id: 'source', l: 'Source' });
          colsToExport.push({ id: 'mode', l: 'Mode' });
          colsToExport.push({ id: 'broker', l: 'Broker' });
        } else {
          colsToExport.push(c);
        }
      });

      let csvLines = [];
      csvLines.push(colsToExport.map(c => `"${c.l.replace(/"/g, '""')}"`).join(','));

      tradesList.forEach(t => {
        const row = colsToExport.map(c => {
          let val = '';
          const g = t._gross || 0, tx = t._tax || 0, n = t._net || 0;
          const pts = t.entry === 'BUY' ? (t.exit_price || 0) - (t.entry_price || 0) : (t.entry_price || 0) - (t.exit_price || 0);
          const inv = t.qty * (t.entry_price || 0);
          const retPct = inv > 0 ? ((n / inv) * 100).toFixed(2) + '%' : '—';

          switch (c.id) {
            case 'date': val = t.entry_date || t.exit_date || ''; break;
            case 'symbol': val = t.sym || t.symbol || ''; break;
            case 'source': val = t.source || ''; break;
            case 'mode': val = t.mode || ''; break;
            case 'strategy': val = regLabel(t.strategy || t.strat || t.strategy_type || ''); break;
            case 'broker': val = t.broker || ''; break;
            case 'side': val = t.entry || ''; break;
            case 'entry_px': val = t.entry_price || 0; break;
            case 'exit_px': val = t.exit_price || 0; break;
            case 'entry_time': val = t.entry_time || ''; break;
            case 'exit_time': val = t.exit_time || ''; break;
            case 'exit_reason': val = t.exit_reason || ''; break;
            case 'duration': val = _durFmt(t.entry_time, t.exit_time) || ''; break;
            case 'qty': val = t.qty || 0; break;
            case 'points': val = pts.toFixed(2); break;
            case 'gross': val = Math.round(g); break;
            case 'tax': val = Math.round(tx); break;
            case 'net': val = Math.round(n); break;
            case 'ret_pct': val = retPct; break;
            case 'run_up':
              let max_val = '—';
              if (t.tags) {
                let max_ltp = null;
                t.tags.forEach(tg => { if (tg.startsWith('MAX_LTP:')) max_ltp = parseFloat(tg.split(':')[1]); });
                if (max_ltp !== null && t.entry_price > 0) {
                  let pnl = (max_ltp - t.entry_price) * t.qty;
                  if (t.entry === 'SELL') {
                    let min_ltp = null;
                    t.tags.forEach(tg => { if (tg.startsWith('MIN_LTP:')) min_ltp = parseFloat(tg.split(':')[1]); });
                    pnl = (t.entry_price - min_ltp) * t.qty;
                  }
                  if (pnl > 0) max_val = Math.round(pnl);
                }
              }
              val = max_val;
              break;
            case 'run_down':
              let min_val = '—';
              if (t.tags) {
                let min_ltp = null;
                t.tags.forEach(tg => { if (tg.startsWith('MIN_LTP:')) min_ltp = parseFloat(tg.split(':')[1]); });
                if (min_ltp !== null && t.entry_price > 0) {
                  let pnl = (min_ltp - t.entry_price) * t.qty;
                  if (t.entry === 'SELL') {
                    let max_ltp = null;
                    t.tags.forEach(tg => { if (tg.startsWith('MAX_LTP:')) max_ltp = parseFloat(tg.split(':')[1]); });
                    pnl = (t.entry_price - max_ltp) * t.qty;
                  }
                  if (pnl < 0) min_val = Math.round(pnl);
                }
              }
              val = min_val;
              break;
            case 'cumulative': val = ''; break;
            default: val = t[c.id] || ''; break;
          }
          return `"${String(val).replace(/"/g, '""')}"`;
        }).join(',');
        csvLines.push(row);
      });

      const csvContent = csvLines.join('\n');
      const filename = `calendar_trades_${window.calSelectedDateFilter || 'filtered'}.csv`;
      downloadCsvBlob(filename, csvContent);
    }

    function exportCalPointsPdf() {
      const cols = (window._calPointsCols || []).filter(c => c.on);
      const trades = window._lastCalPointsTrades || window.currentCalendarTrades || [];
      if (!trades.length) { alert('No trades to export.'); return; }

      const dateLabel = window.calSelectedDateFilter || 'All dates';
      const thead = cols.map(c => `<th style="padding:6px 10px;border:1px solid #ccc;background:#f5f5f5;font-size:11px;">${c.l}</th>`).join('');

      const rows = trades.map(t => {
        const pts = t.entry === 'BUY' ? (t.exit_price || 0) - (t.entry_price || 0) : (t.entry_price || 0) - (t.exit_price || 0);
        const g = t._gross || 0, tx = t._tax || 0, n = t._net || 0;
        const inv = (t.qty || 0) * (t.entry_price || 0);
        return '<tr>' + cols.map(c => {
          let val = '';
          switch (c.id) {
            case 'date': val = t.entry_date || t.exit_date || ''; break;
            case 'symbol': val = t.sym || t.symbol || ''; break;
            case 'side': val = t.entry || ''; break;
            case 'entry_px': val = t.entry_price || 0; break;
            case 'exit_px': val = t.exit_price || 0; break;
            case 'entry_time': val = t.entry_time || ''; break;
            case 'exit_time': val = t.exit_time || ''; break;
            case 'exit_reason': val = (t.exit_reason || '').split(':')[0]; break;
            case 'qty': val = t.qty || 0; break;
            case 'points': val = pts.toFixed(2); break;
            case 'gross': val = Math.round(g); break;
            case 'tax': val = Math.round(tx); break;
            case 'net': val = Math.round(n); break;
            case 'ret_pct': val = inv > 0 ? ((n / inv) * 100).toFixed(2) + '%' : '—'; break;
            default: val = t[c.id] || '';
          }
          const color = (c.id === 'net' || c.id === 'gross') ? (Number(val) >= 0 ? '#196127' : '#9e1c1c') : '';
          return `<td style="padding:5px 10px;border:1px solid #e0e0e0;font-size:11px;${color ? 'color:' + color + ';font-weight:700;' : ''}">${val}</td>`;
        }).join('') + '</tr>';
      }).join('');

      const html = `<!DOCTYPE html><html><head><title>Trades - ${dateLabel}</title>
      <style>body{font-family:Arial,sans-serif;margin:20px}h2{font-size:14px;margin-bottom:8px}table{border-collapse:collapse;width:100%}@media print{@page{size:landscape}}</style>
      </head><body>
      <h2>Point Per Trade Details &mdash; ${dateLabel}</h2>
      <table><thead><tr>${thead}</tr></thead><tbody>${rows}</tbody></table>
      <script>window.onload=function(){window.print();}<\/script>
      </body></body></html>`;

      const w = window.open('', '_blank', 'width=1200,height=800');
      if (w) { w.document.write(html); w.document.close(); }
    }

    // Close export dropdown when clicking outside
    document.addEventListener('click', function () {
      const m = document.getElementById('cal-export-menu');
      if (m) m.style.display = 'none';
    });

    // ── P3: per-position MIS ⇄ NRML (carry overnight) ──────────────────────────
    // MIS = 3:15 auto square-off (default). NRML = carry past 3:15 (pos_monitor
    // skips EOD squareoff for this position). GROUP-WIDE: keyed on group_id so a
    // straddle's both legs carry with one toggle. Day-scoped server-side (agle din
    // wapas MIS). Keys cache re-render pe fresh (renderCachedOrders poll-driven).
    window._carryKeys = window._carryKeys || [];
    window._carryHas = function (gid, id) {
      const k = (gid && String(gid).trim()) ? String(gid) : ('id:' + id);
      return (window._carryKeys || []).indexOf(k) !== -1;
    };
    // self-contained toast — main Orders page pe koi global `toast` nahi hai
    // (sirf stats2 me fallback), isliye pehle "toast nahi aa raha" tha. Ye apna
    // transient div banata hai (jis bhi toast global ho use prefer karta hai).
    window._carryToast = function (msg) {
      try { if (typeof toast === 'function') { toast(msg); return; } } catch (e) {}
      if (window.toast && window.toast !== window._carryToast) { try { window.toast(msg); return; } catch (e) {} }
      let el = document.getElementById('_carryToastBox');
      if (!el) {
        el = document.createElement('div');
        el.id = '_carryToastBox';
        el.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:99999;background:#161b22;border:1px solid #30363d;color:#e6edf3;padding:9px 16px;border-radius:8px;font-size:13px;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,.5);transition:opacity .3s;pointer-events:none';
        document.body.appendChild(el);
      }
      el.textContent = msg; el.style.opacity = '1';
      clearTimeout(window._carryToastT);
      window._carryToastT = setTimeout(function () { el.style.opacity = '0'; }, 2400);
    };
    window._loadCarryKeys = async function (rerender) {
      try {
        const d = await (await fetch('/api/position-carry')).json();
        if (d && d.ok) window._carryKeys = d.keys || [];
      } catch (e) {}
      if (rerender) { try { renderCachedOrders(); } catch (e) {} }
    };
    window.togglePosCarry = async function (gid, id) {
      const cur = window._carryHas(gid, id);
      try {
        const d = await (await fetch('/api/position-carry', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ group_id: gid || '', id: id, on: !cur })
        })).json();
        if (d && d.ok) { window._carryToast((d.on ? '🌙 ' : '☀️ ') + (d.msg || ('carry ' + (d.on ? 'ON' : 'OFF')))); await window._loadCarryKeys(true); }
        else window._carryToast('⚠️ ' + ((d && d.msg) || 'carry toggle fail'));
      } catch (e) { window._carryToast('⚠️ carry fail: ' + e.message); }
    };
    if (!window._carryKeysInit) { window._carryKeysInit = true; window._loadCarryKeys(false); }

    // Header "✕ Close all (N)" — us strategy-group ki SAARI positions ek click me
    // band (confirm ke saath). Dedupe by group_id: hedge/straddle group_id ek baar
    // closePositionGroup se (siblings saath), solo legs closePosition se. Reuses the
    // existing tested close paths (koi naya order code nahi). Group ka mode row se.
    window.closeAllInGroup = async function (grpId, label, n) {
      const list = (window._grpItems && window._grpItems[grpId]) || [];
      if (!list.length) return;
      if (!confirm('Close ALL ' + n + ' position(s) in "' + label + '"?\n(har position apne mode — paper/live — pe band hogi)')) return;
      const doneGroups = {};
      for (const p of list) {
        try {
          if (p.group_id) { if (!doneGroups[p.group_id]) { doneGroups[p.group_id] = 1; await closePositionGroup(p.group_id, p.mode); } }
          else { await closePosition(p.sym, p.entry, p.qty, p.mode, p.source, p.strategy, ''); }
        } catch (e) {}
      }
      window._carryToast('Close-all bheja — ' + n + ' position(s) [' + label + ']');
    };

    function renderCachedOrders() {
      if (!window._ordCompletedCols || !window._ordOpenCols) _loadOrdColPrefs();
      const d = window._lastOrdersData || {};
      const det = d.details || [];
      const opn = d.open || [];
      const date = (document.getElementById('ord-date') || {}).value;
      const ordDate = date;

      const _grpSel = document.getElementById('ord-group-select');
      if (_grpSel) {
        const _gm = window._completedGroupMode || 'none';
        if (_grpSel.value !== _gm) _grpSel.value = _gm;
        const _on = _gm !== 'none';
        _grpSel.style.background = _on ? '#1f6feb' : '#161b22';
        _grpSel.style.color = _on ? '#fff' : '#adbac7';
        _grpSel.style.borderColor = _on ? '#1f6feb' : '#30363d';
      }
      // Collapse/Expand-all button: shown whenever grouping is ON (visibility from the
      // MODE, which is current here; _completedGroupKeys is set later during the grouped
      // render so it's one cycle behind — fine for the label only).
      const _grpColBtn = document.getElementById('ord-group-collapse-btn');
      if (_grpColBtn) {
        const _gm2 = window._completedGroupMode || 'none';
        if (_gm2 === 'none') {
          _grpColBtn.style.display = 'none';
        } else {
          _grpColBtn.style.display = '';
          const _keys = window._completedGroupKeys || [];
          const _set = window._completedGroupExpanded || new Set();
          const _allOpen = _keys.length > 0 && _keys.every(k => _set.has(k));
          _grpColBtn.textContent = _allOpen ? '⊟ Collapse all' : '⊞ Expand all';
        }
      }

      // Instrument Ctrl+click filter applies to Open Positions too (same chip, same filter).
      const _instrOk = t => !window._ordInstrFilter || _instrOf(t) === window._ordInstrFilter;
      const isCapBlocked = t => (t.tags || []).some(tg => tg === 'CAPITAL_BLOCKED');
      const opnBlocked = opn.filter(t => isCapBlocked(t) && _instrOk(t));
      const opnReal = opn.filter(t => !isCapBlocked(t) && _instrOk(t));

      // ── SOURCE SUMMARY (STRATEGY, MANUAL, WEBHOOK) ──
      const _z = () => ({ net: 0, gross: 0, n: 0, w: 0, opn: 0, ru: 0, rd: 0, tax: 0, orows: [] });
      const srcTotals = { 'STRATEGY': _z(), 'MANUAL': _z(), 'WEBHOOK': _z() };
      det.forEach(t => {
        let s = String(t.source || 'STRATEGY').toUpperCase();
        if (s !== 'MANUAL' && s !== 'WEBHOOK') s = 'STRATEGY';
        const ru = _tradeRunAmts(t);
        srcTotals[s].net += t._net; srcTotals[s].gross += t._gross; srcTotals[s].n++;
        srcTotals[s].ru += ru.up; srcTotals[s].rd += ru.down;
        srcTotals[s].tax += (t._tax || 0);
        if (t._net > 0) srcTotals[s].w++;
      });
      opnReal.forEach(t => {
        let s = String(t.source || 'STRATEGY').toUpperCase();
        if (s !== 'MANUAL' && s !== 'WEBHOOK') s = 'STRATEGY';
        srcTotals[s].opn++;
        srcTotals[s].orows.push({ sym: t.sym, entry: Number(t.entry_price || 0), side: t.entry, qty: Number(t.qty || 0) });
      });

      // ── CONSOLIDATED SUMMARY (task 66) — now clickable per strategy (task 73) ──
      const summaryRows = [];

      // per-strategy accumulation (data logic unchanged)
      const stratTotals = {};
      if (typeof RUNNING_PIDS === 'object' && RUNNING_PIDS !== null) {
        Object.keys(RUNNING_PIDS).forEach(k => {
          if (!k.endsWith('_mode') && k !== '_risk' && k !== 'webhooks' && k !== '_ui_config') {
            stratTotals[k] = _z();
          }
        });
      }
      det.forEach(t => {
        let s = String(t.source || 'STRATEGY').toUpperCase();
        if (s !== 'MANUAL' && s !== 'WEBHOOK') {
          const sName = t.strategy || 'STRATEGY';
          if (!stratTotals[sName]) stratTotals[sName] = _z();
          const ru = _tradeRunAmts(t);
          stratTotals[sName].net += t._net;
          stratTotals[sName].gross += t._gross;
          stratTotals[sName].n++;
          stratTotals[sName].ru += ru.up; stratTotals[sName].rd += ru.down;
          stratTotals[sName].tax += (t._tax || 0);
          if (t._net > 0) stratTotals[sName].w++;
        }
      });
      opnReal.forEach(t => {
        let s = String(t.source || 'STRATEGY').toUpperCase();
        if (s !== 'MANUAL' && s !== 'WEBHOOK') {
          const sName = t.strategy || 'STRATEGY';
          if (!stratTotals[sName]) stratTotals[sName] = _z();
          stratTotals[sName].opn++;
          stratTotals[sName].orows.push({ sym: t.sym, entry: Number(t.entry_price || 0), side: t.entry, qty: Number(t.qty || 0) });
        }
      });

      Object.keys(stratTotals).sort().forEach(s => {
        const st = stratTotals[s];
        const isRunning = typeof RUNNING_PIDS === 'object' && RUNNING_PIDS !== null && RUNNING_PIDS[s] !== undefined;
        if (!isRunning && st.n === 0 && st.opn === 0) return;
        const mode = isRunning ? (RUNNING_PIDS[s + '_mode'] || 'paper') : 'stopped';
        summaryRows.push({ key: s, code: regId(s), name: regLabel(s), isSource: false, mode: mode,
          net: st.net, gross: st.gross, n: st.n, w: st.w, opn: st.opn, ru: st.ru, rd: st.rd, tax: st.tax, orows: st.orows });
      });
      // MANUAL / WEBHOOK — not per-strategy, kept as their own rows
      ['MANUAL', 'WEBHOOK'].forEach(s => {
        const st = srcTotals[s];
        if (st.n === 0 && st.opn === 0) return;
        summaryRows.push({ key: s, code: '', name: s.charAt(0) + s.slice(1).toLowerCase(), isSource: true, mode: 'src',
          net: st.net, gross: st.gross, n: st.n, w: st.w, opn: st.opn, ru: st.ru, rd: st.rd, tax: st.tax, orows: st.orows });
      });
      // task 80 — open rows per summary key, for the LIVE Running column
      // (patched by _patchPeakRunCells from the same _ltpLive feed as Open Positions)
      window._peakOpenMap = {};
      summaryRows.forEach(r => { window._peakOpenMap[r.key] = r.orows || []; });

      renderStratSummaryTable(summaryRows);
      // retire the old tiles container
      const _ordSum = document.getElementById('ord-summary');
      if (_ordSum) { _ordSum.innerHTML = ''; _ordSum.style.display = 'none'; }

      // ── COMPLETED TRADES ──
      // Client-side scope filter: when a Summary row is picked (task 73), narrow
      // the Completed Trades table to that strategy/source without touching the
      // orders fetch (so the Summary table above stays full).
      // Exit-reason dropdown (mirrors Stats' cal-exit-reason) — populate from this
      // fetch's completed trades; shares window._ordExitFilter with the Ctrl+click.
      const _erSel = document.getElementById('ord-exit-reason');
      if (_erSel) {
        const _erMap = [
          ['SL_HIT', '🛑 Stop-Loss'], ['TP_HIT', '🎯 Target'], ['EXPIRY_ITM_SQUAREOFF', '📅 Expiry ITM'],
          ['EXPIRY_EOD_SQUAREOFF', '📅 Expiry EOD (2:55)'], ['EOD_315_SQUAREOFF', '⏰ 3:15 EOD'],
          ['KILL_FLOOR', '🔒 Kill-Floor'], ['TRAILING_PROFIT_LOCK', '🔒 Trailing Lock'],
          ['DEFAULT_TSL_TARGET', '🎯 Aggr-Trail Target'], ['DEFAULT_TSL_SL', '🛡️ Aggr-Trail SL'],
          ['RMS_MAXLOSS', '⚠️ RMS Daily Max-Loss'], ['RMS_PROFIT_TARGET', '✅ RMS Daily Target'],
          ['NO_PRICE_EMERGENCY_EXIT', '🚨 No-Price Emergency'], ['ATR_TRAILING', '📉 ATR Trailing'],
          ['RSI_MIDLINE_EXIT', '↩️ RSI Midline'], ['IDX_TRAIL', '📉 Index Trail SL'], ['TRAIL_SL', '📉 Trail SL'],
          ['TARGET', '🎯 Target'], ['GLOBAL_CAP', '🚫 Max Trades/Day'], ['SQUAREOFF_315', '⏰ 3:15 EOD (Webhook)'],
          ['REVERSAL', '🔄 Reversal'], ['TV_EXIT', '📡 TV Exit Signal'], ['MANUAL_CLOSE', '✋ Manual Close'],
          ['EXTERNALLY_CLOSED', '🌐 Closed at Broker'], ['MANUAL_EXIT_BROKER', '🌐 Closed at Broker'],
        ];
        const _pfxOf = raw => { if (!raw) return ''; for (const [p] of _erMap) { if (raw.startsWith(p)) return p; } return (raw.split(':')[0] || raw); };
        const _lblOf = pfx => { for (const [p, l] of _erMap) { if (p === pfx) return l; } return pfx; };
        const _seen = [...new Set(det.map(t => _pfxOf(t.exit_reason || '')).filter(Boolean))].sort();
        const _cur = window._ordExitFilter || '';
        _erSel.innerHTML = '<option value="">All Exit Reasons</option>' +
          _seen.map(p => `<option value="${p}"${p === _cur ? ' selected' : ''}>${_lblOf(p)}</option>`).join('');
        _erSel.value = _cur;
      }
      let sortedCompleted = det.filter(_peakTradeMatch)
        .filter(t => !window._ordExitFilter || (t.exit_reason || '').split(':')[0] === window._ordExitFilter)
        .filter(t => !window._ordInstrFilter || _instrOf(t) === window._ordInstrFilter);
      // header chip showing the active per-strategy + exit-reason filters (clearable)
      const _cf = document.getElementById('ord-completed-filter');
      if (_cf) {
        const _pkf = window._peakStrat || '__all';
        let _cfHtml = (_pkf === '__all') ? '' :
          `<span style="font-size:11px;font-weight:400;color:#58a6ff;margin-left:6px;cursor:pointer" onclick="peakClearStrat()" title="Filter hatao (All)">▸ ${(_pkf === 'MANUAL' || _pkf === 'WEBHOOK') ? (_pkf.charAt(0) + _pkf.slice(1).toLowerCase()) : (regLabel(_pkf) || _pkf)} ✕</span>`;
        if (window._ordExitFilter) {
          _cfHtml += `<span style="font-size:11px;font-weight:400;color:#d29922;margin-left:6px;cursor:pointer" onclick="_ordExitClear()" title="Exit-reason filter hatao (All)">▸ Exit: ${window._ordExitFilter} ✕</span>`;
        }
        if (window._ordInstrFilter) {
          _cfHtml += `<span style="font-size:11px;font-weight:400;color:#b083f0;margin-left:6px;cursor:pointer" onclick="_ordInstrClear()" title="Instrument filter hatao (All)">▸ Instrument: ${window._ordInstrFilter} ✕</span>`;
        }
        // Strategy filter (top #ord-strat dropdown — what a strategy-chip Ctrl+click sets):
        // give it a clearable ✕ pill here too, same as instrument/exit.
        const _stratSel = document.getElementById('ord-strat');
        if (_stratSel && _stratSel.value) {
          const _sTxt = String(((_stratSel.options[_stratSel.selectedIndex] || {}).text) || _stratSel.value).replace(/</g, '&lt;');
          _cfHtml += `<span style="font-size:11px;font-weight:400;color:#58a6ff;margin-left:6px;cursor:pointer" onclick="_ordStratFilterClear()" title="Strategy filter hatao (All)">▸ Strategy: ${_sTxt} ✕</span>`;
        }
        _cf.innerHTML = _cfHtml;
      }
      if (window._completedSortCol) {
        _sortData(sortedCompleted, window._completedSortCol, window._completedSortDir);
      } else {
        sortedCompleted.reverse(); // Default to original reverse order
      }

      // Cumulative P&L column (2026-07-02) — only meaningful in a fixed
      // chronological order, so it's computed ONLY when the table is sorted by
      // exit time ascending (oldest → newest) and not grouped by symbol (grouping
      // breaks the one-row-per-trade chronological sequence entirely). Any other
      // sort state leaves t._cumulative undefined, which _completedRowHtml()
      // renders as a blank "—" rather than a misleading running total.
      const _cumEligible = window._completedSortCol === 'exit_time'
        && window._completedSortDir === 'asc'
        && (window._completedGroupMode || 'none') === 'none';
      sortedCompleted.forEach(t => { delete t._cumulative; });
      if (_cumEligible) {
        let _run = 0;
        sortedCompleted.forEach(t => { _run += (t._net || 0); t._cumulative = _run; });
      }

      const _chartList = sortedCompleted.map(t => ({ sym: t.sym, side: t.entry, entry: t.entry_price, exit: t.exit_price, et: t.entry_time, xt: t.exit_time, qty: t.qty, date: t.entry_date || ordDate, strategy: t.strategy || '' }));
      localStorage.setItem('chartTradeList', JSON.stringify(_chartList));

      let _tot = { g: 0, tx: 0, n: 0, pts: 0, inv: 0 };
      let ch = sortedCompleted.length ? '' : '<div style="color:#6e7681;font-size:12px;padding:6px">Is filter pe koi completed trade nahi</div>';
      if (sortedCompleted.length) {
        const activeCols = window._ordCompletedCols.filter(c => c.on);

        ch = '<table style="width:100%;border-collapse:collapse;font-size:11.5px"><thead><tr style="color:#8b949e;text-align:left;border-bottom:1px solid #30363d">';

        activeCols.forEach(c => {
          const isRight = ['entry_px', 'exit_px', 'points', 'gross', 'tax', 'net', 'ret_pct', 'run_up', 'run_down', 'cumulative'].includes(c.id);
          const isCenter = ['entry_time', 'exit_time', 'duration', 'actions'].includes(c.id);
          const alignStyle = isRight ? ';text-align:right' : (isCenter ? ';text-align:center' : '');

          let sortIndicator = '';
          let sortCursor = 'cursor:pointer;';
          if (c.id !== 'actions') {
            if (window._completedSortCol === c.id) {
              sortIndicator = window._completedSortDir === 'desc' ? ' ▼' : ' ▲';
            }
            // else: no indicator at all — only the currently-sorted column shows a
            // triangle, unsorted columns show just their name (2026-07-02 cleanup,
            // was a static ↕ on every column regardless of sort state).
          } else {
            sortCursor = '';
          }

          const onclickAttr = c.id !== 'actions' ? ` onclick="toggleSort('completed', '${c.id}')"` : '';
          ch += `<th style="padding:6px;font-weight:500;user-select:none;${sortCursor}${alignStyle}"${onclickAttr}>${c.l}${sortIndicator}</th>`;
        });

        ch += '</tr></thead><tbody>';

        const _grpMode = window._completedGroupMode || 'none';
        if (_grpMode !== 'none') {
          // ── Grouped (Zerodha Day's History style) — key/label by _grpMode ──
          // (none/symbol/strategy/pnl/hedge/exit/instrument). Preserves
          // sortedCompleted's current order for group POSITION (first-occurrence),
          // respecting whatever sort column is active. Each group shows its own total.
          const groups = [];
          const groupIdx = {};
          sortedCompleted.forEach((t, i) => {
            const kl = _grpKeyLabel(t, _grpMode);
            if (!(kl.key in groupIdx)) { groupIdx[kl.key] = groups.length; groups.push({ key: kl.key, label: kl.label, trades: [], g: 0, tx: 0, n: 0, pts: 0, inv: 0, qty: 0 }); }
            groups[groupIdx[kl.key]].trades.push({ t, idx: i });
          });
          groups.forEach(grp => {
            grp.trades.forEach(({ t }) => {
              grp.g += t._gross; grp.tx += t._tax; grp.n += t._net; grp.qty += (t.qty || 0);
              grp.inv += t.qty * (t.entry_price || 0);
              grp.pts += t.entry === 'BUY' ? (t.exit_price || 0) - (t.entry_price || 0) : (t.entry_price || 0) - (t.exit_price || 0);
            });
            _tot.g += grp.g; _tot.tx += grp.tx; _tot.n += grp.n; _tot.pts += grp.pts; _tot.inv += grp.inv;
          });
          window._completedGroupKeys = groups.map(g => g.key);   // for the Collapse/Expand-all button

          groups.forEach(grp => {
            const expanded = window._completedGroupExpanded.has(grp.key);
            const gnc = grp.n > 0 ? '#3fb950' : (grp.n < 0 ? '#f85149' : '#e6edf3');
            const ggc = grp.g > 0 ? '#3fb950' : (grp.g < 0 ? '#f85149' : '#e6edf3');
            const gptsC = grp.pts > 0 ? '#3fb950' : (grp.pts < 0 ? '#f85149' : '#8b949e');
            const gRetPct = grp.inv > 0 ? ((grp.n / grp.inv) * 100).toFixed(2) + '%' : '—';
            const grc = grp.n > 0 ? '#3fb950' : (grp.n < 0 ? '#f85149' : '#8b949e');
            const grpKeySafe = String(grp.key).replace(/\\/g, '\\\\').replace(/'/g, "\\'");

            ch += `<tr style="border-bottom:1px solid #21262d;background:#161b22;cursor:pointer" onclick="toggleCompletedGroupExpand('${grpKeySafe}')">`;
            activeCols.forEach(c => {
              let val = '', colorStyle = '';
              const isRight = ['entry_px', 'exit_px', 'points', 'gross', 'tax', 'net', 'ret_pct', 'run_up', 'run_down', 'cumulative'].includes(c.id);
              const isCenter = ['entry_time', 'exit_time', 'duration', 'actions', 'chart'].includes(c.id);
              const alignStyle = isRight ? 'text-align:right;' : (isCenter ? 'text-align:center;' : '');
              switch (c.id) {
                case 'symbol':
                  val = `<span style="display:inline-block;width:14px;color:#8b949e">${expanded ? '▼' : '▶'}</span><b>${grp.label}</b> <span style="color:#6e7681;font-weight:400">(${grp.trades.length})</span>`;
                  colorStyle = 'color:#adbac7;font-weight:600;';
                  break;
                case 'qty': val = grp.qty; break;
                case 'points':
                  val = (grp.pts >= 0 ? '+' : '') + grp.pts.toFixed(2);
                  colorStyle = 'color:' + gptsC + ';';
                  break;
                case 'gross': val = Math.round(grp.g); colorStyle = 'color:' + ggc + ';'; break;
                case 'tax': val = '−' + Math.round(grp.tx); colorStyle = 'color:#f85149;'; break;
                case 'net': val = Math.round(grp.n); colorStyle = 'color:' + gnc + ';font-weight:700;'; break;
                case 'ret_pct': val = gRetPct; colorStyle = 'color:' + grc + ';'; break;
                default: val = ''; break;
              }
              ch += `<td style="padding:7px 6px;${alignStyle}${colorStyle}">${val}</td>`;
            });
            ch += '</tr>';

            if (expanded) {
              grp.trades.forEach(({ t, idx }) => {
                ch += _completedRowHtml(t, idx, activeCols, ordDate, true).html;
              });
            }
          });
        } else {
          // ── Flat (default) ──
          window._completedGroupKeys = [];   // no groups → Collapse/Expand-all button hides
          sortedCompleted.forEach((t, _idx) => {
            const r = _completedRowHtml(t, _idx, activeCols, ordDate, false);
            _tot.g += r.g; _tot.tx += r.tx; _tot.n += r.n; _tot.inv += r.inv; _tot.pts += r.pts;
            ch += r.html;
          });
        }

        const _totRetPct = _tot.inv > 0 ? ((_tot.n / _tot.inv) * 100).toFixed(2) + '%' : '—';
        ch += '</tbody><tfoot><tr style="border-top:2px solid #30363d;font-weight:700">';

        activeCols.forEach((c, idx) => {
          let val = '';
          let colorStyle = '';

          if (idx === 0) {
            val = 'TOTAL';
            colorStyle = 'color:#8b949e;';
          } else {
            switch (c.id) {
              case 'points':
                val = (_tot.pts >= 0 ? '+' : '') + _tot.pts.toFixed(2);
                colorStyle = 'color:' + (_tot.pts >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              case 'gross':
                val = Math.round(_tot.g);
                colorStyle = 'color:' + (_tot.g >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              case 'tax':
                val = '−' + Math.round(_tot.tx);
                colorStyle = 'color:#f85149;';
                break;
              case 'net':
                val = Math.round(_tot.n);
                colorStyle = 'color:' + (_tot.n >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
              case 'ret_pct':
                val = _totRetPct;
                colorStyle = 'color:' + (_tot.n >= 0 ? '#3fb950' : '#f85149') + ';';
                break;
            }
          }

          const isRight = ['entry_px', 'exit_px', 'points', 'gross', 'tax', 'net', 'ret_pct', 'run_up', 'run_down', 'cumulative'].includes(c.id);
          const isCenter = ['entry_time', 'exit_time', 'duration', 'actions'].includes(c.id);
          const alignStyle = isRight ? 'text-align:right;' : (isCenter ? 'text-align:center;' : '');

          ch += `<td style="padding:7px 6px;${alignStyle}${colorStyle}">${val}</td>`;
        });

        ch += '</tr></tfoot></table>';
      }
      _setHtml(document.getElementById('ord-completed'), ch);
      window._realizedTot = _tot;   // store for NET panel
      window._completedData = sortedCompleted;   // for 📄 Export PDF
      window._completedDate = ordDate;
      window._completedSrcTotals = srcTotals;
      _updateNetPanel(0);           // refresh NET panel immediately after render (no open pos yet)

      // ── OPEN POSITIONS ──
      const ordOpenEl = document.getElementById('ord-open');
      const activeOpenColsStr = window._ordOpenCols.filter(c => c.on).map(c => c.id).join(',');
      // raw-id-ok: ye fingerprint hai (re-render chahiye ya nahi), user ko dikhta nahi.
      // Label lagana yahan galat hoga — do strategies ka naam ek jaisa ho to collide karega.
      const openFp = opnReal.map(t => `${t.sym}|${t.entry}|${t.entry_price}|${t.qty}|${t.mode}|${t.source}|${t.strategy}`).join(',')
        + '|blocked:' + opnBlocked.map(t => t.id).join(',')
        + '|sort:' + window._openSortCol + '|dir:' + window._openSortDir
        + '|cols:' + activeOpenColsStr;

      if (!opnReal.length && !opnBlocked.length) {
        _setHtml(ordOpenEl, '<div style="color:#6e7681;font-size:12px;padding:6px">Koi open position nahi</div>');
        ordOpenEl.dataset.fp = '';
        _patchLtpCells();
      } else if (ordOpenEl.dataset.fp === openFp) {
        _patchLtpCells();
      } else {
        try { // catch render errors so blank section doesn't appear silently
          let blockedHtml = '';
          if (opnBlocked.length) {
            blockedHtml = `<details ${_grpOpenAttr('grp_blocked')} ontoggle="_grpToggleSave('grp_blocked', this.open)" style="margin-bottom:12px;background:#21262d;border:1px solid #6e2c2c;border-radius:6px">
        <style>details > summary { list-style: none; } details > summary::-webkit-details-marker { display: none; }</style>
        <summary style="padding:10px 14px;cursor:pointer;font-weight:600;color:#f85149;display:flex;justify-content:space-between;align-items:center;border-radius:6px" onmouseover="this.style.background='#2d1f1f'" onmouseout="this.style.background='transparent'">
          <span>🚫 Block hui Entries <span style="color:#8b949e;font-size:11px;font-weight:normal;margin-left:6px">RMS / capital / cap — reason per row</span><span style="color:#8b949e;font-size:12px;font-weight:normal;margin-left:6px">(${opnBlocked.length})</span></span>
          <span style="font-size:12px;color:#8b949e">Toggle ▾</span>
        </summary>
        <div style="padding:0 14px 12px;overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:11.5px"><thead><tr style="color:#8b949e;text-align:left;border-bottom:1px solid #30363d">
          <th style="padding:5px 6px">Date</th><th style="padding:5px 6px">Time</th><th style="padding:5px 6px">Symbol</th><th style="padding:5px 6px">Strategy</th>
          <th style="padding:5px 6px">Side</th><th style="padding:5px 6px;text-align:right">Qty @ Price</th><th style="padding:5px 6px">Reason</th><th style="padding:5px 6px;text-align:center">Chart</th></tr></thead><tbody>`
              + opnBlocked.map(t => {
                const reason = (t.tags || []).filter(tg => tg !== 'CAPITAL_BLOCKED').join(', ') || 'capital cap hit';
                const bDate = t.entry_date || ordDate;
                // Chart the trigger point on the option's premium chart: pass the
                // block/trigger time as entry (et), no exit (xt='') → single entry
                // marker at the exact time capital blocked this entry.
                const chartBtn = t.entry_time
                  ? `<button onclick="openTradeChart('${(t.sym || '').replace(/'/g, '')}','${t.entry || ''}',${t.entry_price || 0},0,'${t.entry_time}','',${t.qty || 0},'${bDate}',null,null,-1,null,null,'${(t.strategy || '').replace(/'/g, '')}')" title="Trigger point chart" style="padding:2px 8px;font-size:12px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#58a6ff;cursor:pointer">📈</button>`
                  : '—';
                return '<tr style="border-bottom:1px solid #30363d;opacity:.85">'
                  + '<td style="padding:6px">' + bDate + '</td>'
                  + '<td style="padding:6px;color:#8b949e;white-space:nowrap">' + (t.entry_time || '—') + '</td>'
                  + '<td style="padding:6px;color:#adbac7">' + t.sym + '</td>'
                  + '<td style="padding:6px;color:#8b949e">' + (t.strategy ? regLabel(t.strategy) : '—') + '</td>'
                  + '<td style="padding:6px;color:' + (t.entry === 'BUY' ? '#3fb950' : '#f85149') + '">' + t.entry + '</td>'
                  + '<td style="padding:6px;text-align:right;color:#8b949e">' + t.qty + ' @ ' + (Number(t.entry_price) > 0 ? Number(t.entry_price).toFixed(2) : '<span style="color:#6e7681">premium N/A</span>') + '</td>'
                  + '<td style="padding:6px;color:#f85149;font-size:10.5px">' + reason + '</td>'
                  + '<td style="padding:6px;text-align:center">' + chartBtn + '</td></tr>';
              }).join('')
              + '</tbody></table></div></details>';
          }

          // Grouping key (2026-07-30):
          //  • non-empty group_id → group by it. Manual same-minute orders share
          //    MANUAL_<sym>_<minute> (backend), hedge/straddle structures share their
          //    own group_id → ek structure / ek same-time batch = ek box.
          //  • empty group_id → group by STRATEGY (pre-P1 behaviour). A strategy's
          //    multiple stock legs (e.g. Ars-chain Canary ICICIBANK + AXISBANK) stay
          //    in ONE box. P1's solo_<id> split them per-position; user: "baaki jo
          //    jaisa tha waise hi rahega" — sirf manual same-time hi ek group me.
          let grouped = {};
          opnReal.forEach(t => {
            const key = (t.group_id && String(t.group_id).trim())
              ? String(t.group_id)
              : (t.strategy || t.source || 'MANUAL');
            if (!grouped[key]) grouped[key] = [];
            grouped[key].push(t);
          });

          let oh = '';
          const activeOpenCols = window._ordOpenCols.filter(c => c.on);

          for (let stratKey in grouped) {
            let items = grouped[stratKey].slice();

            if (window._openSortCol) {
              _sortData(items, window._openSortCol, window._openSortDir);
            }

            // stratKey ab group_id/solo hai — strategy/symbol/entry-time is group ke
            // pehle leg se lo (label + color + edit ke liye).
            const g0 = items[0] || {};
            let stratName = g0.strategy || g0.source || 'MANUAL';   // strategy → regLabel/color/edit
            let stratDesc = '';
            if (String(stratName).includes(' | ')) {
              let parts = String(stratName).split(' | ');
              stratName = parts[0];
              stratDesc = parts.slice(1).join(' | ');
            }
            // Symbol/time in the label ONLY when the whole group shares one (a single
            // structure or a same-symbol/same-time batch). A strategy-grouped box that
            // spans multiple stocks/times shows just the strategy name — no misleading
            // first-leg symbol/time.
            const _gSyms = new Set(items.map(x => x.symbol || String(x.sym || '').split('-')[0] || ''));
            const _gTimes = new Set(items.map(x => x.entry_time || ''));
            const gSym = _gSyms.size === 1 ? [..._gSyms][0] : '';
            const gTime = _gTimes.size === 1 ? [..._gTimes][0] : '';
            const gLabelTxt = (String(stratName) + (gSym ? ' ' + gSym : '') + (gTime ? ' ' + gTime : '')).replace(/['\\]/g, '');

            const grpId = 'grp_' + stratKey.replace(/[^a-z0-9]/gi, '_');
            // stash this group's positions so the header "✕ Close all" can close them
            // all in one click (closeAllInGroup dedupes by group_id — closePositionGroup
            // once per hedge/straddle group_id, closePosition for solo legs).
            window._grpItems = window._grpItems || {};
            window._grpItems[grpId] = items.map(t => ({
              sym: t.sym, entry: t.entry, qty: t.qty, mode: t.mode || 'paper',
              source: t.source || '', strategy: t.strategy || '', group_id: t.group_id || '', id: t.id
            }));
            let tableHtml = '<table id="' + grpId + '" style="width:100%;border-collapse:collapse;font-size:11.5px"><thead><tr style="color:#8b949e;text-align:left;border-bottom:1px solid #30363d">';

            activeOpenCols.forEach(c => {
              const isRight = ['entry_px', 'ltp', 'points', 'pnl', 'ret_pct', 'margin', 'run_up', 'run_down'].includes(c.id);
              const isCenter = ['entry_time', 'qty', 'chart', 'actions'].includes(c.id);
              const alignStyle = isRight ? ';text-align:right' : (isCenter ? ';text-align:center' : '');

              let sortIndicator = '';
              let sortCursor = 'cursor:pointer;';
              if (c.id !== 'actions' && c.id !== 'chart') {
                if (window._openSortCol === c.id) {
                  sortIndicator = window._openSortDir === 'desc' ? ' ▼' : ' ▲';
                }
                // else: no indicator — same 2026-07-02 cleanup as the completed-trades header.
              } else {
                sortCursor = '';
              }

              const onclickAttr = (c.id !== 'actions' && c.id !== 'chart') ? ` onclick="toggleSort('open', '${c.id}')"` : '';
              tableHtml += `<th style="padding:6px;font-weight:500;user-select:none;${sortCursor}${alignStyle}"${onclickAttr}>${c.l}${sortIndicator}</th>`;
            });

            tableHtml += '</tr></thead><tbody>';
            let grpMargin = 0, grpQty = 0;

            items.forEach(t => {
              const entry = Number(t.entry_price || 0), qty = t.qty || 0;
              const closeSide = t.entry === 'BUY' ? 'SELL' : 'BUY', cbc = t.entry === 'BUY' ? '#f85149' : '#3fb950';
              const bId = 'xo_' + (t.sym + '_' + (t.source || '') + '_' + (t.strategy || '')).replace(/[^a-z0-9]/gi, '_');
              const argEsc = s => String(s || '').replace(/'/g, '');

              // --- Added by Antigravity AI: Stepped Trailing Stop-Loss & Candle Close Checkbox ---
              let sl_pct = '', tp_pct = '', note = '', sl_type = '', sl_val = '', tp_type = '', tp_val = '', sl_trail_step = '', tp_trail_step = '', sl_candle_close = false, tp_candle_close = false;
              let max_ltp = null, min_ltp = null, conf_max_ltp = null, conf_min_ltp = null;
              if (t.tags) {
                t.tags.forEach(tg => {
                  if (tg.startsWith('SL_PCT:')) sl_pct = tg.split(':')[1];
                  if (tg.startsWith('TP_PCT:')) tp_pct = tg.split(':')[1];
                  if (tg.startsWith('SL_TYPE:')) sl_type = tg.split(':')[1];
                  if (tg.startsWith('SL_VAL:')) sl_val = tg.split(':')[1];
                  if (tg.startsWith('TP_TYPE:')) tp_type = tg.split(':')[1];
                  if (tg.startsWith('TP_VAL:')) tp_val = tg.split(':')[1];
                  if (tg.startsWith('SL_TRAIL_STEP:')) sl_trail_step = tg.split(':')[1];
                  if (tg.startsWith('TP_TRAIL_STEP:')) tp_trail_step = tg.split(':')[1];
                  if (tg === 'SL_CANDLE_CLOSE:true') sl_candle_close = true;
                  if (tg === 'TP_CANDLE_CLOSE:true') tp_candle_close = true;
                  if (tg.startsWith('MAX_LTP:')) max_ltp = parseFloat(tg.split(':')[1]);
                  if (tg.startsWith('MIN_LTP:')) min_ltp = parseFloat(tg.split(':')[1]);
                  if (tg.startsWith('CONF_MAX_LTP:')) conf_max_ltp = parseFloat(tg.split(':')[1]);
                  if (tg.startsWith('CONF_MIN_LTP:')) conf_min_ltp = parseFloat(tg.split(':')[1]);
                  if (tg.startsWith('NOTE:')) note = tg.substring(5);
                });
              }
              const _unitLbl = { pct: '%', pt: 'pt', trailing_pt: 'tsl', rs: '₹', premium: 'prem', index: 'idx' };
              let sltp_disp = '';
              if (sl_type || tp_type || sl_pct || tp_pct) {
                sltp_disp = `<div style="font-size:9px;margin-top:2px;white-space:nowrap">`;
                if (sl_type && sl_val) {
                  let cc_ind = sl_candle_close ? ' 🕯️' : '';
                  if (sl_type === 'trailing_pt') {
                    let step_suffix = sl_trail_step ? `:${sl_trail_step}` : '';
                    let tsl_disp = `SL ${sl_val}${step_suffix} tsl${cc_ind}`;

                    let gap = parseFloat(sl_val);
                    let step = parseFloat(sl_trail_step || 1.0);
                    if (isFinite(gap) && entry) {
                      let orig_sl = t.entry === 'BUY' ? (entry - gap) : (entry + gap);
                      let current_sl = orig_sl;

                      if (t.entry === 'BUY') {
                        // Use confirmed peak (with fallback to raw peak/entry) to match backend spike guard
                        let ref_high = conf_max_ltp !== null ? conf_max_ltp : (max_ltp !== null ? max_ltp : entry);
                        let movement = ref_high - entry;
                        if (movement > 0) {
                          let num_steps = Math.floor(movement / step);
                          current_sl = orig_sl + (num_steps * step);
                        }
                      } else {
                        // Use confirmed trough (with fallback to raw trough/entry) to match backend spike guard
                        let ref_low = conf_min_ltp !== null ? conf_min_ltp : (min_ltp !== null ? min_ltp : entry);
                        let movement = entry - ref_low;
                        if (movement > 0) {
                          let num_steps = Math.floor(movement / step);
                          current_sl = orig_sl - (num_steps * step);
                        }
                      }
                      tsl_disp += `<br/><span style="color:#8b949e;font-size:9.5px">${orig_sl.toFixed(1)} → ${current_sl.toFixed(1)}</span>`;
                    }
                    sltp_disp += `<span style="color:#f85149">${tsl_disp}</span> `;
                  } else {
                    sltp_disp += `<span style="color:#f85149">SL ${sl_val}${_unitLbl[sl_type] || ''}${cc_ind}</span> `;
                  }
                }
                else if (sl_pct) sltp_disp += `<span style="color:#f85149">SL ${sl_pct}%</span> `;
                if (tp_type && tp_val) {
                  let cc_ind = tp_candle_close ? ' 🕯️' : '';
                  sltp_disp += `<span style="color:#3fb950">TP ${tp_val}${_unitLbl[tp_type] || ''}${cc_ind}</span> `;
                }
                else if (tp_pct) sltp_disp += `<span style="color:#3fb950">TP ${tp_pct}%</span>`;
                sltp_disp += `</div>`;
              }

              // Resolve SL/Target to a PREMIUM price level for the chart (item F).
              // pct/pt/premium map directly; ₹-amount (rs) → premium points (₹ ÷ qty)
              // so it's plottable; index/underlying-level SL/TP can't be drawn on the
              // premium axis so they're left blank (no line). BUY: SL below / TP above
              // entry; SELL (option-selling): SL above / TP below.
              const _resolveSlTp = (type, valStr, isSL) => {
                const v = parseFloat(valStr);
                if (!isFinite(v) || !entry) return '';
                if (type === 'premium') return v;
                let dlt;
                if (type === 'pt' || type === 'trailing_pt') dlt = v;
                else if (type === 'pct') dlt = entry * v / 100;
                else if (type === 'rs') dlt = v / (qty || 1);
                else return '';
                const below = (t.entry === 'BUY') ? isSL : !isSL;
                return Number((below ? entry - dlt : entry + dlt).toFixed(2));
              };
              let _slPx = '', _tpPx = '';
              if (sl_type && sl_val) _slPx = _resolveSlTp(sl_type, sl_val, true);
              else if (sl_pct) _slPx = _resolveSlTp('pct', sl_pct, true);
              if (tp_type && tp_val) _tpPx = _resolveSlTp(tp_type, tp_val, false);
              else if (tp_pct) _tpPx = _resolveSlTp('pct', tp_pct, false);
              const _slArg = (_slPx === '' || _slPx == null) ? 'null' : _slPx;
              const _tpArg = (_tpPx === '' || _tpPx == null) ? 'null' : _tpPx;

              let _slValPass = sl_val;
              if (sl_type === 'trailing_pt' && sl_trail_step) {
                _slValPass = sl_val + ':' + sl_trail_step;
              }
              let _tpValPass = tp_val;
              if (tp_type === 'trailing_pt' && tp_trail_step) {
                _tpValPass = tp_val + ':' + tp_trail_step;
              }
              // --- End Antigravity AI addition ---

              // Task 8 — live trailing/aggressive SL, shown as the ₹ MAX LOSS if the
              // stop hits right now (or locked-in profit once the SL has trailed past
              // entry). Server-computed in /api/orders (sl_rs = signed ₹). Target keeps
              // its premium price alongside.
              let live_sl_disp = '';
              if (t.sl_now != null && isFinite(t.sl_now)) {
                const _aggr = !!t.sl_aggressive;
                const _mode = _aggr ? 'Aggr' : 'Trail';
                const _slPxTxt = Number(t.sl_now).toFixed(2);
                let _slPart;
                if (t.sl_rs != null && isFinite(t.sl_rs)) {
                  if (t.sl_rs < 0)
                    _slPart = `<span style="color:#f85149" title="${_mode} SL @ ${_slPxTxt} — max loss if stop hits now">Max loss ₹${Math.abs(Math.round(t.sl_rs)).toLocaleString('en-IN')} ▲</span>`;
                  else
                    _slPart = `<span style="color:#3fb950" title="${_mode} SL @ ${_slPxTxt} — profit now locked in (SL past entry)">Locked +₹${Math.round(t.sl_rs).toLocaleString('en-IN')} ▲</span>`;
                } else {
                  _slPart = `<span style="color:${_aggr ? '#e3a008' : '#58a6ff'}">${_mode} SL ${_slPxTxt} ▲</span>`;
                }
                live_sl_disp = `<div style="font-size:9px;margin-top:2px;white-space:nowrap">`
                  + _slPart
                  + (t.tp_now != null && isFinite(t.tp_now) ? ` <span style="color:#3fb950">Tgt ${Number(t.tp_now).toFixed(2)}</span>` : '')
                  + `</div>`;
              }

              let imgs = _imgTagsOf(t);

              const isNoteHidden = window._hiddenNotes.has(t.id) || localStorage.getItem('global_notes_show') !== 'true';
              let dispNote = '';
              if (note || (imgs && imgs.length)) {
                dispNote = `<div id="note-wrapper-${t.id}" style="${isNoteHidden ? 'display:none;' : ''}">`
                  + (note ? `<div style="color:#d29922;font-size:10px;margin-top:4px;white-space:normal;line-height:1.3;max-width:300px" title="${note.replace(/"/g, '&quot;')}">${note.replace(/</g, '&lt;').replace(/\n/g, '<br>')}</div>` : '')
                  + _noteThumbs(t.id, imgs)
                  + `</div>`;
              }

              const margin = t.margin_used != null ? Number(t.margin_used) : null;
              grpMargin += margin || 0;
              grpQty += qty || 0;

              let runup = '—', rundown = '—', _runupLive = false, _runKey = '';
              if (t.tags && entry > 0) {
                let max_ltp = null, min_ltp = null;
                t.tags.forEach(tg => {
                  if (tg.startsWith('MAX_LTP:')) max_ltp = parseFloat(tg.split(':')[1]);
                  if (tg.startsWith('MIN_LTP:')) min_ltp = parseFloat(tg.split(':')[1]);
                });
                if (max_ltp != null && min_ltp != null) {
                  // Carried-over positional legs (yesterday's overnight positions,
                  // trader_dashboard.py:4084) are NOT seen by today-scoped
                  // pos_monitor_loop, so their MAX_LTP/MIN_LTP tags freeze at
                  // yesterday's close. For these, track the day's live excursion
                  // client-side (display-only) — seeded from the frozen tag as a
                  // floor so we never lose yesterday's run, then extended by the
                  // live LTP feed in _patchLtpCells.
                  if (t.carried_over) {
                    _runupLive = true;
                    // Keyed by sym+strategy, not sym alone — the same contract can be
                    // open in two strategies with different entry/side/qty, and a
                    // sym-only key would blend their excursions together.
                    _runKey = t.sym + '|' + (t.strategy || t.source || '');
                    const prev = _carryRunExt[_runKey] || {};
                    _carryRunExt[_runKey] = {
                      maxL: Math.max(max_ltp, prev.maxL != null ? prev.maxL : -Infinity),
                      minL: Math.min(min_ltp, prev.minL != null ? prev.minL : Infinity),
                      entry: entry, side: t.entry, qty: qty
                    };
                    max_ltp = _carryRunExt[_runKey].maxL;
                    min_ltp = _carryRunExt[_runKey].minL;
                  }
                  // AMT (₹) | PT (points) | % — same formatter as Completed Trades
                  const upPt   = t.entry === 'BUY' ? (max_ltp - entry) : (entry - min_ltp);
                  const downPt = t.entry === 'BUY' ? (min_ltp - entry) : (entry - max_ltp);
                  runup   = _ruCell(upPt,   upPt   * qty, upPt   / entry * 100);
                  rundown = _ruCell(downPt, downPt * qty, downPt / entry * 100);
                }
              }

              tableHtml += '<tr style="border-bottom:1px solid #21262d">';

              activeOpenCols.forEach(c => {
                let val = '';
                let colorStyle = '';

                switch (c.id) {
                  case 'date':
                    val = t.entry_date || ordDate;
                    colorStyle = 'color:#6e7681;';
                    val = `<span style="white-space:nowrap;">${val}</span>`;
                    break;
                  case 'symbol':
                    const isNoteColOn = activeOpenCols.some(x => x.id === 'note');
                    val = `<b>${t.sym}</b>` + (isNoteColOn ? '' : dispNote);
                    colorStyle = 'color:#adbac7;';
                    break;
                  case 'instrument':
                    val = _instrCell(t);
                    break;
                  case 'dte':
                    val = (typeof _dteBadge === 'function') ? _dteBadge(t) : (t.dte != null ? t.dte + 'd' : '—');
                    break;
                  case 'strategy':
                    val = _stratCell(t);
                    break;
                  case 'tags':
                    val = _ordTags(t, true);
                    break;
                  case 'side':
                    val = t.entry;
                    colorStyle = 'color:' + (t.entry === 'BUY' ? '#3fb950' : '#f85149') + ';font-weight:600;';
                    break;
                  case 'entry_px':
                    val = entry.toFixed(2);
                    colorStyle = 'color:#8b949e;';
                    break;
                  case 'ltp':
                    val = `<span class="ltp-cell" style="color:#e6edf3" data-sym="${t.sym}" data-entry="${entry}" data-side="${t.entry}" data-qty="${qty}">⏳</span>`;
                    break;
                  case 'entry_time':
                    val = (t.entry_time || '') + sltp_disp + live_sl_disp;
                    colorStyle = 'color:#8b949e;';
                    break;
                  case 'points':
                    val = `<span class="pts-cell" data-sym="${t.sym}" style="font-weight:700;">—</span>`;
                    break;
                  case 'pnl':
                    val = `<span class="unrl-cell" data-sym="${t.sym}" style="font-weight:700;">—</span>`;
                    break;
                  case 'ret_pct':
                    val = `<span class="ret-cell" data-sym="${t.sym}" style="font-weight:700;">—</span>`;
                    break;
                  case 'margin':
                    val = margin != null ? Math.round(margin).toLocaleString('en-IN') : '—';
                    colorStyle = 'color:#8b949e;';
                    break;
                  case 'run_up':
                    val = _runupLive ? `<span class="runup-cell" data-sym="${t.sym}" data-rk="${_runKey}">${runup}</span>` : runup;
                    break;
                  case 'run_down':
                    val = _runupLive ? `<span class="rundown-cell" data-sym="${t.sym}" data-rk="${_runKey}">${rundown}</span>` : rundown;
                    break;
                  case 'qty':
                    val = qty;
                    break;
                  case 'lots':
                    val = (t.lot_size && qty) ? Math.round(qty / t.lot_size) : '—';
                    colorStyle = 'color:#adbac7;';
                    break;
                  case 'chart':
                    val = `<button onclick="openTradeChart('${argEsc(t.sym)}','${t.entry}',${entry},0,'${t.entry_time}','',${qty},'${t.entry_date || ordDate}',null,null,null,${_slArg},${_tpArg},'${argEsc(t.strategy)}')" title="Premium chart" style="padding:3px 9px;font-size:13px;background:#21262d;border:1px solid #30363d;border-radius:5px;color:#58a6ff;cursor:pointer">📈</button>`;
                    break;
                  case 'actions': {
                    // MIS/NRML carry as an INLINE PILL (row me dikhe, ⋮ menu me chhupa nahi).
                    // Click = toggle (group-wide). Green NRML = carry past 3:15, gray MIS = square.
                    const _cOn = !!(window._carryHas && window._carryHas(t.group_id, t.id));
                    const _cPill = `<span onclick="event.stopPropagation();togglePosCarry('${argEsc(t.group_id)}',${t.id})" title="MIS = 3:15 auto square-off · NRML = carry overnight (poora group). Click to toggle."
                      style="cursor:pointer;padding:3px 8px;border-radius:20px;font-size:9.5px;font-weight:700;white-space:nowrap;border:1px solid ${_cOn ? '#3fb95080' : '#30363d'};background:${_cOn ? '#3fb95020' : '#161b22'};color:${_cOn ? '#3fb950' : '#8b949e'}">${_cOn ? '🌙 NRML' : '☀️ MIS'}</span>`;
                    // per-row "🔗 Group ✕" HATA diya — ab poora group header ke "✕ Close all (N)"
                    // se band hota hai (user: button header pe chala jaye, jagah bache). Row me
                    // bas carry-pill + single close ✕.
                    val = `
                <div style="display:flex; align-items:center; justify-content:center; gap:8px;">
                  ${_cPill}
                  <button id="${bId}" onclick="closePosition('${argEsc(t.sym)}','${t.entry}',${qty},'${t.mode || 'paper'}','${argEsc(t.source)}','${argEsc(t.strategy)}','${bId}')"
                    style="padding:4px 10px;background:${cbc}20;border:1px solid ${cbc}80;border-radius:5px;color:${cbc};font-size:11px;font-weight:700;cursor:pointer">
                    ${closeSide} ✕
                  </button>
                  <div class="dropdown">
                    <span class="dropdown-trigger" onclick="toggleDropdown(event, ${t.id})">⋮</span>
                    <div id="dropdown-${t.id}" class="dropdown-content">
                      <a href="javascript:void(0)" onclick="openTradeChart('${argEsc(t.sym)}','${t.entry}',${entry},0,'${t.entry_time}','',${qty},'${t.entry_date || ordDate}',null,null,null,${_slArg},${_tpArg},'${argEsc(t.strategy)}')">📈 Chart</a>
                      <a href="javascript:void(0)" onclick="openSlTpModal(${t.id}, '${sl_val || sl_pct}', '${tp_val || tp_pct}', '${sl_type || 'pct'}', '${tp_type || 'pct'}')">⚙️ SL/Target</a>
                      <a href="javascript:void(0)" onclick="openNoteModal(${t.id})">📝 Edit Note</a>
                      <a href="javascript:void(0)" onclick="toggleNoteDesc(${t.id})">👁️ Toggle Note</a>
                      <a href="javascript:void(0)" onclick="bookClose('${argEsc(t.sym)}','${t.entry}',${qty},${entry},'${t.mode || 'paper'}','${argEsc(t.source)}','${argEsc(t.strategy)}')" style="color:#f85149">🗑 Remove from Book</a>
                    </div>
                  </div>
                </div>`;
                    break;
                  }
                }

                const isRight = ['entry_px', 'ltp', 'points', 'pnl', 'ret_pct', 'margin', 'run_up', 'run_down'].includes(c.id);
                const isCenter = ['entry_time', 'qty', 'chart', 'actions'].includes(c.id);
                const alignStyle = isRight ? 'text-align:right;' : (isCenter ? 'text-align:center;' : '');

                tableHtml += `<td style="padding:7px 6px;vertical-align:top;${alignStyle}${colorStyle}">${val}</td>`;
              });

              tableHtml += '</tr>';
            });

            // ── per-group TOTAL row (tfoot, column-aligned like completed trades) ──
            const isLastGrp = Object.keys(grouped).indexOf(stratKey) === Object.keys(grouped).length - 1;
            tableHtml += '</tbody><tfoot><tr style="border-top:2px solid #30363d;font-weight:700">';
            activeOpenCols.forEach((c, idx) => {
              const isRight = ['entry_px', 'ltp', 'points', 'pnl', 'ret_pct', 'margin', 'run_up', 'run_down'].includes(c.id);
              const isCenter = ['entry_time', 'qty', 'chart', 'actions'].includes(c.id);
              const alignStyle = isRight ? 'text-align:right;' : (isCenter ? 'text-align:center;' : '');
              let val = '', colorStyle = 'color:#8b949e;';
              if (idx === 0) { val = 'TOTAL'; }
              else {
                switch (c.id) {
                  case 'qty': val = grpQty; break;
                  case 'margin': {
                    // Show what the broker REALLY blocks for this group, not the
                    // sum of each leg's standalone margin — for a hedged structure
                    // those differ hugely (live: condor sum ₹3,77,337 vs ₹82,334
                    // actually blocked). Backend sends the basket figure from the
                    // same risk_gate._group_capital() RMS itself uses.
                    // key by stratKey (= t.strategy, exactly what the backend
                    // grouped by), NOT stratName — that's the ' | '-split label.
                    const gm = (window._ordGroupMargin || {})[stratKey];
                    if (gm && gm.hedged != null && gm.standalone > gm.hedged + 1) {
                      const saved = Math.round(gm.standalone - gm.hedged);
                      val = `<span title="Real hedged margin Zerodha blocks for this structure — ₹${saved.toLocaleString('en-IN')} less than the sum of the legs' standalone margins (hedge benefit)">`
                        + `${Math.round(gm.hedged).toLocaleString('en-IN')}`
                        + `<br><span style="font-size:9px;color:#6e7681;font-weight:400"><s>${Math.round(gm.standalone).toLocaleString('en-IN')}</s> hedged</span></span>`;
                    } else {
                      val = Math.round(gm && gm.hedged != null ? gm.hedged : grpMargin).toLocaleString('en-IN');
                    }
                    break;
                  }
                  // live cells — updated by _patchLtpCells; put grand-total IDs on last (or only) group
                  case 'points': val = `<b class="grp-tot-pts" data-grp="${grpId}">—</b>`; break;
                  case 'pnl': val = `<b class="grp-tot-unrl" data-grp="${grpId}">—</b>`; break;
                  case 'ret_pct': val = `<b class="grp-tot-ret" data-grp="${grpId}">—</b>`; break;
                }
              }
              tableHtml += `<td style="padding:7px 6px;${alignStyle}${colorStyle}">${val}</td>`;
            });
            tableHtml += '</tr></tfoot></table>';

            // group margin (real hedged basket if available, else summed) — shown
            // in the COLLAPSED summary so you see capital-in-use without expanding.
            const _gmObj = (window._ordGroupMargin || {})[stratKey];
            const grpMarginDisp = Math.round((_gmObj && _gmObj.hedged != null ? _gmObj.hedged : grpMargin) || 0).toLocaleString('en-IN');

            oh += `
      <details ${_grpOpenAttr(grpId)} ontoggle="_grpToggleSave('${grpId}', this.open)" style="margin-bottom: 12px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px;">
        <style>details > summary { list-style: none; } details > summary::-webkit-details-marker { display: none; }</style>
        <summary style="padding: 10px 14px; cursor: pointer; font-weight: 600; color: #58a6ff; display: flex; justify-content: space-between; align-items: flex-start; border-radius: 6px;" onmouseover="this.style.background='#161b22'" onmouseout="this.style.background='transparent'">
          <div style="display:flex; flex-direction:column; gap:4px; flex-grow:1;">
            <span>📂 ${MISSION_NUM[String(stratName).toLowerCase()] ? regLabel(stratName) : stratName.toUpperCase()}${gSym ? ` <span style="color:#adbac7;font-weight:normal">· ${gSym}</span>` : ''}${gTime ? ` <span style="color:#8b949e;font-size:11px;font-weight:normal">· ${gTime}</span>` : ''} <span style="color:#8b949e;font-size:12px;font-weight:normal;margin-left:8px;">(${items.length} ${items.length === 1 ? 'position' : 'positions'})</span></span>
            ${stratDesc ? `<span style="color:#d29922;font-size:11px;font-weight:normal;margin-left:22px;white-space:pre-wrap;line-height:1.4;margin-top:4px;max-width:90%;">📝 ${stratDesc}</span>` : ''}
          </div>
          <div style="display:flex; align-items:baseline; gap:10px; margin-right:14px; white-space:nowrap;" title="Group unrealized total — ₹ / points / return %">
            <span style="font-size:11px;font-weight:400;color:#6e7681" title="Margin in use (real hedged basket)">💰 ₹${grpMarginDisp}</span>
            <span class="grp-tot-unrl" data-grp="${grpId}" style="font-size:13px;font-weight:700;color:#8b949e">—</span>
            <span class="grp-tot-pts" data-grp="${grpId}" style="font-size:11px;font-weight:400;color:#8b949e">—</span>
            <span class="grp-tot-ret" data-grp="${grpId}" style="font-size:11px;font-weight:400;color:#8b949e">—</span>
          </div>
          ${items.length >= 2 ? `<button onclick="event.stopPropagation();event.preventDefault();closeAllInGroup('${grpId}','${gLabelTxt}',${items.length})" title="Close all ${items.length} positions in this group (confirm ke saath)" style="margin-right:10px;padding:3px 9px;font-size:11px;font-weight:700;background:#f8514920;border:1px solid #f8514980;border-radius:4px;color:#f85149;cursor:pointer;white-space:nowrap">✕ Close all (${items.length})</button>` : ''}
          ${_payoffBtn(items, stratName)}
          <button onclick="editStrategyLabel(this, event)" data-strat="${String(stratName).replace(/"/g, '&quot;')}" style="margin-right: 12px; padding: 3px 8px; font-size: 11px; background: #21262d; border: 1px solid #30363d; border-radius: 4px; color: #8b949e; cursor: pointer;">✏️ Edit</button>
          <span style="font-size:12px;color:#8b949e; margin-top: 2px;">Toggle ▾</span>
        </summary>
        <div style="border-top: 1px solid #30363d; overflow-x: auto;">
          ${tableHtml}
        </div>
      </details>`;
          }

          ordOpenEl.innerHTML = blockedHtml + (oh || '<div style="color:#6e7681;font-size:12px;padding:6px">Koi open position nahi</div>');
          ordOpenEl.dataset.fp = openFp;
          _patchLtpCells(); _fetchPositionLtp();
        } catch (e) {
          if (ordOpenEl) ordOpenEl.innerHTML = '<div style="color:#f85149;font-size:11px;padding:8px;background:#21262d;border-radius:4px">JS ERROR: ' + e.message + '<br><pre style="font-size:10px;white-space:pre-wrap">' + e.stack + '</pre></div>';
        }
      }
    }

    function switchCfgTab(type) {
      document.querySelectorAll('#cfg-tabs .tab').forEach(e => e.classList.remove('active'));
      const targetTab = document.querySelector(`#cfg-tabs .tab[onclick="switchCfgTab('${type}')"]`);
      if (targetTab) targetTab.classList.add('active');
      activeCfgTab = type;
      if (type === 'webhook') {
        document.getElementById('cfg-content').style.display = 'none';
        document.getElementById('webhook-content-wrapper').style.display = 'block';
        if (typeof whEnter === 'function') whEnter();
      } else {
        document.getElementById('cfg-content').style.display = 'block';
        const whWrapper = document.getElementById('webhook-content-wrapper');
        if (whWrapper) whWrapper.style.display = 'none';
        if (typeof whLeave === 'function') whLeave();
        renderConfigTable();
      }
    }

    // ── DATA FETCHING ──
    // Accepts either (key, value) for a single setting, OR a single object
    // {key1: val1, key2: val2, ...} to update several keys in ONE atomic
    // GET-modify-POST round trip. Found 2026-07-03: saveColPrefs() used to
    // fire 2 separate un-awaited calls of this function back-to-back (one
    // per column-set key) — each does its own independent GET of /api/config,
    // so whichever call's POST lands LAST silently overwrites the other's
    // just-saved change with its own (now-stale) snapshot. That's why toggling
    // "Exit Reason" on, saving, and refreshing sometimes reverted it — a
    // genuine race, not a rendering bug, and non-deterministic (whichever
    // round trip happened to finish first/last that time).
    async function saveUiConfigToBackend(keyOrUpdates, value) {
      try {
        const r = await fetch('/api/config');
        const cfg = await r.json();
        if (!cfg._ui_config) cfg._ui_config = {};
        const updates = (typeof keyOrUpdates === 'object' && keyOrUpdates !== null)
          ? keyOrUpdates : { [keyOrUpdates]: value };
        for (const k in updates) {
          if (updates[k] === null) delete cfg._ui_config[k];
          else cfg._ui_config[k] = updates[k];
        }
        await fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cfg)
        });
        GLOBAL_CONFIG._ui_config = cfg._ui_config;
      } catch (e) {
        console.error("Failed to save UI config to backend:", e);
      }
    }

    async function fetchConfig() {
      const r = await fetch('/api/config');
      GLOBAL_CONFIG = await r.json();
      if (GLOBAL_CONFIG && GLOBAL_CONFIG._ui_config) {
        for (const k in GLOBAL_CONFIG._ui_config) {
          if (GLOBAL_CONFIG._ui_config[k] === null) {
            localStorage.removeItem(k);
          } else {
            localStorage.setItem(k, GLOBAL_CONFIG._ui_config[k]);
          }
        }
      }
    }

    async function loadAll() {
      await fetchConfig();
      _loadOrdColPrefs();
      _loadCalPointsColPrefs();
      _loadStatsColPrefs();
      initGlobalNotesToggle();
      initCalGroupSymbolToggle();
      await checkStatus();
      renderControlTab();
      renderPnlTab();
      renderLogTab();
      renderConfigTable();
      loadTokenStatus();
      if (activeTab === 'log') updateLogs();
    }

    async function checkStatus() {
      const r = await fetch('/api/status');
      const d = await r.json();
      RUNNING_PIDS = d;
      document.getElementById('hdr-dot').className = Object.keys(d).length > 0 ? "dot on" : "dot";
    }

    // ── DYNAMIC RENDERING ──
