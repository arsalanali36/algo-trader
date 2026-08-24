/* topnav.js — the SAME global header as index.html, cloned onto every standalone
   page (stats2 / curves / gex / whatif / fii-flow / registry2 / reports / …).
   index.html keeps its own live .hdr .tabs (this self-skips there), so the two match.

   Faithful clone of the dashboard header: brand + env badge, the full tab row
   (Logs / Orders & P&L / Stats 2 / Curves / FII Flow / Risk / Strategies) plus the
   Reports ▾ and More ▾ dropdowns with all their items, and clock + avatar on the
   right. In-page-only tabs (Logs/Orders/Risk/Stats/Watch) deep-link to the dashboard
   via /?tab=X (index reads ?tab=). On ≤760px the tab row collapses into a right-side
   hamburger drawer (thumb-friendly). Injects its own CSS; static (in-flow) so it never
   fights a page's own sticky bar. */
(function () {
  'use strict';

  // in-page dashboard tabs → deep-link; cross-page → real routes
  var TABS = [
    { href: '/?tab=log',     label: 'Logs' },
    { href: '/stats2',       label: '📊 Stats 2' },
    { href: '/?tab=risk',    label: '⚠️ Risk' },
    { href: '/registry2',    label: '🗂️ Strategies' }
  ];
  // 📒 Orders ▾ dropdown (Orders & P&L in-page tab + standalone Broker Orders page)
  var ORDERS = [
    { href: '/?tab=orders',   label: '📒 Orders & P&L' },
    { href: '/broker-orders', label: '🧾 Broker Orders' }
  ];
  var REPORTS = [
    { href: '/report',        label: '📆 Daily Report' },
    { href: '/reports',       label: '📋 EOD Reports' },
    { href: '/intervention',  label: '🖐 Intervention' },
    { href: '/brief',         label: '☀️ Brief' },
    { href: '/fii-flow',      label: '🏦 FII Flow' },
    { href: '/presentations', label: '🎬 YT Presentations' },
    { href: '/sl-map',        label: '🛡️ SL Map' }
  ];
  var CURVES = [
    { href: '/curves',  label: '📈 Curves' },
    { href: '/gex',     label: '🟢 GEX Profile' },
    { href: '/whatif',  label: '🧪 Options What-If' },
    { href: '/whatif2', label: '📐 Strategy Builder' },
    { href: '/backtest-lab', label: '🧪 Backtest Lab' },
    { href: '/crypto',  label: '🪙 Crypto (Delta)' }
  ];
  var MORE = [
    { href: '/registry',       label: '🗂️ Strategies (classic)' },
    { href: '/?tab=calendar',  label: '📊 Stats (calendar)' },
    { href: '/script3',        label: '📜 Script 3' },
    { href: '/strategy-equity',label: '📈 Strategy Equity' },
    { href: '/mtm-charts',     label: '📈 MTM Analyzer' },
    { href: '/spec-builder',   label: '🧭 Spec Builder' },
    { href: '/backtest-chart', label: '📊 Results' },
    { href: '/ideas',          label: '💡 Idea Vault' }
  ];
  var AVATAR = [
    { href: '/?tab=config', label: '⚙ Settings' },
    { href: '/logout',      label: '🚪 Logout', danger: true }
  ];

  var CSS = [
    '#gnav{display:flex;align-items:center;gap:12px;height:52px;padding:0 16px;',
      'background:#161b22;border-bottom:1px solid #30363d;position:relative;z-index:70;',
      'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}',
    '#gnav .gn-dot{width:8px;height:8px;border-radius:50%;background:#3fb950;flex:0 0 auto}',
    '#gnav h1{font-size:15px;font-weight:700;color:#fff;white-space:nowrap;margin:0}',
    '#gnav .gn-env{font-size:11px;font-weight:700;padding:2px 9px;border-radius:12px;border:1px solid #30363d;color:#8b949e;white-space:nowrap}',
    '#gnav .gn-tabs{display:flex;align-items:stretch;height:100%;gap:0}',
    '#gnav .gn-tab{display:flex;align-items:center;height:100%;padding:0 13px;color:#8b949e;font-size:13px;',
      'font-weight:600;white-space:nowrap;text-decoration:none;border-bottom:3px solid transparent;cursor:pointer;transition:all .15s}',
    '#gnav .gn-tab:hover{color:#e6edf3;background:rgba(255,255,255,.03)}',
    '#gnav .gn-tab.on{color:#58a6ff;border-bottom-color:#1f6feb;background:rgba(31,111,235,.05)}',
    '#gnav .gn-dd{position:relative;display:flex;align-items:center;height:100%}',
    '#gnav .gn-menu{display:none;position:absolute;top:100%;left:0;background:#161b22;border:1px solid #30363d;',
      'border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,.5);padding:4px 0;min-width:180px;z-index:10001;margin-top:2px}',
    '#gnav .gn-menu.gn-r{left:auto;right:0}',
    '#gnav .gn-menu.show{display:block}',
    '#gnav .gn-menu a{display:block;padding:8px 13px;font-size:12px;color:#c9d1d9;text-decoration:none;white-space:nowrap}',
    '#gnav .gn-menu a:hover{background:#21262d}',
    '#gnav .gn-menu a.danger{color:#f85149}',
    '#gnav .gn-right{margin-left:auto;display:flex;align-items:center;gap:14px}',
    '#gnav .gn-icon{font-size:15px;color:#8b949e;text-decoration:none;cursor:pointer}',
    '#gnav .gn-clock{display:flex;flex-direction:column;align-items:flex-end;line-height:1.2}',
    '#gnav .gn-clock .d{font-size:11px;color:#8b949e;font-weight:600}',
    '#gnav .gn-clock .t{font-size:13px;color:#e6edf3;font-family:monospace;font-weight:700}',
    '#gnav .gn-avatar{width:28px;height:28px;border-radius:50%;background:#1f6feb;color:#fff;display:flex;',
      'align-items:center;justify-content:center;font-size:13px;font-weight:700;cursor:pointer}',
    '#gnav .gn-burger{display:none;align-items:center;justify-content:center;width:36px;height:36px;flex:0 0 auto;',
      'background:#21262d;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:19px;line-height:1;cursor:pointer}',
    '#gnav .gn-drawer-extra{display:none}',
    // scrim
    '#gn-scrim{position:fixed;inset:0;background:#000a;z-index:99998;opacity:0;pointer-events:none;transition:opacity .22s}',
    '#gn-scrim.open{opacity:1;pointer-events:auto}',
    // ── mobile ≤760: tab row → right-side drawer, hamburger on the right (thumb) ──
    '@media(max-width:760px){',
      '#gnav{position:sticky;top:0;z-index:9000;padding:0 12px;gap:9px}',
      '#gnav h1{font-size:14px}',
      '#gnav .gn-right{gap:10px}',
      '#gnav .gn-right .gn-icon,#gnav .gn-right .gn-clock,#gnav .gn-right .gn-avatar-wrap{display:none}',
      '#gnav .gn-burger{display:inline-flex}',
      '#gnav .gn-tabs{position:fixed;top:0;right:0;bottom:0;width:82%;max-width:300px;height:auto;',
        'flex-direction:column;align-items:stretch;gap:0;background:#161b22;border-left:1px solid #30363d;',
        'box-shadow:-2px 0 18px #0008;z-index:99999;overflow-y:auto;padding:4px 0;',
        // hide via display:none (NOT transform/right — on this specific nav element the
        // browser was ignoring both, leaving the drawer stuck VISIBLE at right:0 covering
        // + BLOCKING the right side of every page). display can't get stuck. No slide, but
        // the scrim still fades; reliability > animation here.
        'display:none}',
      '#gnav .gn-tabs.open{display:flex}',
      '#gnav .gn-tab{height:auto;padding:12px 16px;border-bottom:1px solid #21262d;border-left:3px solid transparent;border-bottom-width:1px}',
      '#gnav .gn-tab.on{border-bottom-color:#21262d;border-left-color:#1f6feb}',
      '#gnav .gn-dd{flex-direction:column;align-items:stretch;height:auto}',
      '#gnav .gn-menu{display:block;position:static;border:0;box-shadow:none;margin:0;padding:0;min-width:0;background:#0d1117}',
      '#gnav .gn-menu a{padding-left:34px}',
      '#gnav .gn-drawer-extra{display:block;border-top:1px solid #30363d;margin-top:4px}',
    '}'
  ].join('');

  function norm(p) { return (p !== '/' && p.slice(-1) === '/') ? p.slice(0, -1) : p; }

  function tabHTML(t, here) {
    var path = t.href.split('?')[0];
    var on = path !== '/' && here === path;
    return '<a class="gn-tab' + (on ? ' on' : '') + '" href="' + t.href + '">' + t.label + '</a>';
  }
  function menuHTML(items, id, right) {
    return '<div class="gn-menu' + (right ? ' gn-r' : '') + '" id="' + id + '">' +
      items.map(function (m) {
        return '<a href="' + m.href + '"' + (m.danger ? ' class="danger"' : '') + '>' + m.label + '</a>';
      }).join('') + '</div>';
  }

  function init() {
    if (document.querySelector('.hdr .tabs') || document.getElementById('gnav')) return;   // index has its own
    if (document.body.getAttribute('data-no-topnav') != null) return;

    var here = norm(location.pathname);
    var st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);

    var ordersOn = here === '/broker-orders';
    var ordersDD = '<div class="gn-dd"><span class="gn-tab' + (ordersOn ? ' on' : '') + '" data-dd="ord">📒 Orders & P&L ▾</span>' + menuHTML(ORDERS, 'gn-ord') + '</div>';
    var curvesOn = ['/curves', '/gex', '/whatif'].indexOf(here) >= 0;
    var curvesDD = '<div class="gn-dd"><span class="gn-tab' + (curvesOn ? ' on' : '') + '" data-dd="cur">📈 Curves ▾</span>' + menuHTML(CURVES, 'gn-cur') + '</div>';
    var tabsArr = TABS.map(function (t) { return tabHTML(t, here); });
    tabsArr.splice(1, 0, ordersDD);   // Orders ▾ right after Logs
    tabsArr.splice(3, 0, curvesDD);   // Curves ▾ right after Stats 2
    var tabsHTML = tabsArr.join('') +
      '<div class="gn-dd"><span class="gn-tab" data-dd="rep">📋 Reports ▾</span>' + menuHTML(REPORTS, 'gn-rep') + '</div>' +
      '<div class="gn-dd"><span class="gn-tab" data-dd="more">More ▾</span>' + menuHTML(MORE, 'gn-more') + '</div>' +
      '<div class="gn-drawer-extra">' + AVATAR.map(function (m) {
        return '<a class="gn-tab" href="' + m.href + '"' + (m.danger ? ' style="color:#f85149"' : '') + '>' + m.label + '</a>';
      }).join('') + '</div>';

    var bar = document.createElement('header');
    bar.id = 'gnav';
    bar.innerHTML =
      '<span class="gn-dot"></span><h1>Algo Trader</h1><span class="gn-env" id="gn-env">—</span>' +
      '<nav class="gn-tabs" id="gn-tabs">' + tabsHTML + '</nav>' +
      '<div class="gn-right">' +
        '<a class="gn-icon" href="/?tab=orders" title="Notifications (dashboard)">🔔</a>' +
        '<div class="gn-clock"><span class="d" id="gn-date">—</span><span class="t" id="gn-time">—</span></div>' +
        '<div class="gn-dd gn-avatar-wrap"><div class="gn-avatar" data-dd="av">A</div>' + menuHTML(AVATAR, 'gn-av', true) + '</div>' +
        '<button class="gn-burger" type="button" aria-label="Menu">&#9776;</button>' +
      '</div>';
    document.body.insertBefore(bar, document.body.firstChild);

    var scrim = document.createElement('div'); scrim.id = 'gn-scrim'; document.body.appendChild(scrim);
    var tabs = document.getElementById('gn-tabs');

    // env badge (same rule as env-badge.js: private/loopback host = LOCAL, else VPS)
    (function () {
      var h = location.hostname, local = /^(127\.|192\.168\.|10\.|localhost$)/.test(h);
      var e = document.getElementById('gn-env');
      e.textContent = local ? '💻 LOCAL' : '☁ VPS';
      e.style.color = local ? '#d29922' : '#3fb950';
      e.style.borderColor = local ? '#d29922' : '#238636';
    })();

    // clock (IST)
    function tick() {
      var n = new Date();
      var ist = new Date(n.getTime() + (330 + n.getTimezoneOffset()) * 60000);
      var wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][ist.getDay()];
      var mo = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][ist.getMonth()];
      var hh = ist.getHours(), ap = hh >= 12 ? 'pm' : 'am', h12 = ((hh + 11) % 12) + 1;
      function p(x) { return (x < 10 ? '0' : '') + x; }
      var de = document.getElementById('gn-date'), te = document.getElementById('gn-time');
      if (de) de.textContent = mo + ' ' + ist.getDate() + ', ' + wd;
      if (te) te.textContent = p(h12) + ':' + p(ist.getMinutes()) + ':' + p(ist.getSeconds()) + ' ' + ap;
    }
    tick(); setInterval(tick, 1000);

    // dropdowns (desktop click-toggle)
    function closeMenus() { [].forEach.call(document.querySelectorAll('#gnav .gn-menu.show'), function (m) { m.classList.remove('show'); }); }
    bar.addEventListener('click', function (e) {
      var trg = e.target.closest('[data-dd]');
      if (trg) {
        var map = { ord: 'gn-ord', cur: 'gn-cur', rep: 'gn-rep', more: 'gn-more', av: 'gn-av' };
        var m = document.getElementById(map[trg.getAttribute('data-dd')]);
        var wasOpen = m.classList.contains('show');
        closeMenus(); if (!wasOpen) m.classList.add('show');
        e.stopPropagation();
      }
    });
    document.addEventListener('click', closeMenus);

    // ── mobile declutter: hide a page's OWN top-bar nav links that just duplicate
    // this global nav (e.g. curves/whatif's "GEX · Curves · ← Dashboard" links). Only
    // links whose href is one of our routes, sitting inside a header/top bar, are hidden
    // — page tool-buttons (Panels, View, filters…) are untouched. ──
    if (window.matchMedia && window.matchMedia('(max-width:760px)').matches) {
      var routes = {};
      TABS.concat(ORDERS, CURVES, REPORTS, MORE).forEach(function (n) { routes[n.href.split('?')[0]] = 1; });
      routes['/'] = 1;
      [].forEach.call(document.querySelectorAll('.top a[href], .hdr a[href], header a[href], .brand a[href]'), function (a) {
        if (bar.contains(a)) return;                       // never our own nav
        var h = (a.getAttribute('href') || '').split('?')[0];
        if (routes[h]) a.style.display = 'none';
      });
    }

    // mobile drawer (right side)
    function close() { tabs.classList.remove('open'); scrim.classList.remove('open'); }
    function open() { tabs.classList.add('open'); scrim.classList.add('open'); }
    bar.querySelector('.gn-burger').addEventListener('click', function (e) { e.stopPropagation(); open(); });
    scrim.addEventListener('click', close);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
