/* health-pill.js — ONE always-visible line: "does the app match Zerodha?"
 *
 * WHY (TRAP #191, 2026-08-29): invariant_guard had ALREADY detected that four
 * positions the user was really carrying were missing from the app. Its RED sat
 * unread in a notification bell showing "99+", so for days the dashboard calmly
 * showed a wrong picture. A guard nobody can hear is not a guard.
 *
 * So the guard's verdict gets one pill in the header of EVERY page. Green means
 * the user needs to understand nothing. Red means look now.
 *
 * Reads only /api/health/app-vs-broker, which reads only the status file the
 * guard writes each cycle — a page render never costs a broker call.
 * `stale` is deliberately NOT green: "not checked recently" must never look the
 * same as "all good".
 */
(function () {
  if (window.__healthPillLoaded) return;
  window.__healthPillLoaded = true;

  var CSS = '' +
    '#hpill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;' +
    'font-size:12px;font-weight:600;cursor:pointer;white-space:nowrap;border:1px solid transparent;' +
    'font-family:inherit;line-height:1.4}' +
    '#hpill.ok{background:#0f2d1a;color:#3fb950;border-color:#1f6f3d}' +
    '#hpill.bad{background:#3d1417;color:#ff6b6b;border-color:#a03038;animation:hpulse 1.4s ease-in-out infinite}' +
    '#hpill.meh{background:#21262d;color:#8b949e;border-color:#30363d}' +
    '@keyframes hpulse{0%,100%{opacity:1}50%{opacity:.55}}' +
    '#hpill-pop{position:fixed;z-index:99999;max-width:min(560px,92vw);background:#161b22;color:#e6edf3;' +
    'border:1px solid #30363d;border-radius:10px;padding:12px 14px;font-size:12.5px;line-height:1.6;' +
    'box-shadow:0 12px 40px rgba(0,0,0,.6);display:none}' +
    '#hpill-pop b{color:#e6edf3}#hpill-pop .it{color:#ff9b9b;margin-top:6px}' +
    '#hpill-pop .mut{color:#8b949e;font-weight:400}';

  function mount() {
    if (document.getElementById('hpill')) return true;
    var host = document.querySelector('#gnav .gn-right') ||
               document.querySelector('.hdr-right') ||
               document.querySelector('.hdr');
    if (!host) return false;
    var st = document.createElement('style'); st.textContent = CSS;
    document.head.appendChild(st);
    var el = document.createElement('span');
    el.id = 'hpill'; el.className = 'meh'; el.textContent = '⚪ checking…';
    host.insertBefore(el, host.firstChild);
    var pop = document.createElement('div'); pop.id = 'hpill-pop';
    document.body.appendChild(pop);
    el.addEventListener('click', function (e) {
      e.stopPropagation();
      if (pop.style.display === 'block') { pop.style.display = 'none'; return; }
      var r = el.getBoundingClientRect();
      pop.style.display = 'block';
      pop.style.top = (r.bottom + 8) + 'px';
      pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - pop.offsetWidth - 8)) + 'px';
    });
    document.addEventListener('click', function () { pop.style.display = 'none'; });
    return true;
  }

  function render(d) {
    var el = document.getElementById('hpill'), pop = document.getElementById('hpill-pop');
    if (!el) return;
    var s = (d && d.state) || 'unknown';
    var when = d && d.ts ? '<span class="mut">last checked ' + d.ts + '</span>' : '';
    if (s === 'ok') {
      el.className = 'ok'; el.textContent = '✅ Zerodha match';
      el.title = 'App aur Zerodha ki positions match karti hain';
      pop.innerHTML = '<b>✅ App aur Zerodha match karte hain</b><br>' +
        '<span class="mut">Har position jo broker ke paas hai, app me hai — aur ulta bhi. ' +
        'Aapko kuch karne ki zaroorat nahi.</span><br>' + when;
    } else if (s === 'mismatch') {
      el.className = 'bad'; el.textContent = '🔴 Zerodha mismatch (' + (d.red || 0) + ')';
      el.title = 'App aur Zerodha alag hain — click karo';
      var items = (d.items || []).map(function (x) { return '<div class="it">• ' + x + '</div>'; }).join('');
      pop.innerHTML = '<b>🔴 App aur Zerodha alag hain</b><br>' +
        '<span class="mut">Neeche wale contracts pe app ka hisaab broker se nahi milta. ' +
        'Koi order lagane se pehle ye theek karo.</span>' + items + '<br>' + when;
    } else if (s === 'stale') {
      el.className = 'meh'; el.textContent = '⚪ check purana';
      el.title = 'Check kaafi der se nahi chala';
      pop.innerHTML = '<b>⚪ Check purana hai</b><br><span class="mut">' +
        (d.age_min ? d.age_min + ' min ' : '') + 'se app-vs-Zerodha verify nahi hua. ' +
        'Iska matlab "sab theek" NAHI hai — sirf "abhi check nahi hua".</span><br>' + when;
    } else {
      el.className = 'meh'; el.textContent = '⚪ check pending';
      pop.innerHTML = '<b>⚪ Abhi tak koi check nahi</b><br><span class="mut">' +
        'Guard ne abhi tak koi verdict nahi likha.</span>';
    }
  }

  function tick() {
    fetch('/api/health/app-vs-broker', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function () { /* keep last state — a fetch blip is not a verdict */ });
  }

  function start() {
    if (!mount()) { setTimeout(start, 400); return; }   // header may not exist yet
    tick();
    setInterval(tick, 60000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
