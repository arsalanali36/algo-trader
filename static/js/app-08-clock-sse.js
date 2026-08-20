// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    // Step 1: Login link banao (api_key config se aata hai)
    async function kiteSaveKey() {
      const key = document.getElementById('kite-api-key').value.trim();
      const sec = document.getElementById('kite-api-secret').value.trim();
      const msg = document.getElementById('kite-key-msg');
      if (!key || !sec) { msg.textContent = '⚠️ Key aur Secret dono chahiye'; msg.style.color = '#f85149'; return; }
      const r = await fetch('/api/kite-save-key', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: key, api_secret: sec }) }).then(r => r.json()).catch(() => ({}));
      if (r.ok) {
        msg.textContent = '✅ Saved (permanent)! Ab Step 1 se login karo';
        msg.style.color = '#3fb950';
        document.getElementById('kite-api-secret').value = '';
        kiteLoadKeyStatus();
      }
      else { msg.textContent = '❌ ' + (r.error || 'Error'); msg.style.color = '#f85149'; }
    }

    async function kiteOpenLogin() {
      // Tab ko click ke turant baad (synchronously) kholo — warna await fetch ke baad
      // window.open() ko browser popup-blocker silently block kar deta hai (no error shown).
      const win = window.open('about:blank', '_blank');
      const r = await fetch('/api/kite-login-url').then(r => r.json()).catch(() => ({}));
      if (r.url) {
        if (win) win.location = r.url;
      } else {
        if (win) win.close();
        document.getElementById('kite-token-msg').textContent =
          '⚠️ kite_api_key config.json mein nahi hai — pehle save karo';
        document.getElementById('kite-token-msg').style.color = '#f85149';
      }
    }

    // Step 2: request_token paste karo → exchange karke access_token save karo
    async function kiteExchangeToken() {
      // URL se paste kiya to extra params strip karo — sirf token chahiye
      let req_token = document.getElementById('kite-request-token').value.trim();
      req_token = req_token.split('&')[0].replace('request_token=', '').trim();
      const msg = document.getElementById('kite-token-msg');
      if (!req_token) { msg.textContent = 'request_token paste karo pehle'; return; }
      msg.textContent = 'Exchanging...'; msg.style.color = '#8b949e';
      const r = await fetch('/api/kite-exchange-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_token: req_token })
      }).then(r => r.json()).catch(e => ({ ok: false, error: e.toString() }));

      if (r.ok) {
        msg.textContent = '✅ Kite token saved! Orders ab Zerodha pe jayenge.';
        msg.style.color = '#3fb950';
        document.getElementById('kite-request-token').value = '';
      } else {
        msg.textContent = '❌ ' + (r.error || 'Exchange failed');
        msg.style.color = '#f85149';
      }
    }

    // ── CLOCK, DATE & DROPDOWNS ──
    function updateClockAndDate() {
      const d = new Date();
      document.getElementById('clock').innerText = d.toLocaleTimeString('en-IN');

      const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
      const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
      const dateStr = `${monthNames[d.getMonth()]} ${d.getDate()}, ${dayNames[d.getDay()]}`;
      const dateEl = document.getElementById('hdr-top-date');
      if (dateEl && dateEl.textContent !== dateStr) {
        dateEl.textContent = dateStr;
      }
    }
    updateClockAndDate();
    setInterval(updateClockAndDate, 1000);

    // Dropdown Menus toggles
    window.toggleAvatarDropdown = (event) => {
      event.stopPropagation();
      window.closeMoreDropdown();
      const dropdown = document.getElementById('avatar-dropdown');
      dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
    };
    window.closeAvatarDropdown = () => {
      const dropdown = document.getElementById('avatar-dropdown');
      if (dropdown) dropdown.style.display = 'none';
    };
    window.toggleMoreDropdown = (event) => {
      event.stopPropagation();
      window.closeAvatarDropdown();
      window.closeReportsDropdown();
      window.closeCurvesDropdown && window.closeCurvesDropdown();
      window.closeOrdersDropdown && window.closeOrdersDropdown();
      const dropdown = document.getElementById('more-dropdown');
      dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
    };
    window.closeMoreDropdown = () => {
      const dropdown = document.getElementById('more-dropdown');
      if (dropdown) dropdown.style.display = 'none';
    };
    // 📋 Reports dropdown (task 76 — EOD Reports + YT Presentations)
    window.toggleReportsDropdown = (event) => {
      event.stopPropagation();
      window.closeAvatarDropdown();
      window.closeMoreDropdown();
      window.closeCurvesDropdown && window.closeCurvesDropdown();
      window.closeOrdersDropdown && window.closeOrdersDropdown();
      const dropdown = document.getElementById('reports-dropdown');
      if (dropdown) dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
    };
    window.closeReportsDropdown = () => {
      const dropdown = document.getElementById('reports-dropdown');
      if (dropdown) dropdown.style.display = 'none';
    };
    // 📈 Curves dropdown (Curves + GEX + What-If)
    window.toggleCurvesDropdown = (event) => {
      event.stopPropagation();
      window.closeAvatarDropdown();
      window.closeMoreDropdown();
      window.closeReportsDropdown();
      window.closeOrdersDropdown && window.closeOrdersDropdown();
      const dropdown = document.getElementById('curves-dropdown');
      if (dropdown) dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
    };
    window.closeCurvesDropdown = () => {
      const dropdown = document.getElementById('curves-dropdown');
      if (dropdown) dropdown.style.display = 'none';
    };
    // 📒 Orders dropdown (Orders & P&L + Broker Orders)
    window.toggleOrdersDropdown = (event) => {
      event.stopPropagation();
      window.closeAvatarDropdown();
      window.closeMoreDropdown();
      window.closeReportsDropdown();
      window.closeCurvesDropdown && window.closeCurvesDropdown();
      const dropdown = document.getElementById('orders-dropdown');
      if (dropdown) dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
    };
    window.closeOrdersDropdown = () => {
      const dropdown = document.getElementById('orders-dropdown');
      if (dropdown) dropdown.style.display = 'none';
    };

    document.addEventListener('click', () => {
      window.closeAvatarDropdown();
      window.closeMoreDropdown();
      window.closeReportsDropdown();
      window.closeCurvesDropdown && window.closeCurvesDropdown();
      window.closeOrdersDropdown && window.closeOrdersDropdown();
    });

    setInterval(() => {
      checkStatus().then(() => {
        if (activeTab === 'control') renderControlTab();
        if (activeTab === 'log') renderLogRunControls();
      });
    }, 5000);

    // Market band hone pe kuch badalta nahi — 4s auto-refresh sirf "Loading…"
    // flash karta rehta hai bina reason. IST weekday 09:15–15:35 me hi auto-refresh
    // karo; uske baad tab-open / manual Refresh se hi load ho (user ki shikayat).
    function _mktOpenNow() {
      const n = new Date();
      const ist = new Date(n.getTime() + n.getTimezoneOffset() * 60000 + 5.5 * 3600000);
      const day = ist.getDay();                     // 0 Sun .. 6 Sat
      if (day === 0 || day === 6) return false;
      const mins = ist.getHours() * 60 + ist.getMinutes();
      return mins >= (9 * 60 + 15) && mins <= (15 * 60 + 35);
    }
    window._mktOpenNow = _mktOpenNow;
    setInterval(() => {
      if (activeTab === 'orders' && _mktOpenNow()) ordersRender();   // P&L merged here — DB-backed, no Dhan
      if (activeTab === 'log') updateLogs();
    }, 4000);

    // ── Real-time LTP via SSE (dhan_feed WebSocket) ──
    let _ltpLive = {};  // trad_sym -> ltp  (populated by _fetchPositionLtp)
    // sym -> {maxL,minL,entry,side,qty} — live Run-Up/Down tracker for carried-over
    // positional legs whose frozen MAX_LTP/MIN_LTP tags pos_monitor no longer updates.
    let _carryRunExt = {};
    let _sseSrc = null;
    function startLtpStream() {
      if (_sseSrc) return;
      _sseSrc = new EventSource('/api/ltp-stream');
      _sseSrc.onmessage = (e) => {
        // MERGE, don't replace. The SSE stream can omit carried-over positional legs
        // (e.g. VRP condor) that only _fetchPositionLtp (/api/positions-ltp) knows
        // about — a wholesale replace wiped their LTP every message, so the Today's
        // Peak "Running" column kept resetting to ⏳. Merge keeps the last good LTP
        // for any sym the stream doesn't carry this tick.
        let _m; try { _m = JSON.parse(e.data); } catch (_) { return; }
        if (_m && typeof _m === 'object') Object.assign(_ltpLive, _m);
        if (activeTab === 'orders') _patchLtpCells();
      };
      _sseSrc.onerror = () => { _sseSrc.close(); _sseSrc = null; setTimeout(startLtpStream, 5000); };
    }

    // Fetch position LTP separately — updates cells without rebuilding table
    window.feedPaused = false;
    function toggleFeedPause() {
      window.feedPaused = !window.feedPaused;
      const btn = document.getElementById('pause-feed-btn');
      // The toggle now lives inside the avatar dropdown, so it styles as a menu
      // item (text colour only). The avatar itself carries the paused state, so
      // a paused feed is still visible without opening the menu.
      const av = document.getElementById('avatar-btn');
      if (window.feedPaused) {
        if (btn) { btn.innerHTML = '⏸️ Feed Paused'; btn.style.color = '#f85149'; }
        if (av) { av.style.background = '#da3633'; av.title = 'Feed paused — LTP updates stopped'; }
      } else {
        if (btn) { btn.innerHTML = '🟢 Feed Active'; btn.style.color = '#3fb950'; }
        if (av) { av.style.background = '#1f6feb'; av.title = ''; }
        if (typeof _fetchPositionLtp === 'function') _fetchPositionLtp();
        if (typeof qoLoadChain === 'function') qoLoadChain();   // Quick Order chain (was qoFetchLtp pre-2026-07-28 redesign)
      }
    }

    async function _fetchPositionLtp() {
      if (window.feedPaused || document.hidden) return;   // background tab — skip positions LTP poll (load-trim)
      const cells = [...document.querySelectorAll('.ltp-cell')];
      if (!cells.length) return;
      // Join on sec_id (the unique contract), NOT trad_sym: two open positions can share
      // a month-only trad_sym on different expiries (weekly+monthly) → a sym key showed
      // one leg's LTP on the other → fake P&L (TRAP #166). Cells carry data-sec.
      const secs = [...new Set(cells.map(el => el.getAttribute('data-sec')).filter(Boolean))];
      const syms = [...new Set(cells.filter(el => !el.getAttribute('data-sec'))
                                    .map(el => el.getAttribute('data-sym')).filter(Boolean))];
      if (!secs.length && !syms.length) return;
      const qs = [];
      if (secs.length) qs.push('secs=' + secs.map(encodeURIComponent).join(','));
      if (syms.length) qs.push('syms=' + syms.map(encodeURIComponent).join(','));
      try {
        const r = await fetch('/api/positions-ltp?' + qs.join('&'));
        const j = await r.json();
        if (!j.ok) return;
        const ltpMap = j.ltp_map || {};   // keyed by sec_id (+ sym for any legacy rows)
        Object.entries(ltpMap).forEach(([k, data]) => { _ltpLive[k] = data?.ltp; });
        _patchLtpCells();
      } catch (e) { }
    }
    setInterval(() => { if (activeTab === 'orders') _fetchPositionLtp(); }, 3000);
    // per-group leg-checkbox All/None → re-tick then recompute the subset total
    window._grpLegSel = function (grpId, on) {
      document.querySelectorAll(`.grp-leg-chk[data-grp="${grpId}"]`).forEach(c => { c.checked = on; });
      _patchLtpCells();
    };

    // Fetch net + per-leg Delta/Vega + underlying spot-move for every open group
    // (light route: no candle series). Stores per-leg dqty/vqty by sec_id so the
    // selected-legs bar (in _patchLtpCells) can sum any subset; paints the group
    // spot-move span. Called after the open table renders.
    window._grpGreeks = window._grpGreeks || {};
    window._fetchGroupGreeks = async function () {
      const groups = document.querySelectorAll('#ord-open details[data-greekqs]');
      for (const el of groups) {
        const grpId = el.getAttribute('data-grpid');
        const qs = el.getAttribute('data-greekqs');
        if (!grpId || !qs || qs === 'ids=') continue;
        try {
          const g = await (await fetch('/api/position-greeks?' + qs)).json();
          if (!g || !g.ok) continue;
          const legMap = {};
          (g.legs || []).forEach(L => { if (L.sec_id != null) legMap[String(L.sec_id)] = L; });
          window._grpGreeks[grpId] = { net_delta: g.net_delta, net_vega: g.net_vega, spot: g.spot,
            spot_move: g.spot_move, spot_entry: g.spot_entry, symbol: g.symbol, legs: legMap };
          const sm = document.querySelector(`.grp-spot-move[data-grp="${grpId}"]`);
          if (sm) {
            if (g.spot_move != null && g.spot_entry != null) {
              const c = g.spot_move >= 0 ? '#3fb950' : '#f85149';
              sm.innerHTML = `${g.symbol || 'spot'} <b style="color:${c}">${g.spot_move >= 0 ? '+' : ''}${g.spot_move.toFixed(1)}</b>`
                + ` <span style="color:#6e7681;font-size:10px">(${g.spot_entry.toFixed(0)}→${(g.spot || 0).toFixed(0)})</span>`;
            } else if (g.spot != null) {
              sm.innerHTML = `${g.symbol || 'spot'} <b>${g.spot.toFixed(0)}</b>`;
            }
          }
        } catch (e) { /* greeks are best-effort — never block the table */ }
      }
      _patchLtpCells();   // refresh selected Δ/V now that per-leg greeks are in
    };

    function _patchLtpCells() {
      document.querySelectorAll('.ltp-cell').forEach(el => {
        const key = el.getAttribute('data-sec') || el.getAttribute('data-sym');   // sec_id join (TRAP #166)
        const raw = _ltpLive[key];
        // _ltpLive[key] can be a number (from _fetchPositionLtp) or object (from SSE)
        const ltp = typeof raw === 'number' ? raw : raw?.ltp;
        if (ltp == null) return;
        el.textContent = ltp.toFixed(2);
        // patch points cell
        const entry = parseFloat(el.getAttribute('data-entry')) || 0;
        const side = el.getAttribute('data-side');
        const qty = parseFloat(el.getAttribute('data-qty')) || 1;
        const pts = side === 'BUY' ? ltp - entry : entry - ltp;
        const unrl = pts * qty;
        // Scope to THIS row, not the document. The same contract can be open in
        // two strategies at once (e.g. a leg shared by ORB+Supertrend and Debit
        // Vertical) — a document-wide querySelector only ever returns the first
        // match, so every later row's Points/P&L/%Ret stayed "—" and its group
        // TOTAL silently dropped that leg.
        const _row = el.closest('tr') || document;
        const ptEl = _row.querySelector('.pts-cell');
        const unEl = _row.querySelector('.unrl-cell');
        const retEl = _row.querySelector('.ret-cell');
        if (ptEl) { ptEl.textContent = (pts >= 0 ? '+' : '') + pts.toFixed(1); ptEl.style.color = pts >= 0 ? '#3fb950' : '#f85149'; }
        if (unEl) { unEl.textContent = (unrl >= 0 ? '+' : '') + Math.round(unrl).toLocaleString('en-IN'); unEl.style.color = unrl >= 0 ? '#3fb950' : '#f85149'; }
        if (retEl) {
          const inv = entry * qty;
          const retPct = inv > 0 ? ((unrl / inv) * 100).toFixed(2) + '%' : '—';
          retEl.textContent = retPct;
          retEl.style.color = unrl >= 0 ? '#3fb950' : '#f85149';
        }
      });
      // Carried-over positional legs — extend the day's Run-Up/Run-Down from the
      // live LTP feed (their frozen tags are stale; pos_monitor is today-scoped).
      document.querySelectorAll('.runup-cell').forEach(el => {
        const sym = el.getAttribute('data-sym');
        const ext = _carryRunExt[el.getAttribute('data-rk') || sym];
        if (!ext) return;
        const raw = _ltpLive[el.getAttribute('data-sec') || sym];   // sec_id join (TRAP #166)
        const ltp = typeof raw === 'number' ? raw : raw?.ltp;
        if (ltp == null) return;
        ext.maxL = Math.max(ext.maxL, ltp);
        ext.minL = Math.min(ext.minL, ltp);
        const upPt   = ext.side === 'BUY' ? (ext.maxL - ext.entry) : (ext.entry - ext.minL);
        const downPt = ext.side === 'BUY' ? (ext.minL - ext.entry) : (ext.entry - ext.maxL);
        el.innerHTML = _ruCell(upPt, upPt * ext.qty, upPt / ext.entry * 100);
        // Same-row lookup — a sym-only document query would patch the wrong
        // strategy's leg when one contract is open in two strategies.
        const dEl = (el.closest('tr') || document).querySelector('.rundown-cell');
        if (dEl) dEl.innerHTML = _ruCell(downPt, downPt * ext.qty, downPt / ext.entry * 100);
      });
      // per-strategy-table totals — sum pts/unrl/inv across each table's own rows
      document.querySelectorAll('table[id^="grp_"]').forEach(tbl => {
        let totPts = 0, totUnrl = 0, totInv = 0, any = false;
        let selUnrl = 0, selPts = 0, selN = 0, totN = 0;   // per-leg-checkbox subset total (user ask)
        let selDelta = 0, selDeltaDec = 0, selVega = 0, gHas = false;   // Δ (units + decimal) / V of the selected legs
        const gg = (window._grpGreeks || {})[tbl.id];      // this group's per-leg greeks (may be absent)
        tbl.querySelectorAll('tbody tr').forEach(tr => {
          const ltpCell = tr.querySelector('.ltp-cell');
          const unEl = tr.querySelector('.unrl-cell');
          if (!ltpCell || !unEl || unEl.textContent === '—') return;
          any = true;
          const entry = parseFloat(ltpCell.getAttribute('data-entry')) || 0;
          const qty = parseFloat(ltpCell.getAttribute('data-qty')) || 0;
          const rowUnrl = parseFloat(unEl.textContent.replace('+', '').replace(/,/g, '')) || 0;
          totUnrl += rowUnrl;
          totInv += entry * qty;
          const ptEl = tr.querySelector('.pts-cell');
          const rowPts = ptEl ? (parseFloat(ptEl.textContent.replace('+', '')) || 0) : 0;
          totPts += rowPts;
          totN++;
          const chk = tr.querySelector('.grp-leg-chk');   // ticked (or no box) → in subset
          if (!chk || chk.checked) {
            selUnrl += rowUnrl; selPts += rowPts; selN++;
            if (gg && gg.legs) {
              const lg = gg.legs[String(ltpCell.getAttribute('data-sec'))];
              if (lg && lg.dqty != null) {
                selDelta += lg.dqty; selVega += lg.vqty; gHas = true;
                if (lg.d != null) selDeltaDec += lg.d;   // decimal per-unit net delta
              }
            }
          }
        });
        const id = tbl.id;
        if (!any) return;
        // update EVERY matching cell (tfoot TOTAL row + the collapsed-group
        // summary line — task 68 shows ₹/pts/% right in the summary), so use
        // querySelectorAll not querySelector.
        // each total cell colors by its OWN sign — P&L drives the money color,
        // NOT the raw points sum (points across different-qty legs can't be summed
        // meaningfully, so a −0.2 point-sum must never paint a +₹3,326 P&L red).
        const ptCol = totPts >= 0 ? '#3fb950' : '#f85149';
        const unCol = totUnrl >= 0 ? '#3fb950' : '#f85149';
        const ptTxt = (totPts >= 0 ? '+' : '') + totPts.toFixed(1);
        const unTxt = (totUnrl >= 0 ? '+' : '') + Math.round(totUnrl).toLocaleString('en-IN');
        const retTxt = totInv > 0 ? ((totUnrl / totInv) * 100).toFixed(2) + '%' : '—';
        document.querySelectorAll(`.grp-tot-pts[data-grp="${id}"]`).forEach(el => { el.textContent = ptTxt; el.style.color = ptCol; });
        document.querySelectorAll(`.grp-tot-unrl[data-grp="${id}"]`).forEach(el => { el.textContent = unTxt; el.style.color = unCol; });
        document.querySelectorAll(`.grp-tot-ret[data-grp="${id}"]`).forEach(el => { el.textContent = retTxt; el.style.color = unCol; });
        // selected-leg subset total (all ticked → equals the group TOTAL above)
        // INDEX (underlying) point-move for this group — shown here instead of the
        // combined premium points (that's already in 3 other places). spot_move
        // comes from the group greeks fetch (underlying move since entry).
        let idxTxt = '';
        if (gg && gg.spot_move != null) {
          const im = gg.spot_move, ic = im >= 0 ? '#3fb950' : '#f85149';
          idxTxt = ` · <span style="color:#8b949e;font-weight:400">${gg.symbol || 'index'} <b style="color:${ic}">${im >= 0 ? '+' : ''}${im.toFixed(1)}pt</b></span>`;
        }
        let selTxt = selN === 0 ? 'koi leg select nahi'
          : `<span class="pm-pnl">${selUnrl >= 0 ? '+' : ''}₹${Math.round(selUnrl).toLocaleString('en-IN')}</span>`
            + ` · ${selN}/${totN} leg${idxTxt}`;
        if (selN > 0 && gHas) {
          // net Δ as a DECIMAL (per-unit, e.g. +0.25) — not position delta in units
          selTxt += ` <span style="color:#8b949e;font-weight:400">· net Δ <b style="color:#adbac7">${selDeltaDec >= 0 ? '+' : ''}${selDeltaDec.toFixed(2)}</b>`
            + ` · V <b style="color:${selVega >= 0 ? '#3fb950' : '#f85149'}">${selVega >= 0 ? '+' : ''}${Math.round(selVega).toLocaleString('en-IN')}</b></span>`;
        }
        document.querySelectorAll(`.grp-sel-tot[data-grp="${id}"]`).forEach(el => {
          el.innerHTML = selTxt; el.style.color = selN === 0 ? '#6e7681' : (selUnrl >= 0 ? '#3fb950' : '#f85149');
        });
      });
      // grand TOTAL across ALL open-position rows (item D) — feeds the TOTAL bar
      (function () {
        const unrlB = document.getElementById('open-tot-unrl');  // legacy grand-total cell (may be absent — per-group totals live on each group now)
        let totPts = 0, totUnrl = 0, totInv = 0, any = false;
        document.querySelectorAll('#ord-open table[id^="grp_"] tbody tr').forEach(tr => {
          const ltpCell = tr.querySelector('.ltp-cell');
          const unEl = tr.querySelector('.unrl-cell');
          if (!ltpCell || !unEl || unEl.textContent === '—') return;
          any = true;
          const entry = parseFloat(ltpCell.getAttribute('data-entry')) || 0;
          const qty = parseFloat(ltpCell.getAttribute('data-qty')) || 0;
          totUnrl += parseFloat(unEl.textContent.replace('+', '').replace(/,/g, '')) || 0;
          totInv += entry * qty;
          const ptEl = tr.querySelector('.pts-cell');
          if (ptEl) totPts += parseFloat(ptEl.textContent.replace('+', '')) || 0;
        });
        if (any && unrlB) {
          const ptsB = document.getElementById('open-tot-pts');
          const retB = document.getElementById('open-tot-ret');
          if (ptsB) { ptsB.textContent = (totPts >= 0 ? '+' : '') + totPts.toFixed(1); ptsB.style.color = totPts >= 0 ? '#3fb950' : '#f85149'; }
          unrlB.textContent = (totUnrl >= 0 ? '+' : '') + Math.round(totUnrl).toLocaleString('en-IN'); unrlB.style.color = totUnrl >= 0 ? '#3fb950' : '#f85149';
          if (retB) { const r = totInv > 0 ? ((totUnrl / totInv) * 100).toFixed(2) + '%' : '—'; retB.textContent = r; retB.style.color = totUnrl >= 0 ? '#3fb950' : '#f85149'; }
        }
        // NET summary panel — always update (even when no open positions, show realized + 0 unrealized)
        _updateNetPanel(totUnrl);
      })();
      _patchPeakRunCells();   // task 80 — Summary view "Running" column, same tick
    }
    function _updateNetPanel(unrealizedPnl) {
      const _realized = (window._realizedTot || {}).n || 0;
      const _unrl = unrealizedPnl || 0;
      const _net = _realized + _unrl;
      const _fmt = (v) => (v >= 0 ? '+' : '') + '₹' + Math.abs(Math.round(v)).toLocaleString('en-IN');
      const _col = (v) => v >= 0 ? '#3fb950' : '#f85149';
      const rEl = document.getElementById('net-realized');
      const uEl = document.getElementById('net-unrealized');
      const nEl = document.getElementById('net-total');
      if (rEl) { rEl.textContent = _fmt(_realized); rEl.style.color = _col(_realized); }
      if (uEl) { uEl.textContent = _unrl !== 0 ? _fmt(_unrl) : '₹0'; uEl.style.color = _unrl >= 0 ? '#3fb950' : '#f85149'; }
      if (nEl) {
        nEl.textContent = _fmt(_net); nEl.style.color = _col(_net);
        const panel = document.getElementById('net-pnl-panel');
        if (panel) panel.style.borderColor = _net >= 0 ? '#238636' : '#6e2c2c';
      }
    }

    // DOMContentLoaded, NOT setTimeout(...,0) — this bit is why the split broke.
    // Back when all of this was one 9.5k-line inline <script>, a 0ms timer queued
    // here could only fire after that whole block had run, so everything it
    // touches was already defined. As separate <script src> files the browser is
    // free to run the timer BETWEEN two of them: switchTab('calendar') fired while
    // app-13 (calendarRender) hadn't loaded yet — "Uncaught ReferenceError:
    // calendarRender is not defined". DOMContentLoaded fires only once the parser
    // and EVERY classic script are done, which is the guarantee this code always
    // wanted. No readyState fallback needed: these are plain <script src> tags in
    // the document, so they always execute while parsing, before this event.
    document.addEventListener('DOMContentLoaded', () => {
      startLtpStream();
      loadAll();
      // deep-link from the shared topnav on other pages (/?tab=log) overrides the saved tab
      var _qt = new URLSearchParams(location.search).get('tab');
      switchTab(_qt || activeTab);   // else restore last active tab (localStorage)
    });

