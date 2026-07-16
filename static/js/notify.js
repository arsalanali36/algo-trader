// Auto-extracted from templates/index.html (2026-07-16). Classic script,
// global scope — load order in index.html IS the original code order.
    (function () {
      var _seenMax = parseInt(localStorage.getItem('notifSeenMax') || '0', 10) || 0;
      var _booted = false;   // pehla poll = backlog, uspe toast/sound nahi bajta
      var _muted = localStorage.getItem('notifMuted') === 'true';
      var _actx = null;

      var LV = {
        error: { c: '#f85149', bg: '#3d1214', bd: '#6e2c2c', icon: '🔴', hz: 320 },
        warn:  { c: '#d29922', bg: '#3b2f11', bd: '#6b5420', icon: '🟠', hz: 480 },
        info:  { c: '#58a6ff', bg: '#122b3d', bd: '#1f4a6e', icon: '🔵', hz: 640 }
      };
      function _lv(l) { return LV[l] || LV.error; }
      function _esc(s) { return String(s == null ? '' : s).replace(/[<>&"]/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]; }); }
      function _ago(ms) {
        var s = Math.max(0, Math.floor((Date.now() - ms) / 1000));
        if (s < 60) return s + 's ago';
        if (s < 3600) return Math.floor(s / 60) + 'm ago';
        if (s < 86400) return Math.floor(s / 3600) + 'h ago';
        return new Date(ms).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
      }

      // Alert sound — WebAudio se generate hota hai, koi asset file nahi (ek
      // missing .mp3 se alert chup ho jaana theek isi bug ka doosra roop hota).
      function _beep(level) {
        if (_muted) return;
        try {
          _actx = _actx || new (window.AudioContext || window.webkitAudioContext)();
          if (_actx.state === 'suspended') _actx.resume();
          var cfg = _lv(level);
          // error = 3 urgent beeps, warn = 2, info = 1
          var n = level === 'error' ? 3 : (level === 'warn' ? 2 : 1);
          for (var i = 0; i < n; i++) {
            var t0 = _actx.currentTime + i * 0.18;
            var o = _actx.createOscillator(), g = _actx.createGain();
            o.type = 'square'; o.frequency.value = cfg.hz;
            g.gain.setValueAtTime(0.0001, t0);
            g.gain.exponentialRampToValueAtTime(0.16, t0 + 0.01);
            g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.14);
            o.connect(g); g.connect(_actx.destination);
            o.start(t0); o.stop(t0 + 0.15);
          }
        } catch (e) { /* audio blocked (no user gesture yet) — toast still shows */ }
      }

      function _toast(n) {
        var box = document.getElementById('notif-toasts');
        if (!box) return;
        var cfg = _lv(n.level);
        var el = document.createElement('div');
        el.style.cssText = 'background:' + cfg.bg + ';border:1px solid ' + cfg.bd + ';border-left:3px solid ' + cfg.c
          + ';border-radius:7px;padding:10px 12px;color:#e6edf3;font-size:12.5px;line-height:1.45;'
          + 'box-shadow:0 4px 16px rgba(0,0,0,.5);cursor:pointer;opacity:0;transform:translateX(20px);transition:opacity .2s,transform .2s;';
        el.innerHTML = '<div style="display:flex;gap:8px;align-items:flex-start">'
          + '<span style="font-size:12px">' + cfg.icon + '</span>'
          + '<div style="flex:1;min-width:0"><div style="word-break:break-word">' + _esc(n.msg) + '</div>'
          + (n.source ? '<div style="font-size:10px;color:#8b949e;margin-top:3px">' + _esc(n.source) + '</div>' : '')
          + '</div><span style="color:#8b949e;font-weight:700;padding:0 2px">✕</span></div>';
        // Click = dismiss the toast only. The record stays in the bell's history —
        // this is the whole point: nothing an error does can be made to vanish.
        el.onclick = function () { _drop(el); };
        box.appendChild(el);
        requestAnimationFrame(function () { el.style.opacity = '1'; el.style.transform = 'none'; });
        // Errors stay until clicked; warn/info auto-fade.
        if (n.level !== 'error') setTimeout(function () { _drop(el); }, n.level === 'warn' ? 12000 : 7000);
        // Never let toasts pile past 5 — oldest goes, history keeps it anyway.
        while (box.children.length > 5) _drop(box.firstChild);
      }
      function _drop(el) {
        if (!el || !el.parentNode) return;
        el.style.opacity = '0'; el.style.transform = 'translateX(20px)';
        setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 200);
      }

      function _renderPanel(items) {
        var list = document.getElementById('notif-list');
        if (!list) return;
        if (!items.length) {
          list.innerHTML = '<div style="padding:22px 12px;text-align:center;color:#6e7681;font-size:12px">Abhi tak koi notification nahi 🎉</div>';
          return;
        }
        list.innerHTML = items.map(function (n) {
          var cfg = _lv(n.level);
          // resolved = uski asli wajah khatam ho chuki hai (alert file se hat gaya).
          // Row rehti hai — history kabhi nahi jaati — bas chup + "fixed" ho jaati hai.
          return '<div style="padding:8px 12px;border-bottom:1px solid #21262d;display:flex;gap:8px;align-items:flex-start;'
            + (n.read ? 'opacity:.55' : 'background:' + cfg.bg + '33') + '">'
            + '<span style="font-size:11px;margin-top:2px">' + (n.resolved ? '✅' : cfg.icon) + '</span>'
            + '<div style="flex:1;min-width:0">'
            + '<div style="font-size:12px;color:#e6edf3;word-break:break-word;line-height:1.4;'
            + (n.resolved ? 'text-decoration:line-through;text-decoration-color:#6e7681' : '') + '">' + _esc(n.msg) + '</div>'
            + '<div style="font-size:10px;color:#6e7681;margin-top:3px">'
            + (n.resolved ? '<span style="color:#3fb950;font-weight:700">✓ fixed</span> · ' : '')
            + _ago(n.last_ts || n.ts)
            + (n.source ? ' · ' + _esc(n.source) : '')
            + (n.count > 1 ? ' · <span style="color:' + cfg.c + ';font-weight:700">×' + n.count + '</span>' : '')
            + '</div></div></div>';
        }).join('');
      }

      var _cache = [];
      function _poll() {
        fetch('/api/notifications').then(function (r) { return r.json(); }).then(function (d) {
          _cache = d.items || [];
          var badge = document.getElementById('notif-badge');
          var bell = document.getElementById('notif-bell');
          if (badge) {
            badge.textContent = d.unread > 99 ? '99+' : d.unread;
            badge.style.display = d.unread ? 'block' : 'none';
          }
          if (bell) bell.style.color = d.unread ? '#f85149' : '#8b949e';
          // Fresh (id > last seen) unread → toast + sound. First poll after a page
          // load is backlog, so it only seeds the mark — no alarm for old news.
          var fresh = _cache.filter(function (n) { return n.id > _seenMax && !n.read; });
          if (_booted && fresh.length) {
            fresh.slice().reverse().forEach(_toast);
            var worst = fresh.some(function (n) { return n.level === 'error'; }) ? 'error'
              : (fresh.some(function (n) { return n.level === 'warn'; }) ? 'warn' : 'info');
            _beep(worst);
          }
          if (d.max_id > _seenMax) {
            _seenMax = d.max_id;
            localStorage.setItem('notifSeenMax', String(_seenMax));
          }
          _booted = true;
          var p = document.getElementById('notif-panel');
          if (p && p.style.display !== 'none') _renderPanel(_cache);
        }).catch(function () { /* dashboard down = bigger problems, banner covers it */ });
      }

      window.notifToggle = function (e) {
        if (e) e.stopPropagation();
        var p = document.getElementById('notif-panel');
        if (!p) return;
        var open = p.style.display === 'none';
        p.style.display = open ? 'flex' : 'none';
        if (open) _renderPanel(_cache);
      };
      window.notifMarkAllRead = function () {
        fetch('/api/notifications/read', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
        }).then(_poll);
      };
      window.notifClear = function () {
        if (!confirm('Poori notification history delete karein? Ye wapas nahi aayegi.')) return;
        fetch('/api/notifications/clear', { method: 'POST' }).then(function () {
          _seenMax = 0; localStorage.setItem('notifSeenMax', '0'); _poll();
        });
      };
      document.addEventListener('click', function (e) {
        var p = document.getElementById('notif-panel');
        var b = document.getElementById('notif-bell');
        if (p && p.style.display !== 'none' && !p.contains(e.target) && b && !b.contains(e.target)) {
          p.style.display = 'none';
        }
      });

      // Browser-side failures were the last fully-silent class — a JS crash just
      // froze the UI with nothing anywhere. Now they land in the same history.
      window.notifPush = function (msg, level, key) {
        try {
          fetch('/api/notify', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ msg: msg, level: level || 'error', key: key, source: 'ui' })
          }).then(_poll);
        } catch (e) { }
      };
      window.addEventListener('error', function (e) {
        window.notifPush('UI crash: ' + (e.message || 'unknown') + ' @ ' + (e.filename || '?').split('/').pop() + ':' + (e.lineno || 0),
          'error', 'ui:' + (e.message || '').slice(0, 80));
      });
      window.addEventListener('unhandledrejection', function (e) {
        var r = e.reason;
        window.notifPush('UI promise fail: ' + ((r && (r.message || r)) || 'unknown'),
          'error', 'uip:' + String((r && r.message) || r).slice(0, 80));
      });

      _poll();
      setInterval(_poll, 8000);
    })();
  
