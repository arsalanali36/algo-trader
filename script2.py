import re
import sys

with open('templates/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

replacement_logic = """
      let tradesList = [...(window.currentCalendarTrades || [])];
      
      const searchInput = document.getElementById('cal-symbol-search');
      if (searchInput && searchInput.value) {
        const query = searchInput.value.toUpperCase();
        tradesList = tradesList.filter(t => (t.sym || t.symbol || '').toUpperCase().includes(query));
      }
      
      if (window.calSelectedDateFilter) {
        tradesList = tradesList.filter(t => (t.exit_date || t.entry_date) === window.calSelectedDateFilter);
      }
      if (window.calExitReasonFilter) {
        tradesList = tradesList.filter(t => (t.exit_reason || '') === window.calExitReasonFilter);
      }

      if (typeof _sortData === 'function' && window._calPointsSortCol) {
        _sortData(tradesList, window._calPointsSortCol, window._calPointsSortDir);
      } else {
        tradesList.sort((a, b) => {
          const da = a.entry_date + ' ' + (a.entry_time || '00:00');
          const db = b.entry_date + ' ' + (b.entry_time || '00:00');
          return db.localeCompare(da);
        });
      }
      const sortedTrades = tradesList;

      const _chartList = sortedTrades.map(t => ({
        sym: t.sym || t.symbol,
        side: t.entry,
        entry: t.entry_price,
        exit: t.exit_price,
        et: t.entry_time,
        xt: t.exit_time,
        qty: t.qty,
        date: t.exit_date || t.entry_date,
        strategy: t.strategy || ''
      }));
      localStorage.setItem('chartTradeList', JSON.stringify(_chartList));
      
      // Render table headers with sorting
      const theadRow = document.getElementById('cal-points-cols-thead');
      if (theadRow) {
        theadRow.innerHTML = cols.map(c => {
          let align = 'left';
          if (c.a === 'center') align = 'center';
          if (c.a === 'right') align = 'right';
          
          let sortIndicator = '';
          if (window._calPointsSortCol === c.id) {
            sortIndicator = window._calPointsSortDir === 'desc' ? ' ▼' : ' ▲';
          }
          const onclickAttr = c.id !== 'actions' ? ` onclick="toggleCalPointsSort('${c.id}')"` : '';
          return `<th style="padding:6px; font-weight:500; text-align:${align}; cursor:pointer; user-select:none;"${onclickAttr}>${c.l}${sortIndicator}</th>`;
        }).join('');
      }

      if (sortedTrades.length === 0) {
        pointsBody.innerHTML = `<tr><td colspan="${cols.length}" style="text-align:center;color:#6e7681;padding:12px;">Is filter pe koi trade details nahi hain</td></tr>`;
        const wrap = document.getElementById('cal-points-pagination-wrap');
        if (wrap) wrap.innerHTML = '';
        return;
      }

      // Group trades by date and symbol if enabled
      const isGroupEnabled = localStorage.getItem('cal_group_symbol') === 'true';
      let displayItems = [];

      if (isGroupEnabled) {
        const dateGroups = {}; // date -> { symbol -> [trades] }
        sortedTrades.forEach(t => {
          const dStr = t.exit_date || t.entry_date || '—';
          const sStr = t.sym || t.symbol || '—';
          if (!dateGroups[dStr]) dateGroups[dStr] = {};
          if (!dateGroups[dStr][sStr]) dateGroups[dStr][sStr] = [];
          dateGroups[dStr][sStr].push(t);
        });

        const seenGroups = new Set();
        sortedTrades.forEach(t => {
          const dStr = t.exit_date || t.entry_date || '—';
          const sStr = t.sym || t.symbol || '—';
          const grpKey = dStr + '||' + sStr;
          if (seenGroups.has(grpKey)) return;
          seenGroups.add(grpKey);

          const groupTrades = dateGroups[dStr][sStr];
          if (groupTrades.length > 1) {
            displayItems.push({ type: 'group', date: dStr, symbol: sStr, trades: groupTrades });
          } else {
            displayItems.push({ type: 'single', date: dStr, trade: groupTrades[0] });
          }
        });
      } else {
        sortedTrades.forEach(t => {
          displayItems.push({ type: 'single', date: t.exit_date || t.entry_date || '—', trade: t });
        });
      }

      let pointsHtml = '';
      let currentGroupDate = '';
      
      let _tot = { g: 0, tx: 0, n: 0, inv: 0, pts: 0 };

      // Make the tbody scrollable
      displayItems.forEach((item, itemIdx) => {
        const tradeDate = item.date;
        if (tradeDate !== currentGroupDate) {
          currentGroupDate = tradeDate;
          let dateText = tradeDate;
          try {
            const parts = tradeDate.split('-');
            if (parts.length === 3) {
              const dObj = new Date(parts[0], parts[1] - 1, parts[2]);
              dateText = dObj.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
            }
          } catch (e) { }

          pointsHtml += `
        <tr style="background:#1c2128; font-weight:600; border-bottom:1px solid #30363d; border-top:1px solid #30363d;">
          <td colspan="${cols.length}" style="padding:8px; color:#58a6ff; font-size:11.5px; font-weight:600;">📅 ${dateText}</td>
        </tr>
      `;
        }

        if (item.type === 'single') {
          const t = item.trade;
          const ep = t.entry_price || 0, xp = t.exit_price || 0, qt = t.qty || 0;
          _tot.g += t._gross || 0; _tot.tx += t._tax || 0; _tot.n += t._net || 0; _tot.inv += qt * ep;
          _tot.pts += t.entry === 'BUY' ? (xp - ep)*qt : (ep - xp)*qt;
          pointsHtml += renderSingleTradeRow(item.trade, cols, sortedTrades);
        } else {
          const trades = item.trades;
          trades.forEach(t => {
            const ep = t.entry_price || 0, xp = t.exit_price || 0, qt = t.qty || 0;
            _tot.g += t._gross || 0; _tot.tx += t._tax || 0; _tot.n += t._net || 0; _tot.inv += qt * ep;
            _tot.pts += t.entry === 'BUY' ? (xp - ep)*qt : (ep - xp)*qt;
          });
          pointsHtml += renderGroupedRow(item, cols, sortedTrades, itemIdx);
        }
      });
      
      const _totRetPct = _tot.inv > 0 ? ((_tot.n / _tot.inv) * 100).toFixed(2) + '%' : '—';
      pointsHtml += '<tr style="border-top:2px solid #30363d;font-weight:700;background:#161b22;">';
      
      cols.forEach((c, idx) => {
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

          let align = 'left';
          if (c.a === 'center') align = 'center';
          if (c.a === 'right') align = 'right';
          pointsHtml += `<td style="padding:7px 6px;text-align:${align};${colorStyle}">${val}</td>`;
      });
      pointsHtml += '</tr>';

      pointsBody.innerHTML = pointsHtml;
      
      // Remove pagination visually
      const wrap = document.getElementById('cal-points-pagination-wrap');
      if (wrap) wrap.innerHTML = '';
    }
"""

start_pattern = r"      let tradesList = \[\.\.\.\(window\.currentCalendarTrades \|\| \[\]\)\];"
end_pattern = r"      renderPointsPagination\(totalPages\);\s*\}"

match = re.search(start_pattern + r".*?" + end_pattern, html, flags=re.DOTALL)
if match:
    html = html[:match.start()] + replacement_logic.lstrip("\n") + html[match.end():]
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("Success")
else:
    print("Match failed")
