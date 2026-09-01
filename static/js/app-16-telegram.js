/* app-16-telegram.js — Telegram Alerts settings panel.
   Trade entry/exit ka message Telegram pe. Backend: /api/telegram/* .
   Sab fail-safe — koi bhi call fail ho to sirf msg, dashboard kabhi na tootے. */
(function () {
  'use strict';

  function _msg(txt, ok) {
    var m = document.getElementById('tg-msg');
    if (!m) return;
    m.style.display = 'block';
    m.textContent = txt;
    m.style.background = ok ? '#12261a' : '#2b1416';
    m.style.color = ok ? '#3fb950' : '#f85149';
    m.style.border = '1px solid ' + (ok ? '#238636' : '#8b2c2c');
  }
  function _clearMsg() { var m = document.getElementById('tg-msg'); if (m) m.style.display = 'none'; }

  function _renderStrategies(strats, selected) {
    var box = document.getElementById('tg-strat-list');
    if (!box) return;
    if (!strats || !strats.length) {
      box.innerHTML = '<div style="color:#8b949e;font-size:12px;">Koi active strategy nahi mili.</div>';
      return;
    }
    var sel = {};
    (selected || []).forEach(function (s) { sel[s] = true; });
    box.innerHTML = '';
    strats.forEach(function (s) {
      var lbl = document.createElement('label');
      lbl.style.cssText = 'display:flex;align-items:center;gap:8px;font-size:12px;color:#e6edf3;padding:4px 0;cursor:pointer;';
      var cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'tg-strat-cb';
      cb.value = s.id;
      cb.checked = !!sel[s.id];
      cb.style.cursor = 'pointer';
      var tag = document.createElement('span');
      var isLive = (s.mode === 'live');
      tag.textContent = isLive ? 'LIVE' : 'paper';
      tag.style.cssText = 'font-size:9px;font-weight:700;padding:1px 5px;border-radius:4px;' +
        (isLive ? 'background:#1a3d1f;color:#3fb950;' : 'background:#21262d;color:#8b949e;');
      var name = document.createElement('span');
      name.textContent = s.label || s.id;
      lbl.appendChild(cb); lbl.appendChild(tag); lbl.appendChild(name);
      box.appendChild(lbl);
    });
  }

  window.openTelegramModal = function () {
    var mo = document.getElementById('tg-modal');
    if (mo) mo.style.display = 'flex';
    _clearMsg();
    fetch('/api/telegram/config').then(function (r) { return r.json(); }).then(function (j) {
      if (!j || !j.ok) { _msg((j && j.error) || 'config load fail', false); return; }
      var c = j.config || {};
      document.getElementById('tg-enabled').checked = !!c.enabled;
      // token masked — placeholder me dikhao, field khaali (naya type karega to hi badlega)
      var tok = document.getElementById('tg-token');
      tok.value = '';
      tok.placeholder = c.has_token ? (c.bot_token + '  (saved — badalna ho to naya paste karo)') : '8123456789:AAH...';
      document.getElementById('tg-chat').value = c.chat_id || '';
      document.getElementById('tg-ev-entry').checked = c.notify_entries !== false;
      document.getElementById('tg-ev-exit').checked = c.notify_exits !== false;
      document.getElementById('tg-ev-blocked').checked = !!c.notify_blocked;
      _renderStrategies(j.strategies, c.notify_strategies);
    }).catch(function (e) { _msg('load error: ' + e, false); });
  };

  window.closeTelegramModal = function () {
    var mo = document.getElementById('tg-modal');
    if (mo) mo.style.display = 'none';
  };

  function _collect() {
    var picked = [];
    document.querySelectorAll('.tg-strat-cb').forEach(function (cb) {
      if (cb.checked) picked.push(cb.value);
    });
    var body = {
      enabled: document.getElementById('tg-enabled').checked,
      chat_id: (document.getElementById('tg-chat').value || '').trim(),
      notify_entries: document.getElementById('tg-ev-entry').checked,
      notify_exits: document.getElementById('tg-ev-exit').checked,
      notify_blocked: document.getElementById('tg-ev-blocked').checked,
      notify_strategies: picked,
      // mode filter: koi strategy chuni hai to mode-filter loose rakho (unhi ki id
      // se match hoga, chahe paper ho). Kuch NA chuna = "sab" — us case me sirf
      // LIVE, warna 25+ paper strategies phone ko wallpaper bana deti hain.
      notify_modes: picked.length ? ['live', 'paper'] : ['live']
    };
    var tok = (document.getElementById('tg-token').value || '').trim();
    if (tok) body.bot_token = tok;   // sirf naya token type kiya to bhejo
    return body;
  }

  window.tgSaveConfig = function () {
    _clearMsg();
    var btn = document.getElementById('tg-save-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
    fetch('/api/telegram/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_collect())
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
      if (j && j.ok) {
        _msg('✅ Save ho gaya.' + (j.config && j.config.ready ? '' : ' (token/chat_id abhi adhoora — Detect + token daalo)'), true);
        // token field saaf karo, placeholder update
        var tok = document.getElementById('tg-token');
        if (tok && j.config) { tok.value = ''; tok.placeholder = j.config.has_token ? (j.config.bot_token + '  (saved)') : '8123456789:AAH...'; }
      } else {
        _msg((j && j.error) || 'save fail', false);
      }
    }).catch(function (e) { if (btn) { btn.disabled = false; btn.textContent = 'Save'; } _msg('save error: ' + e, false); });
  };

  window.tgDetectChat = function () {
    _clearMsg();
    var hint = document.getElementById('tg-chat-hint');
    var btn = document.getElementById('tg-detect-btn');
    var tok = (document.getElementById('tg-token').value || '').trim();
    // agar naya token type kiya hai to pehle save karo warna backend purana use karega
    var pre = tok ? fetch('/api/telegram/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bot_token: tok })
    }).then(function (r) { return r.json(); }) : Promise.resolve(null);
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    if (hint) hint.textContent = '';
    pre.then(function () {
      return fetch('/api/telegram/detect-chat', { method: 'POST' });
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = 'Detect Chat ID'; }
      if (!j || !j.ok) { _msg((j && j.error) || 'detect fail', false); return; }
      if (!j.chats || !j.chats.length) {
        if (hint) hint.textContent = j.hint || 'Koi message nahi mila — pehle bot ko message bhejo.';
        return;
      }
      if (j.chats.length === 1) {
        document.getElementById('tg-chat').value = j.chats[0].chat_id;
        if (hint) hint.textContent = '✅ Mila: ' + j.chats[0].name + ' (' + j.chats[0].chat_id + ')';
      } else {
        if (hint) hint.innerHTML = j.chats.map(function (c) {
          return '<a href="#" onclick="document.getElementById(\'tg-chat\').value=\'' + c.chat_id + '\';return false;" style="color:#58a6ff;">' + c.chat_id + ' (' + c.name + ')</a>';
        }).join(' · ');
      }
    }).catch(function (e) { if (btn) { btn.disabled = false; btn.textContent = 'Detect Chat ID'; } _msg('detect error: ' + e, false); });
  };

  window.tgSendTest = function () {
    _clearMsg();
    var btn = document.getElementById('tg-test-btn');
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    // save-first taaki current fields (token/chat) use ho
    fetch('/api/telegram/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_collect())
    }).then(function () {
      return fetch('/api/telegram/test', { method: 'POST' });
    }).then(function (r) { return r.json(); }).then(function (j) {
      if (btn) { btn.disabled = false; btn.textContent = 'Test'; }
      if (j && j.ok) _msg('✅ Test message bhej diya — Telegram check karo.', true);
      else _msg('Test fail: ' + ((j && j.error) || '?'), false);
    }).catch(function (e) { if (btn) { btn.disabled = false; btn.textContent = 'Test'; } _msg('test error: ' + e, false); });
  };

  // click outside to close
  document.addEventListener('click', function (e) {
    var mo = document.getElementById('tg-modal');
    if (mo && mo.style.display === 'flex' && e.target === mo) window.closeTelegramModal();
  });
})();
