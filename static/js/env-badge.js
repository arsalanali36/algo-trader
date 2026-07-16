// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
      (function () {
        var h = location.hostname;
        var isLocal = h === '127.0.0.1' || h === 'localhost' || h.indexOf('192.168.') === 0 || h.indexOf('10.') === 0 || h.indexOf('172.') === 0;
        var b = document.getElementById('envBadge');
        if (b) {
          b.textContent = isLocal ? '🖥️ LOCAL' : '☁️ VPS';
          b.style.background = isLocal ? '#1f6feb22' : '#23863622';
          b.style.color = isLocal ? '#58a6ff' : '#3fb950';
          b.style.borderColor = isLocal ? '#1f6feb' : '#238636';
          document.title = (isLocal ? '🖥️ LOCAL · ' : '☁️ VPS · ') + 'Algo Trader';
        }
        window._IS_LOCAL = isLocal;
        var bar = document.getElementById('vpsSyncBar');
        if (isLocal && bar) {
          bar.style.display = 'inline-flex';
          var last = localStorage.getItem('vps_last_sync');
          if (last) document.getElementById('vpsSyncStatus').textContent = 'last: ' + last;
          var auto = document.getElementById('vpsAutoSync');
          auto.checked = localStorage.getItem('vps_auto_sync') === '1';
          auto.addEventListener('change', function () {
            localStorage.setItem('vps_auto_sync', auto.checked ? '1' : '0');
            _setupVpsAuto();
          });
          _setupVpsAuto();
        }
      })();

      window._vpsAutoTimer = null;
      function _setupVpsAuto() {
        if (window._vpsAutoTimer) { clearInterval(window._vpsAutoTimer); window._vpsAutoTimer = null; }
        var auto = document.getElementById('vpsAutoSync');
        if (window._IS_LOCAL && auto && auto.checked) {
          window._vpsAutoTimer = setInterval(function () { syncFromVps(false); }, 600000);
        }
      }
      async function syncFromVps(manual) {
        if (!window._IS_LOCAL) return;
        var btn = document.getElementById('vpsSyncBtn'), st = document.getElementById('vpsSyncStatus');
        if (btn) { btn.disabled = true; btn.textContent = '⏳ Syncing…'; }
        if (st) st.textContent = 'syncing…';
        try {
          var r = await fetch('/api/sync-from-vps', { method: 'POST' });
          var d = await r.json();
          var t = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
          if (d.ok) {
            localStorage.setItem('vps_last_sync', t);
            if (st) { st.textContent = '✓ synced ' + t; st.style.color = '#3fb950'; }
            if (manual) setTimeout(function () { location.reload(); }, 700);
          } else {
            if (st) { st.textContent = '✗ ' + (d.msg || 'fail'); st.style.color = '#f85149'; }
            if (manual) alert('Sync fail:\n' + (d.msg || '') + '\n\n' + (d.output || ''));
          }
        } catch (e) {
          if (st) { st.textContent = '✗ ' + e; st.style.color = '#f85149'; }
        } finally {
          if (btn) { btn.disabled = false; btn.textContent = '🔄 Sync from VPS'; }
        }
      }
    
