/* app-15-mobile.js — 9:16 / narrow-width shell glue.
   Injects the hamburger button + scrim and toggles the main .tabs bar into a
   slide-in drawer (styling lives in static/css/mobile.css, all behind a
   max-width:760px media query). No-op on desktop widths. */
(function () {
  'use strict';
  function init() {
    var hdr = document.querySelector('.hdr');
    var tabs = hdr && hdr.querySelector('.tabs');
    if (!hdr || !tabs) return;
    if (document.querySelector('.mobile-burger')) return;   // already wired

    var burger = document.createElement('button');
    burger.type = 'button';
    burger.className = 'mobile-burger';
    burger.setAttribute('aria-label', 'Menu');
    burger.innerHTML = '&#9776;';                            // ☰
    // right-thumb side: drop it at the end of the header (after .hdr-right)
    hdr.appendChild(burger);

    var scrim = document.getElementById('mobile-scrim');
    if (!scrim) {
      scrim = document.createElement('div');
      scrim.id = 'mobile-scrim';
      document.body.appendChild(scrim);
    }

    function close() { tabs.classList.remove('m-open'); scrim.classList.remove('m-open'); }
    function open() { tabs.classList.add('m-open'); scrim.classList.add('m-open'); }
    function toggle() { tabs.classList.contains('m-open') ? close() : open(); }

    burger.addEventListener('click', function (e) { e.stopPropagation(); toggle(); });
    scrim.addEventListener('click', close);

    // Tapping a real nav item closes the drawer; the Reports/More dropdown
    // toggles keep it open so their submenu can expand inline.
    tabs.addEventListener('click', function (e) {
      var t = e.target.closest('.tab');
      if (!t) return;
      var oc = t.getAttribute('onclick') || '';
      if (/Dropdown/.test(oc)) return;
      close();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
