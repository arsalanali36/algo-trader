// presentation-mode.js — 🎬 YouTube "Presentation Mode" (2026-08-20).
//
// KYUN: recording ke waqt (a) strategy ke NAAM chhupane hon — sirf number
// dikhe (04.03 · VRP… -> 04.03), aur (b) PROFIT/LOSS ka ₹ value chhupe (**)
// jabki points/%/margin/invested/run-up sab dikhte rahein.
//
// Do INDEPENDENT toggle, localStorage-persist, bell ke barabar 🎬 popup se.
//
//  A · numberOnly  -> registry.js ka regLabel/regFull number-only lauta dete
//     hain => POORI app ek hi single-labeller se number-only ho jaati hai
//     (koi surface chhoot nahi sakti). Toggle pe ek halka reload taaki har
//     already-rendered table naye label le le (leaked naam = risk, isliye
//     reload = guaranteed).
//
//  B · hidePnl     -> pure CSS: body.pm-hide-pnl. P&L-VALUE cells (.unrl-cell,
//     .grp-tot-unrl, #open-tot-unrl, NET panel, .pm-pnl marker) ka text
//     font-size:0 + ::after '**'. Points/%/margin/tax/run-up alag classes pe
//     hain => untouched. Instant, koi reload nahi (live toggle safe).
(function () {
  var KEY_NUM = 'pm_number_only', KEY_PNL = 'pm_hide_pnl';
  function on(k) { return localStorage.getItem(k) === '1'; }

  // registry.js is flag ko label-time pe padhta hai (lazy).
  window.__pmNumberOnly = on(KEY_NUM);

  // P&L-value cells ke SELECTORS — sirf profit/loss ₹, aur kuch nahi.
  var PNL = ['.pm-pnl', '.unrl-cell', '.grp-tot-unrl', '.peak-run-cell', '.cal-day-pnl',
    '#open-tot-unrl', '#net-realized', '#net-unrealized', '#net-total', '#cal-stat-gross'];

  function injectCss() {
    if (document.getElementById('pm-style')) return;
    var hide = PNL.map(function (s) { return 'body.pm-hide-pnl ' + s; }).join(',');
    var aft = PNL.map(function (s) { return 'body.pm-hide-pnl ' + s + '::after'; }).join(',');
    var css = hide + '{font-size:0!important;letter-spacing:0!important}'
      + aft + "{content:'**';font-size:.8rem;font-weight:700;color:#8b949e;letter-spacing:normal}";
    var st = document.createElement('style');
    st.id = 'pm-style'; st.textContent = css;
    document.head.appendChild(st);
  }

  function applyPnl() {
    if (document.body) document.body.classList.toggle('pm-hide-pnl', on(KEY_PNL));
  }

  window.pmToggle = function (which) {
    var k = which === 'num' ? KEY_NUM : KEY_PNL;
    localStorage.setItem(k, on(k) ? '0' : '1');
    if (which === 'num') { window.__pmNumberOnly = on(KEY_NUM); location.reload(); }
    else { applyPnl(); syncUi(); }
  };

  function syncUi() {
    var a = document.getElementById('pm-tgl-num'), b = document.getElementById('pm-tgl-pnl');
    if (a) a.checked = on(KEY_NUM);
    if (b) b.checked = on(KEY_PNL);
    var btn = document.getElementById('pm-btn');
    if (btn) btn.style.color = (on(KEY_NUM) || on(KEY_PNL)) ? '#ff5c5c' : '#8b949e';
  }
  window.pmSync = syncUi;

  window.pmPopupToggle = function (ev) {
    if (ev) ev.stopPropagation();
    var p = document.getElementById('pm-popup');
    if (!p) return;
    p.style.display = p.style.display === 'block' ? 'none' : 'block';
    syncUi();
  };

  // bahar click => popup band
  document.addEventListener('click', function (e) {
    var p = document.getElementById('pm-popup'), btn = document.getElementById('pm-btn');
    if (p && p.style.display === 'block' && !p.contains(e.target) && (!btn || !btn.contains(e.target))) {
      p.style.display = 'none';
    }
  });

  function init() { injectCss(); applyPnl(); syncUi(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
