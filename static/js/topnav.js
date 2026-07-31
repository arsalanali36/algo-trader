/* topnav.js — ONE shared global app-nav header for every standalone page
   (stats2 / curves / gex / whatif / fii-flow / registry2 / reports / …).
   index.html has its own .hdr .tabs nav (app-15-mobile.js) — this self-skips there.

   Injects its own CSS + a top bar with brand + inline links (desktop) that
   collapse to a hamburger drawer on ≤760px. Zero page-markup assumptions, so it
   drops onto any page with a single <script defer src=...> tag. Static (in-flow)
   so it never fights a page's own sticky control bar; the drawer/scrim are fixed
   overlays. Single source of the nav list → change once, every page updates. */
(function () {
  'use strict';

  // canonical destinations (all real routes) — same order everywhere
  var NAV = [
    { href: '/',              icon: '🏠', label: 'Dashboard' },
    { href: '/stats2',        icon: '📊', label: 'Stats' },
    { href: '/curves',        icon: '📈', label: 'Curves' },
    { href: '/gex',           icon: '🟢', label: 'GEX' },
    { href: '/whatif',        icon: '🧪', label: 'What-If' },
    { href: '/fii-flow',      icon: '🏦', label: 'FII Flow' },
    { href: '/registry2',     icon: '🗂️', label: 'Strategies' },
    { href: '/reports',       icon: '📋', label: 'Reports' },
    { href: '/intervention',  icon: '🖐', label: 'Intervention' }
  ];

  var CSS = [
    '#gnav{display:flex;align-items:center;gap:10px;padding:8px 14px;',
      'background:linear-gradient(180deg,#11161d,#0d1117);border-bottom:1px solid #30363d;',
      'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;position:relative;z-index:60}',
    '#gnav .gn-burger{display:none;align-items:center;justify-content:center;width:36px;height:36px;flex:0 0 auto;',
      'background:#21262d;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:19px;line-height:1;cursor:pointer}',
    '#gnav .gn-brand{display:flex;align-items:center;gap:7px;color:#e6edf3;font-weight:800;font-size:15px;',
      'text-decoration:none;white-space:nowrap}',
    '#gnav .gn-sp{flex:1}',
    '#gnav .gn-links{display:flex;align-items:center;gap:5px;flex-wrap:wrap}',
    '#gnav .gn-links a{display:inline-flex;align-items:center;gap:5px;color:#adbac7;text-decoration:none;font-size:12.5px;',
      'font-weight:600;padding:5px 10px;border-radius:7px;border:1px solid transparent;white-space:nowrap}',
    '#gnav .gn-links a:hover{background:#21262d;color:#e6edf3}',
    '#gnav .gn-links a.on{background:rgba(31,111,235,.16);color:#58a6ff;border-color:rgba(31,111,235,.45)}',
    // drawer + scrim (fixed overlays)
    '#gn-scrim{position:fixed;inset:0;background:#000a;z-index:99998;opacity:0;pointer-events:none;transition:opacity .22s}',
    '#gn-scrim.open{opacity:1;pointer-events:auto}',
    '#gn-drawer{position:fixed;top:0;left:0;bottom:0;width:82%;max-width:300px;background:#161b22;',
      'border-right:1px solid #30363d;box-shadow:2px 0 18px #0008;z-index:99999;overflow-y:auto;',
      'transform:translateX(-106%);transition:transform .22s ease}',
    '#gn-drawer.open{transform:translateX(0)}',
    '#gn-drawer .gn-dh{display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid #30363d;',
      'font-weight:800;color:#e6edf3;font-size:15px;position:sticky;top:0;background:#161b22}',
    '#gn-drawer .gn-dh .x{margin-left:auto;background:none;border:0;color:#8b949e;font-size:20px;cursor:pointer;line-height:1}',
    '#gn-drawer a{display:flex;align-items:center;gap:11px;padding:12px 18px;color:#e6edf3;text-decoration:none;',
      'font-size:14px;font-weight:600;border-bottom:1px solid #21262d}',
    '#gn-drawer a:hover{background:#21262d}',
    '#gn-drawer a.on{color:#58a6ff;background:rgba(31,111,235,.12);box-shadow:inset 3px 0 #1f6feb}',
    '#gn-drawer a .gi{width:20px;text-align:center;flex:0 0 auto}',
    '@media(max-width:760px){',
      '#gnav{position:sticky;top:0;z-index:9000;padding:7px 12px;gap:9px}',
      '#gnav .gn-burger{display:inline-flex}',
      '#gnav .gn-links{display:none}',
      '#gnav .gn-brand{font-size:14px}',
    '}'
  ].join('');

  function norm(p) { return (p !== '/' && p.slice(-1) === '/') ? p.slice(0, -1) : p; }

  function init() {
    // index.html already has full nav; never double up
    if (document.querySelector('.hdr .tabs') || document.getElementById('gnav')) return;
    if (document.body.getAttribute('data-no-topnav') != null) return;

    var here = norm(location.pathname);

    var st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);

    var links = NAV.map(function (n) {
      var on = (n.href === '/') ? (here === '/') : (here === norm(n.href));
      return '<a href="' + n.href + '"' + (on ? ' class="on"' : '') +
             '><span class="gi">' + n.icon + '</span>' + n.label + '</a>';
    }).join('');

    var bar = document.createElement('header');
    bar.id = 'gnav';
    bar.innerHTML =
      '<button class="gn-burger" type="button" aria-label="Menu">&#9776;</button>' +
      '<a class="gn-brand" href="/">⚡ Algo Trader</a>' +
      '<div class="gn-sp"></div>' +
      '<nav class="gn-links">' + links + '</nav>';
    document.body.insertBefore(bar, document.body.firstChild);

    var scrim = document.createElement('div'); scrim.id = 'gn-scrim';
    var drawer = document.createElement('nav'); drawer.id = 'gn-drawer';
    drawer.innerHTML =
      '<div class="gn-dh">⚡ Algo Trader<button class="x" type="button" aria-label="Close">&times;</button></div>' +
      links;
    document.body.appendChild(scrim);
    document.body.appendChild(drawer);

    function close() { drawer.classList.remove('open'); scrim.classList.remove('open'); }
    function open() { drawer.classList.add('open'); scrim.classList.add('open'); }

    bar.querySelector('.gn-burger').addEventListener('click', open);
    drawer.querySelector('.gn-dh .x').addEventListener('click', close);
    scrim.addEventListener('click', close);
    // drawer links are real hrefs → let them navigate (also closes via unload)
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
