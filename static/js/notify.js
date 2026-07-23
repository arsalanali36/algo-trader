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
      // Source ka display naam. Backend (`notify.listing`) har record pe
      // `source_label` bhejta hai — registry ka naam, read-time pe resolve hua.
      // Yahan regLabel() nahi chalega: notify.js registry.js se pehle load hoti
      // hai, aur ye page ke bahar (toast) bhi render hoti hai. Raw `source`
      // title= me rehta hai — debugging ke liye ek hover door.
      function _src(n) { return String(n.source_label || n.source || ''); }
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
          + (n.source ? '<div title="' + _esc(n.source) + '" style="font-size:10px;color:#8b949e;margin-top:3px">' + _esc(_src(n)) + '</div>' : '')
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

      // ── Grouping ───────────────────────────────────────────────────────────
      // 2026-07-17: a single Dhan 401 burst at market open produced 50 rows —
      // 24 "ema_v1: <SYM> intraday 401" + 26 "ARS_CHAIN_V1_PAPER: fetch_daily
      // <SYM>: HTTP 401", one per symbol. One problem, 50 rows, bell useless.
      //
      // Grouping lives HERE and not in _signature(): that function keeps symbols
      // distinct on purpose ("INFY vs KOTAKBANK genuinely alag cheez hai") and
      // it's right — when ONE symbol fails, you want to see which. The records
      // stay granular; only the display collapses. Nothing is dropped or merged
      // server-side, so count / resolve / history all behave exactly as before.
      //
      // The symbol list comes from the app's own config, never a regex guess at
      // what "looks like" a ticker (HTTP, DH, ERROR are all-caps too).
      var _symCache = null;
      function _knownSyms() {
        if (_symCache) return _symCache;
        var s = { NIFTY: 1, BANKNIFTY: 1, FINNIFTY: 1, MIDCPNIFTY: 1, SENSEX: 1 };
        var n = 5;
        try {
          var cfg = (typeof GLOBAL_CONFIG !== 'undefined' && GLOBAL_CONFIG) || {};
          Object.keys(cfg).forEach(function (k) {
            var v = cfg[k];
            if (!v || typeof v !== 'object') return;
            var sy = v.symbols, arr = [];
            if (typeof sy === 'string') arr = sy.split(',');
            else if (Array.isArray(sy)) arr = sy;
            arr.forEach(function (x) {
              x = String(x).trim().toUpperCase();
              if (x && !s[x]) { s[x] = 1; n++; }
            });
          });
        } catch (e) { }
        if (n > 5) _symCache = s;   // only cache once the config has actually loaded
        return s;
      }
      // Same shape as error_watch._signature (digits -> #), plus symbol masking.
      function _famKey(n) {
        var syms = _knownSyms();
        var s = String(n.msg || '').replace(/0x[0-9a-fA-F]+/g, '#').replace(/\d+/g, '#');
        s = s.split(/\s+/).map(function (w) {
          var t = w.replace(/[^A-Za-z0-9&\-]/g, '').toUpperCase();
          return (t && syms[t]) ? '<SYM>' : w;
        }).join(' ');
        return (n.source || '') + '|' + s.slice(0, 120);
      }
      var _openGroups = {};
      function _toggleGroup(k) {
        _openGroups[k] = !_openGroups[k];
        _renderPanel(_cache);
      }
      window._notifToggleGroup = _toggleGroup;

      function _row(n, indent) {
        var cfg = _lv(n.level);
        // resolved = uski asli wajah khatam ho chuki hai (alert file se hat gaya).
        // Row rehti hai — history kabhi nahi jaati — bas chup + "fixed" ho jaati hai.
        return '<div style="padding:8px 12px;border-bottom:1px solid #21262d;display:flex;gap:8px;align-items:flex-start;'
          + (indent ? 'padding-left:30px;background:#0d1117;' : '')
          + (n.read ? 'opacity:.55' : (indent ? '' : 'background:' + cfg.bg + '33')) + '">'
          + '<span style="font-size:11px;margin-top:2px">' + (n.resolved ? '✅' : cfg.icon) + '</span>'
          + '<div style="flex:1;min-width:0">'
          + '<div style="font-size:12px;color:#e6edf3;word-break:break-word;line-height:1.4;'
          + (n.resolved ? 'text-decoration:line-through;text-decoration-color:#6e7681' : '') + '">' + _esc(n.msg) + '</div>'
          + '<div style="font-size:10px;color:#6e7681;margin-top:3px">'
          + (n.resolved ? '<span style="color:#3fb950;font-weight:700">✓ fixed</span> · ' : '')
          + _ago(n.last_ts || n.ts)
          + (n.source ? ' · <span title="' + _esc(n.source) + '">' + _esc(_src(n)) + '</span>' : '')
          + (n.count > 1 ? ' · <span style="color:' + cfg.c + ';font-weight:700">×' + n.count + '</span>' : '')
          + '</div></div></div>';
      }

      function _renderPanel(items) {
        var list = document.getElementById('notif-list');
        if (!list) return;
        if (!items.length) {
          list.innerHTML = '<div style="padding:22px 12px;text-align:center;color:#6e7681;font-size:12px">Abhi tak koi notification nahi 🎉</div>';
          return;
        }
        // group, newest-first order preserved by first appearance
        var order = [], byKey = {};
        items.forEach(function (n) {
          var k = _famKey(n);
          if (!byKey[k]) { byKey[k] = []; order.push(k); }
          byKey[k].push(n);
        });
        list.innerHTML = order.map(function (k) {
          var g = byKey[k];
          if (g.length === 1) return _row(g[0], false);
          var head = g[0], cfg = _lv(head.level);
          var open = !!_openGroups[k];
          var unread = g.filter(function (n) { return !n.read; }).length;
          var fixed = g.filter(function (n) { return n.resolved; }).length;
          var hits = g.reduce(function (a, n) { return a + (parseInt(n.count, 10) || 1); }, 0);
          var esc = k.replace(/'/g, "\\'").replace(/"/g, '&quot;');
          return '<div style="border-bottom:1px solid #21262d;'
            + (unread ? 'background:' + cfg.bg + '33' : 'opacity:.55') + '">'
            + '<div onclick="_notifToggleGroup(\'' + esc + '\')" style="padding:8px 12px;display:flex;gap:8px;align-items:flex-start;cursor:pointer">'
            + '<span style="font-size:11px;margin-top:2px">' + (fixed === g.length ? '✅' : cfg.icon) + '</span>'
            + '<div style="flex:1;min-width:0">'
            + '<div style="font-size:12px;color:#e6edf3;word-break:break-word;line-height:1.4">'
            + _esc(head.msg) + '</div>'
            + '<div style="font-size:10px;color:#6e7681;margin-top:3px">'
            + (fixed === g.length ? '<span style="color:#3fb950;font-weight:700">✓ fixed</span> · ' : '')
            + _ago(head.last_ts || head.ts)
            + (head.source ? ' · <span title="' + _esc(head.source) + '">' + _esc(_src(head)) + '</span>' : '')
            + ' · <span style="color:' + cfg.c + ';font-weight:700">' + g.length + ' jaise</span>'
            + (hits > g.length ? ' <span style="color:#6e7681">(' + hits + ' hits)</span>' : '')
            + ' · <span style="color:#58a6ff">' + (open ? '▾ chhupao' : '▸ sab dekho') + '</span>'
            + '</div></div></div>'
            + (open ? g.map(function (n) { return _row(n, true); }).join('') : '')
            + '</div>';
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
      // Transient / non-actionable browser noise must NOT land in the persistent
      // notification history — a network blip mid-poll ("Failed to fetch"), a
      // cancelled request, or an opaque cross-origin "Script error." self-heal on
      // the next cycle and only bury the real trading alerts. Genuine dashboard
      // outages are already covered by _poll()'s catch + the liveness banner.
      // Console still logs everything for debugging; genuine code bugs
      // (ReferenceError/TypeError) still notify.
      function _isNoise(text) {
        var t = String(text == null ? '' : text).toLowerCase();
        return !t
          || t.indexOf('failed to fetch') >= 0          // network blip during a background fetch
          || t.indexOf('networkerror') >= 0
          || t.indexOf('load failed') >= 0              // Safari's "Failed to fetch"
          || t.indexOf('err_network') >= 0
          || t.indexOf('err_internet_disconnected') >= 0
          || t.indexOf('err_connection') >= 0
          || t.indexOf('the user aborted') >= 0
          || t.indexOf('the operation was aborted') >= 0
          || t.indexOf('aborterror') >= 0
          || t.indexOf('signal is aborted') >= 0
          || t === 'script error.'                      // opaque cross-origin, no detail
          || t.indexOf('resizeobserver loop') >= 0;     // benign browser warning
      }
      window.addEventListener('error', function (e) {
        var m = e.message || (e.error && e.error.message) || 'unknown';
        if (_isNoise(m)) { try { console.debug('[notify skip]', m); } catch (x) { } return; }
        window.notifPush('UI crash: ' + m + ' @ ' + (e.filename || '?').split('/').pop() + ':' + (e.lineno || 0),
          'error', 'ui:' + String(m).slice(0, 80));
      });
      window.addEventListener('unhandledrejection', function (e) {
        var r = e.reason;
        var m = (r && (r.message || r)) || 'unknown';
        if (_isNoise(m)) { try { console.debug('[notify skip]', m); } catch (x) { } return; }
        window.notifPush('UI promise fail: ' + m,
          'error', 'uip:' + String((r && r.message) || r).slice(0, 80));
      });

      _poll();
      setInterval(_poll, 8000);
    })();
  
