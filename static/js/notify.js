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
      // Option-chain alerts (_ops/option_alerts.py, key opt_<type>_<UNDERLYING>,
      // source "chain") are clickable → open the /curves chart for that underlying.
      function _chainU(n) {
        var k = String(n.dedup || n.key || '');
        if (n.source === 'chain' || k.indexOf('opt_') === 0) {
          var m = k.match(/(BANKNIFTY|FINNIFTY|NIFTY)$/);   // BANKNIFTY before NIFTY (substring)
          return m ? m[1] : 'NIFTY';
        }
        return null;
      }
      function _openChart(u) { try { window.open('/curves?underlying=' + (u || 'NIFTY'), '_blank'); } catch (e) { } }
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
      // Tones — har alert-type ko alag sound de sakte ho (kaan se pehchano). ''
      // = level-based default (purana behaviour).
      var TONES = {
        '':    null,
        beep:  { hz: 480, n: 2, type: 'square',   gap: 0.16 },
        chime: { hz: 660, n: 3, type: 'sine',     gap: 0.13 },
        ping:  { hz: 900, n: 1, type: 'sine',     gap: 0.16 },
        buzz:  { hz: 190, n: 2, type: 'sawtooth', gap: 0.18 },
        alarm: { hz: 720, n: 4, type: 'square',   gap: 0.14 }
      };
      var TONE_LIST = ['', 'beep', 'chime', 'ping', 'buzz', 'alarm'];
      var TONE_NAME = { '': 'Default', beep: 'Beep', chime: 'Chime', ping: 'Ping', buzz: 'Buzz', alarm: 'Alarm' };
      function _beep(level, tone, force) {
        if (_muted && !force) return;
        try {
          _actx = _actx || new (window.AudioContext || window.webkitAudioContext)();
          if (_actx.state === 'suspended') _actx.resume();
          var t = TONES[tone || ''], hz, n, type, gap;
          if (t) { hz = t.hz; n = t.n; type = t.type; gap = t.gap; }
          else { var cfg = _lv(level); hz = cfg.hz; type = 'square'; gap = 0.18; n = level === 'error' ? 3 : (level === 'warn' ? 2 : 1); }
          for (var i = 0; i < n; i++) {
            var t0 = _actx.currentTime + i * gap;
            var o = _actx.createOscillator(), g = _actx.createGain();
            o.type = type; o.frequency.value = hz;
            g.gain.setValueAtTime(0.0001, t0);
            g.gain.exponentialRampToValueAtTime(0.16, t0 + 0.01);
            g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.14);
            o.connect(g); g.connect(_actx.destination);
            o.start(t0); o.stop(t0 + 0.15);
          }
        } catch (e) { /* audio blocked (no user gesture yet) — toast still shows */ }
      }

      // ── Big-number formatting: OI etc. ko 8.5L / 1.2Cr dikhao (≥1 lakh). Strikes/
      //    prices safe (5-digit / decimals ko haath nahi lagata). Display-only.
      function _fmtBig(s) {
        return String(s == null ? '' : s).replace(/\b\d{1,3}(?:,\d{2,3})+\b|\b\d{6,}\b/g, function (m) {
          var v = parseInt(m.replace(/,/g, ''), 10);
          if (isNaN(v) || v < 100000) return m;
          if (v >= 10000000) return (v / 10000000).toFixed(v % 10000000 ? 1 : 0).replace(/\.0$/, '') + 'Cr';
          return (v / 100000).toFixed(v % 100000 ? 1 : 0).replace(/\.0$/, '') + 'L';
        });
      }

      // ── Category — har notification ka stable bucket (settings + filter ke liye).
      //    chain/opt_ alerts → alert TYPE (crush+pop jaisi jodi ek label me merge);
      //    baaki → source (strategy/module). id se localStorage config bind hota hai.
      var _CATM = {
        oi_bomb: { e: '💣', l: 'OI bomb (big add/unwind)' }, oi_unwind: { e: '💣', l: 'OI bomb (big add/unwind)' },
        straddle_crush: { e: '📉', l: 'ATM straddle crush/pop' }, straddle_pop: { e: '📈', l: 'ATM straddle crush/pop' },
        gamma_spike: { e: '⚡', l: 'Gamma spike' }, ce_unwind: { e: '🔄', l: 'Call/Put OI unwind' }, pe_unwind: { e: '🔄', l: 'Call/Put OI unwind' },
        iv_crush: { e: '📉', l: 'ATM IV crush/pop' }, iv_pop: { e: '📈', l: 'ATM IV crush/pop' },
        decay_edge: { e: '🎯', l: 'Theo decay edge/lag' }, decay_lag: { e: '⚠️', l: 'Theo decay edge/lag' },
        delta_drift: { e: '🧭', l: 'Straddle delta drift' }, call_wall_shift: { e: '🧱', l: 'OI wall shift' }, put_wall_shift: { e: '🧱', l: 'OI wall shift' },
        ivrank_high: { e: '🔥', l: 'IV Rank extreme' }, ivrank_low: { e: '🧊', l: 'IV Rank extreme' },
        vrp_gone: { e: '⚠️', l: 'VRP edge gone' }, term_back: { e: '📐', l: 'Term backwardation' },
        skew_steepen: { e: '🩹', l: 'Put-skew steepen' }, spread_wide: { e: '↔️', l: 'Spread wide' },
        // per-instance events (unique group-id in key) — normalized to one TYPE below
        straddle_open: { e: '🩳', l: 'Straddle placed' }, straddle_exit: { e: '🩳', l: 'Straddle exit (target/SL)' },
        straddle_920_fail: { e: '⚠️', l: 'Straddle fire fail' }, straddle_unwind: { e: '⚠️', l: 'Straddle unwind' },
        straddle_hedge_resolve: { e: '⚠️', l: 'Straddle hedge fail' }
      };
      function _cat(n) {
        var k = String(n.dedup || n.key || '');
        // Strip a trailing unique id (STRAD_xxxx / strad_xxxx / hex hash / numeric)
        // so per-instance notifications (straddle open/exit, each with its own group
        // id) collapse into ONE category by TYPE — warna har ek apni row banati hai.
        var base = k.replace(/^opt_/, '')
          .replace(/_(BANKNIFTY|FINNIFTY|MIDCPNIFTY|SENSEX|NIFTY)$/, '')
          .replace(/_strad_?[0-9a-f]{4,}$/i, '').replace(/_STRAD_?[0-9a-f]{4,}$/i, '')
          .replace(/_[0-9a-f]{6,}$/i, '').replace(/_\d{3,}$/, '');
        if (n.source === 'chain' || k.indexOf('opt_') === 0 || base.indexOf('straddle') === 0) {
          var m = _CATM[base];
          if (m) return { id: 'chain:' + m.l, e: m.e, l: m.l, g: 'Curves' };
          return { id: 'chain:' + base, e: '📊', l: (base || 'chain').replace(/_/g, ' '), g: 'Curves' };
        }
        var s = n.source || 'other';
        return { id: 'src:' + s, e: _lv(n.level).icon, l: _src(n) || s, g: 'strategy' };
      }
      function _catCfg() { try { return JSON.parse(localStorage.getItem('notifCatCfg') || '{}'); } catch (e) { return {}; } }
      function _catSet(id) { var c = _catCfg()[id] || {}; return { show: c.show !== false, sound: c.sound !== false, tone: c.tone || '' }; }
      function _catSave(id, patch) { var c = _catCfg(); c[id] = Object.assign(_catSet(id), patch); localStorage.setItem('notifCatCfg', JSON.stringify(c)); }
      var _searchQ = '';

      function _toast(n) {
        var box = document.getElementById('notif-toasts');
        if (!box) return;
        if (!_catSet(_cat(n).id).show) return;   // is category ka toast off hai
        var cfg = _lv(n.level);
        var el = document.createElement('div');
        el.style.cssText = 'background:' + cfg.bg + ';border:1px solid ' + cfg.bd + ';border-left:3px solid ' + cfg.c
          + ';border-radius:7px;padding:10px 12px;color:#e6edf3;font-size:12.5px;line-height:1.45;'
          + 'box-shadow:0 4px 16px rgba(0,0,0,.5);cursor:pointer;opacity:0;transform:translateX(20px);transition:opacity .2s,transform .2s;';
        el.innerHTML = '<div style="display:flex;gap:8px;align-items:flex-start">'
          + '<span style="font-size:12px">' + cfg.icon + '</span>'
          + '<div style="flex:1;min-width:0"><div style="word-break:break-word">' + _fmtBig(_esc(n.msg)) + '</div>'
          + (n.source ? '<div title="' + _esc(n.source) + '" style="font-size:10px;color:#8b949e;margin-top:3px">' + _esc(_src(n)) + '</div>' : '')
          + '</div><span style="color:#8b949e;font-weight:700;padding:0 2px">✕</span></div>';
        // Click dismisses the toast (record stays in the bell's history). For an
        // option-chain alert, the click ALSO opens the /curves chart for that
        // underlying — one tap from "gamma spike" toast to the live curve.
        var _cu = _chainU(n);
        if (_cu) el.title = 'Click → open ' + _cu + ' curves';
        el.onclick = function () { if (_cu) _openChart(_cu); _drop(el); };
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
        var cu = _chainU(n);
        // resolved = uski asli wajah khatam ho chuki hai (alert file se hat gaya).
        // Row rehti hai — history kabhi nahi jaati — bas chup + "fixed" ho jaati hai.
        return '<div ' + (cu ? "onclick=\"window.open('/curves?underlying=" + cu + "','_blank')\" " : '')
          + 'style="padding:8px 12px;border-bottom:1px solid #21262d;display:flex;gap:8px;align-items:flex-start;'
          + (cu ? 'cursor:pointer;' : '')
          + (indent ? 'padding-left:30px;background:#0d1117;' : '')
          + (n.read ? 'opacity:.55' : (indent ? '' : 'background:' + cfg.bg + '33')) + '">'
          + '<span style="font-size:11px;margin-top:2px">' + (n.resolved ? '✅' : cfg.icon) + '</span>'
          + '<div style="flex:1;min-width:0">'
          + '<div style="font-size:12px;color:#e6edf3;word-break:break-word;line-height:1.4;'
          + (n.resolved ? 'text-decoration:line-through;text-decoration-color:#6e7681' : '') + '">' + _fmtBig(_esc(n.msg)) + '</div>'
          + '<div style="font-size:10px;color:#6e7681;margin-top:3px">'
          + (n.resolved ? '<span style="color:#3fb950;font-weight:700">✓ fixed</span> · ' : '')
          + _ago(n.last_ts || n.ts)
          + (n.source ? ' · <span title="' + _esc(n.source) + '">' + _esc(_src(n)) + '</span>' : '')
          + (n.count > 1 ? ' · <span style="color:' + cfg.c + ';font-weight:700">×' + n.count + '</span>' : '')
          + (cu ? ' · <span style="color:#58a6ff">📈 chart</span>' : '')
          + '</div></div></div>';
      }

      function _renderPanel(items) {
        var list = document.getElementById('notif-list');
        if (!list) return;
        var settingsOn = window._notifSettingsOn;
        var setEl = document.getElementById('notif-settings');
        if (setEl) setEl.style.display = settingsOn ? 'block' : 'none';
        list.style.display = settingsOn ? 'none' : 'block';
        if (settingsOn) { _renderSettings(); return; }
        // filter: hidden categories out + live search
        var q = _searchQ.trim().toLowerCase();
        items = items.filter(function (n) {
          if (!_catSet(_cat(n).id).show) return false;
          if (q) {
            var hay = (String(n.msg || '') + ' ' + _src(n) + ' ' + _cat(n).l).toLowerCase();
            if (hay.indexOf(q) < 0) return false;
          }
          return true;
        });
        if (!items.length) {
          list.innerHTML = '<div style="padding:22px 12px;text-align:center;color:#6e7681;font-size:12px">' + (q ? 'Kuch nahi mila "' + _esc(q) + '"' : 'Abhi tak koi notification nahi 🎉') + '</div>';
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
            + _fmtBig(_esc(head.msg)) + '</div>'
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
        if (document.hidden) return;   // background tab — skip poll, resume on focus (load-trim)
        fetch('/api/notifications').then(function (r) { return r.json(); }).then(function (d) {
          _cache = d.items || [];
          // Badge = VISIBLE unread only — a category turned off in settings is
          // fully muted (no toast, no sound, no count, no list row).
          var vu = _cache.filter(function (n) { return !n.read && _catSet(_cat(n).id).show; }).length;
          var badge = document.getElementById('notif-badge');
          var bell = document.getElementById('notif-bell');
          if (badge) {
            badge.textContent = vu > 99 ? '99+' : vu;
            badge.style.display = vu ? 'block' : 'none';
          }
          if (bell) bell.style.color = vu ? '#f85149' : '#8b949e';
          // Fresh (id > last seen) unread + SHOWN → toast + sound. First poll after a
          // page load is backlog, so it only seeds the mark — no alarm for old news.
          var fresh = _cache.filter(function (n) { return n.id > _seenMax && !n.read && _catSet(_cat(n).id).show; });
          if (_booted && fresh.length) {
            fresh.slice().reverse().forEach(_toast);
            // per-category tone — play each DISTINCT (tone,level) once, cap 2 (no cacophony)
            var seen = {}, order = [];
            fresh.forEach(function (n) {
              var cs = _catSet(_cat(n).id); if (!cs.sound) return;
              var key = cs.tone + '|' + n.level;
              if (!seen[key]) { seen[key] = { tone: cs.tone, level: n.level }; order.push(key); }
            });
            order.slice(0, 2).forEach(function (k, i) {
              setTimeout(function () { _beep(seen[k].level, seen[k].tone); }, i * 420);
            });
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

      // ── Settings view (⚙) — per-category show / sound / tone ─────────────────
      function _renderSettings() {
        var el = document.getElementById('notif-settings'); if (!el) return;
        var cats = {}, order = [];
        _cache.forEach(function (n) {
          var c = _cat(n);
          if (!cats[c.id]) { cats[c.id] = { c: c, n: 0 }; order.push(c.id); }
          cats[c.id].n += (parseInt(n.count, 10) || 1);
        });
        order.sort(function (a, b) { var A = cats[a], B = cats[b]; if (A.c.g !== B.c.g) return A.c.g === 'Curves' ? -1 : 1; return B.n - A.n; });
        var GC = 'grid-template-columns:1fr 46px 52px 92px;gap:8px';
        var bar = '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:#12161d;border-bottom:1px solid #21262d;font-size:11px;color:#8b949e">'
          + '<span>Har type ka <b style="color:#e6edf3">show</b> / <b style="color:#e6edf3">sound</b> tick karo</span>'
          + '<span><span onclick="notifSetAll(true)" style="color:#58a6ff;cursor:pointer">✓ sab on</span> · <span onclick="notifSetAll(false)" style="color:#8b949e;cursor:pointer">✗ off</span></span></div>';
        if (!order.length) { el.innerHTML = bar + '<div style="padding:22px 12px;text-align:center;color:#6e7681;font-size:12px">Abhi koi category nahi — notifications aane pe yahan dikhengi.</div>'; return; }
        var colhead = '<div style="display:grid;' + GC + ';padding:6px 12px;background:#0f141b;border-bottom:1px solid #21262d">'
          + '<span style="font-size:9px;color:#6e7681;text-transform:uppercase">Alert type</span>'
          + '<span style="font-size:9px;color:#6e7681;text-align:center">Show</span>'
          + '<span style="font-size:9px;color:#6e7681;text-align:center">Sound</span>'
          + '<span style="font-size:9px;color:#6e7681;text-align:center">Tone</span></div>';
        var toneOpts = function (sel) { return TONE_LIST.map(function (t) { return '<option value="' + t + '"' + (t === sel ? ' selected' : '') + '>' + TONE_NAME[t] + '</option>'; }).join(''); };
        var cbox = function (on, dim, id, field) {
          return '<div onclick="notifSetCat(\'' + id.replace(/'/g, "\\'") + '\',\'' + field + '\')" title="' + field + '" '
            + 'style="width:17px;height:17px;border:1.5px solid ' + (on ? '#1f6feb' : '#30363d') + ';border-radius:4px;cursor:pointer;justify-self:center;'
            + 'display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;'
            + 'background:' + (on ? '#1f6feb' : 'transparent') + ';color:' + (on ? '#fff' : 'transparent') + ';' + (dim ? 'opacity:.4' : '') + '">✓</div>';
        };
        var rows = order.map(function (id) {
          var c = cats[id].c, s = _catSet(id), esc = id.replace(/'/g, "\\'");
          var soundOn = s.show && s.sound;
          return '<div style="display:grid;' + GC + ';align-items:center;padding:8px 12px;border-bottom:1px solid #21262d' + (s.show ? '' : ';opacity:.6') + '">'
            + '<div style="font-size:12px;display:flex;align-items:center;gap:6px;min-width:0"><span>' + c.e + '</span>'
            + '<div style="min-width:0"><div style="color:#e6edf3;overflow:hidden;text-overflow:ellipsis">' + _esc(c.l) + '</div>'
            + '<div style="font-size:9.5px;color:#6e7681">' + c.g + ' · ' + cats[id].n + '×</div></div></div>'
            + cbox(s.show, false, id, 'show')
            + cbox(soundOn, !s.show, id, 'sound')
            + '<div style="display:flex;align-items:center;gap:4px">'
            + '<select onchange="notifSetTone(\'' + esc + '\',this.value)"' + (soundOn ? '' : ' disabled')
            + ' style="flex:1;min-width:0;background:#0d1117;border:1px solid #30363d;border-radius:5px;color:#e6edf3;font-size:11px;padding:3px 4px;cursor:pointer;' + (soundOn ? '' : 'opacity:.4') + '">' + toneOpts(s.tone) + '</select>'
            + '<span onclick="notifTestTone(\'' + s.tone + '\',\'' + (c.g === 'Curves' ? 'warn' : 'error') + '\')" style="font-size:12px;cursor:pointer;color:#8b949e" title="test">▶</span>'
            + '</div></div>';
        }).join('');
        el.innerHTML = bar + colhead + '<div>' + rows + '</div>';
      }
      window.notifToggleSettings = function (e) { if (e) e.stopPropagation(); window._notifSettingsOn = !window._notifSettingsOn; _renderPanel(_cache); };
      window.notifToggleSearch = function (e) {
        if (e) e.stopPropagation();
        var w = document.getElementById('notif-searchwrap'); if (!w) return;
        var on = w.style.display === 'none'; w.style.display = on ? 'block' : 'none';
        var i = document.getElementById('notif-search');
        if (on) { if (i) i.focus(); } else { _searchQ = ''; if (i) i.value = ''; _renderPanel(_cache); }
      };
      window.notifSearch = function (v) { _searchQ = v || ''; _renderPanel(_cache); };
      window.notifSetCat = function (id, field) { var s = _catSet(id); var p = {}; p[field] = !s[field]; _catSave(id, p); _renderSettings(); _poll(); };
      window.notifSetTone = function (id, tone) { _catSave(id, { tone: tone }); };
      window.notifTestTone = function (tone, level) { _beep(level || 'warn', tone, true); };
      window.notifSetAll = function (on) {
        var c = _catCfg();
        _cache.forEach(function (n) { var id = _cat(n).id; c[id] = Object.assign(_catSet(id), { show: !!on, sound: !!on }); });
        localStorage.setItem('notifCatCfg', JSON.stringify(c)); _renderSettings(); _poll();
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
        var fn = e.filename || '';
        // Inline onclick handlers in the HTML (nav tabs, header buttons) call
        // global functions that live in app-*.js loaded at the very bottom of the
        // page. A click during the page-load window — before those scripts run —
        // throws "X is not defined" FROM the inline handler (filename = the page
        // itself, not a .js file). That's a transient load-race: the control works
        // fine once loaded, and a genuinely-missing function is self-evident in the
        // UI, not something the trading-alert bell should carry. Real ReferenceErrors
        // INSIDE a script (filename ends in .js) still notify.
        var inlineRace = /is not defined/i.test(m) && !/\.js(\?|$)/i.test(fn);
        if (_isNoise(m) || inlineRace) { try { console.debug('[notify skip]', m, fn); } catch (x) { } return; }
        window.notifPush('UI crash: ' + m + ' @ ' + (fn || '?').split('/').pop() + ':' + (e.lineno || 0),
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
  
