/* mobile-tools.js — collapse a page's own busy toolbar into a per-page "☰" sub-menu
   on MOBILE ONLY. Desktop is untouched (the toolbar stays exactly where it is).

   Usage: mark any toolbar container with data-mtools="Label". On ≤760px it moves into
   a dropdown behind a "☰ Label" button; on desktop the wrapper is display:contents so
   the toolbar renders inline as before. All original elements + their listeners are
   preserved (DOM move, not re-create). Include this script on any page that needs it. */
(function () {
  'use strict';

  var CSS = [
    // desktop: wrapper transparent, button hidden → toolbar renders exactly as before
    '.mtb-wrap{display:contents}',
    '.mtb-btn{display:none}',
    '.mtb-panel{display:contents}',
    '@media(max-width:760px){',
      '.mtb-wrap{display:block;position:relative}',
      '.mtb-btn{display:inline-flex;align-items:center;gap:7px;background:#21262d;border:1px solid #30363d;',
        'border-radius:8px;color:#e6edf3;font-size:12.5px;font-weight:600;padding:8px 13px;cursor:pointer;',
        'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}',
      '.mtb-wrap.open .mtb-btn{background:#1f6feb;border-color:#1f6feb;color:#fff}',
      '.mtb-panel{display:none;position:absolute;top:calc(100% + 5px);left:0;right:0;background:#161b22;',
        'border:1px solid #30363d;border-radius:10px;box-shadow:0 10px 28px rgba(0,0,0,.6);padding:11px;',
        'z-index:9600;max-height:74vh;overflow:auto}',
      '.mtb-wrap.open .mtb-panel{display:block}',
      // the moved toolbar stacks vertically inside the panel; nested rows wrap
      '.mtb-panel > [data-mtools]{display:flex!important;flex-direction:column!important;',
        'align-items:stretch!important;gap:9px!important;width:auto!important;margin:0!important;',
        'position:static!important;background:transparent!important;border:0!important;padding:0!important}',
      '.mtb-panel > [data-mtools] > *{width:100%;max-width:none}',
      '.mtb-panel select,.mtb-panel input,.mtb-panel .mtb-row{width:100%!important;box-sizing:border-box}',
    '}'
  ].join('');

  function isMobile() { return window.matchMedia && window.matchMedia('(max-width:760px)').matches; }

  function collapse(bar) {
    if (bar.__mtb) return; bar.__mtb = 1;
    var label = bar.getAttribute('data-mtools') || 'Menu';
    var w = document.createElement('div'); w.className = 'mtb-wrap';
    var b = document.createElement('button'); b.type = 'button'; b.className = 'mtb-btn';
    b.innerHTML = '&#9776; ' + label;
    var p = document.createElement('div'); p.className = 'mtb-panel';
    bar.parentNode.insertBefore(w, bar);
    w.appendChild(b); w.appendChild(p); p.appendChild(bar);
    b.addEventListener('click', function (e) { e.stopPropagation(); w.classList.toggle('open'); });
    // tapping a real control (not a dropdown-opener) closes the menu
    p.addEventListener('click', function (e) {
      var t = e.target.closest('button,a,[onclick]');
      if (t && !/toggle|open|dropdown|menu|filters/i.test(t.getAttribute('onclick') || '')) {
        setTimeout(function () { w.classList.remove('open'); }, 60);
      }
    });
    document.addEventListener('click', function (e) { if (!w.contains(e.target)) w.classList.remove('open'); });
  }

  function init() {
    var st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
    // only collapse on mobile — desktop keeps the toolbar inline (and unwrapped)
    if (!isMobile()) return;
    [].forEach.call(document.querySelectorAll('[data-mtools]'), collapse);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
