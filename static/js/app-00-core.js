// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    const NIFTY50 = ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "BAJFINANCE", "WIPRO", "KOTAKBANK", "LT", "MARUTI", "HINDUNILVR", "ITC", "ADANIENT", "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND", "POWERGRID", "NTPC", "ONGC"];

    let GLOBAL_CONFIG = {};
    let RUNNING_PIDS = {};
    let activeTab = localStorage.getItem('activeMainTab') || 'orders';
    // NOTE: the pine preload that used to run here moved to the END of the last module (app-16). It relied on function-hoisting across this whole block, which does not survive being split into files.
    let activeCfgTab = 'ema';
    let currentModalTarget = null; // store the config key (e.g. ema_v1) being edited

    // ── Auto-reload every 10 min ──────────────────────────────────────────────
    // A long-lived dashboard slowly leaks browser memory and Chrome eventually
    // crashes the tab ("Aw, Snap!"); a periodic reload clears it (same as opening a
    // fresh tab). The active tab is remembered (localStorage 'activeMainTab'), so the
    // reload lands back where you were. NEVER interrupts: if a modal / Quick-Order
    // panel is open, or you're typing in a field, it defers and reloads the moment
    // it's safe. Root memory-leak fix is separate (heap-snapshot pending).
    (function _autoReloadDashboard() {
      const EVERY = 10 * 60 * 1000;
      let pending = false;
      function _busy() {
        const open = [].some.call(
          document.querySelectorAll('.modal-overlay, #qo-panel, #run-modal-overlay'),
          el => el && getComputedStyle(el).display !== 'none');
        if (open) return true;
        const a = document.activeElement;
        return !!(a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName));
      }
      function _attempt() {
        if (!pending) return;
        if (!_busy()) { location.reload(); return; }
        setTimeout(_attempt, 20000);   // busy → re-check in 20s, reload as soon as free
      }
      setInterval(() => { pending = true; _attempt(); }, EVERY);
    })();

    function flash(msg, color = '#3fb950') {
      const f = document.getElementById('flash');
      f.style.color = color; f.innerText = msg;
      setTimeout(() => f.innerText = '', 3000);
    }

    function switchTab(id) {
      document.querySelectorAll('.tab').forEach(e => e.classList.remove('active'));
      document.querySelectorAll('.tab-body').forEach(e => e.classList.remove('active'));
      let tabEl = document.querySelector(`.tab[onclick="switchTab('${id}')"]`);
      let bodyEl = document.getElementById('tab-' + id);
      // Tabs that live inside a nav dropdown (e.g. Script 3 under More) have no
      // top-level .tab of their own — highlight the dropdown that hosts them, so
      // the tab stays reachable instead of falling through to the Orders default.
      if (!tabEl) {
        const hosted = document.querySelector(`[data-hosts-tab="${id}"]`);
        if (hosted) tabEl = hosted.closest('.tab-dropdown-container')?.querySelector('.tab') || null;
      }
      // Stored tab no longer exists (e.g. removed 'script2') → fall back to Orders
      // instead of leaving the page blank (switchTab used to just return here).
      if (!tabEl || !bodyEl) {
        id = 'orders';
        // Orders & P&L now lives inside the "Orders ▾" dropdown, so it has no
        // top-level .tab of its own — resolve its host container (same as the
        // data-hosts-tab path above) so the fallback still highlights + shows it.
        tabEl = document.querySelector(`.tab[onclick="switchTab('orders')"]`)
          || document.querySelector(`[data-hosts-tab="orders"]`)?.closest('.tab-dropdown-container')?.querySelector('.tab')
          || null;
        bodyEl = document.getElementById('tab-orders');
        if (!tabEl || !bodyEl) return;
      }
      tabEl.classList.add('active');
      bodyEl.classList.add('active');
      activeTab = id;
      localStorage.setItem('activeMainTab', id);
      if (id === 'pine') { pineLoadLatest(); pineLoadHistory(); }
      if (id === 'webhook') { whEnter(); } else { whLeave(); }
      if (id === 'orders') { ordersEnter(); loadPeakGraph(); }
      if (id === 'calendar') { calendarRender(); }
      if (id === 'risk') { renderRiskTab(); }
    }

    function openCredentialsModal() {
      document.getElementById('credentials-modal').style.display = 'flex';
      renderWebhookTab();
    }
    function closeCredentialsModal() {
      document.getElementById('credentials-modal').style.display = 'none';
    }

    function openPwModal() {
      document.getElementById('pw-cur').value = '';
      document.getElementById('pw-new').value = '';
      document.getElementById('pw-new2').value = '';
      var m = document.getElementById('pw-msg'); m.style.display = 'none';
      document.getElementById('pw-modal').style.display = 'flex';
      setTimeout(function () { document.getElementById('pw-cur').focus(); }, 50);
    }
    function closePwModal() {
      document.getElementById('pw-modal').style.display = 'none';
    }
    function _pwMsg(text, ok) {
      var m = document.getElementById('pw-msg');
      m.textContent = text;
      m.style.display = 'block';
      m.style.background = ok ? '#23863622' : '#f8514922';
      m.style.border = '1px solid ' + (ok ? '#238636' : '#f85149');
      m.style.color = ok ? '#3fb950' : '#ff9b95';
    }
    async function submitPwChange() {
      var cur = document.getElementById('pw-cur').value;
      var nw = document.getElementById('pw-new').value;
      var nw2 = document.getElementById('pw-new2').value;
      if (!cur || !nw) { _pwMsg('Sab fields bharo.', false); return; }
      if (nw.length < 4) { _pwMsg('Naya password kam se kam 4 characters.', false); return; }
      if (nw !== nw2) { _pwMsg('Naya password aur confirm match nahi karte.', false); return; }
      var btn = document.getElementById('pw-save');
      btn.disabled = true; btn.textContent = 'Saving…';
      try {
        var r = await fetch('/api/change-password', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ current_password: cur, new_password: nw })
        });
        var d = await r.json();
        if (d.ok) {
          _pwMsg('✅ Password badal gaya.', true);
          setTimeout(closePwModal, 1200);
        } else {
          _pwMsg('❌ ' + (d.error || 'Failed'), false);
        }
      } catch (e) {
        _pwMsg('❌ ' + e, false);
      } finally {
        btn.disabled = false; btn.textContent = 'Save';
      }
    }

    async function riskStartBot(s, mode) {
      let r = await fetch(`/api/start?s=${s}&mode=${mode}`, { method: 'POST' });
      let j = await r.json();
      flash(j.msg, j.msg.includes('✅') ? '#3fb950' : '#d29922');
      await checkStatus();
      renderRmsSummary();
      renderLogRunControls();
    }

    async function riskToggleWebhook(s, mode) {
      let cfg = {}; try { const r = await fetch('/api/config'); cfg = await r.json(); } catch (e) { }
      if (!cfg.webhooks) cfg.webhooks = {};
      if (!cfg.webhooks[s]) cfg.webhooks[s] = {};

      if (mode === 'stop') {
        cfg.webhooks[s].active = false;
      } else {
        cfg.webhooks[s].active = true;
        cfg.webhooks[s].mode = mode;
      }

      const res = await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cfg) });
      if (res.ok) {
        flash(`Webhook ${regLabel(s)} set to ${mode}`, mode === 'stop' ? '#f85149' : '#3fb950');
        if (typeof whLoadStrats === 'function') await whLoadStrats();
        renderRmsSummary();
      } else {
        flash(`Failed to update ${regLabel(s)}`, '#f85149');
      }
    }

    async function riskStopBot(s) {
      let r = await fetch(`/api/stop?s=${s}`, { method: 'POST' });
      let j = await r.json();
      flash(j.msg, '#f85149');
      await checkStatus();
      renderRmsSummary();
      renderLogRunControls();
    }

