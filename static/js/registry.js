// registry.js — frontend ka SINGLE source for strategy identity.
//
// KYUN BANA (2026-07-17):
//   Registry (`strategy_registry.json`) hamesha se sahi tha — naam usme likhe
//   the. Phir bhi app me raw config-key (ARS_CHAIN_V1, arschain_MAIN,
//   vrp_condor_v1) leak hote rahe. Ek audit me 25 jagah mili. Teen asli wajah:
//
//   1. JS ka lookup sirf `config_key` pe bana tha, jabki Python ka
//      `strategy_registry.resolve()` id + config_key + slug + aliases — CHARON
//      pe match karta hai. Yaani do resolver, alag rules. Jo bhi alias se aaya
//      (order_store ki purani rows, `ema920`, log filename) wo JS me miss ho ke
//      raw chhap jaata tha. Ab ye file wahi 4-tarfa index banati hai — parity.
//
//   2. Har page ka apna label-map. `regLabel` app-05 me kaid tha, to
//      mtm_charts.html jaisi standalone page pe wo function EXIST hi nahi karta
//      tha — wahan raw dikhna majboori thi, bug nahi. Ab ye file har us page pe
//      load hoti hai jo strategy dikhati hai.
//
//   3. Hardcoded fallback map (purana MISSION_NAME) me RETIRE ho chuke naam
//      pade the — "Range Breakout", "Ars Chain (live)". Registry fetch ek baar
//      fail ho, aur app confidently PURANA naam dikhati thi. Wo fallback yahan
//      JAAN-BOOJH KE nahi hai: registry na mile to raw key dikhega (spasht
//      "load nahi hua"), jhoota-purana naam nahi (chupa hua drift).
//
// RULE: user ki aankh ke saamne strategy dikhani ho -> regLabel(key). Bas.
//       Raw key sirf plumbing me (order_store, API query param, config key,
//       title= hover). `_TOOLS/architecture_audit.py` ka RAW-STRAT-LABEL check
//       is rule ko mechanically pakadta hai — regression yahin ruk jaata hai.
(function () {
  var REG = { families: {}, strategies: {}, _meta: {} };
  var IDX = {};            // alias(lowercased) -> {id, name}
  var HIDDEN = new Set();
  var LOADED = false;
  var _cbs = [];

  function _lc(x) { return String(x == null ? '' : x).toLowerCase(); }

  // Python `strategy_registry.resolve()` ka exact mirror: ek strategy jitne bhi
  // naamo se jaani jaati hai — permanent ID, config_key, hub slug, aur har
  // purana/retired alias — sab isi entry pe aane chahiye. Alias isliye hai ki
  // naam badalne pe order_store/RMS ki purani rows resolve hoti rahein.
  var BUCKETS = {};        // lc(bucket-id) -> clean label (non-strategy P&L rows)

  function _index() {
    IDX = {};
    var strs = REG.strategies || {};
    for (var id in strs) {
      var s = strs[id] || {};
      var rec = { id: id, name: s.name || id };
      var keys = [id, s.config_key, s.slug].concat(s.aliases || []);
      for (var i = 0; i < keys.length; i++) {
        if (keys[i]) IDX[_lc(keys[i])] = rec;
      }
    }
    var hid = ((REG._meta || {}).hidden || {}).identifiers || {};
    HIDDEN = new Set(Object.keys(hid).map(_lc));
    // Non-strategy buckets (unknown/default/manual/'') ke saaf naam — Python ka
    // bucket_labels() ka mirror. Ye P&L rakhte hain (table hide nahi kar sakta),
    // isliye raw ki jagah saaf naam. `_why` jaisi meta keys skip.
    BUCKETS = {};
    var bl = (REG._meta || {}).bucket_labels || {};
    for (var bk in bl) { if (bk.charAt(0) !== '_') BUCKETS[_lc(bk)] = bl[bk]; }
  }

  // ID + config_key + slug + aliases pe match; "id | desc" pollution ho to id
  // part pe. Mila to {id,name}, warna null.
  function _rec(key) {
    var r = IDX[_lc(key)];
    if (r) return r;
    var k = String(key == null ? '' : key);
    if (k.indexOf(' | ') >= 0) { r = IDX[_lc(k.split(' | ')[0])]; if (r) return r; }
    return null;
  }
  // Anjaana raw id -> padhne-layak Title Case (Python _beautify ka mirror) —
  // taaki raw underscore/kebab kabhi na dikhe.
  function _beautify(raw) {
    var s = String(raw == null ? '' : raw).split(' | ')[0]
      .replace(/[_\-]+/g, ' ')
      .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
      .replace(/\s+/g, ' ').trim();
    if (!s) return String(raw == null ? '' : raw);
    return s.replace(/\S+/g, function (t) { return t.charAt(0).toUpperCase() + t.slice(1); });
  }

  // Canonical display name — har surface ka SINGLE gate. Registered -> Name.
  // Non-strategy bucket -> saaf naam. Anjaana -> beautified. Raw sirf tab jab
  // registry LOAD hi na hui ho (spasht "load nahi hua", TRAP #132) — warna kabhi
  // raw ugly id leak nahi hota.
  function regLabel(key) {
    var r = _rec(key);
    if (r) return r.name;
    if (!LOADED) return String(key == null ? '' : key);   // load-fail: honest raw
    var low = _lc(String(key == null ? '' : key).trim());
    if (low in BUCKETS) return BUCKETS[low];
    if (low === '') return 'Untagged';
    return _beautify(key);
  }
  // Sirf chhota permanent ID ("04.04") — tang jagahon ke liye (row tags).
  function regId(key) {
    var r = _rec(key);
    return r ? r.id : String(key == null ? '' : key);
  }
  // "04.04 · Ars chain - DirectWebhook" — jahan dono chahiye. Unresolved pe
  // sirf saaf label (raw·raw nahi).
  function regFull(key) {
    var r = _rec(key);
    return r ? (r.id + ' · ' + r.name) : regLabel(key);
  }
  // Registry ke _meta.hidden se: mare hue config, non-strategy block (`global`),
  // aur order_store me likha kachra (`''`, `unknown`) — list/picker se bahar.
  // Ye DELETE nahi hai aur P&L se exclude bhi NAHI — har row apni jagah hai.
  function regHidden(key) { return HIDDEN.has(_lc(key)); }

  function regReady() { return LOADED; }
  // Registry aane ke baad chalna ho to yahan callback do (aa chuki ho to turant).
  function regOnLoad(cb) { if (LOADED) { try { cb(REG); } catch (e) {} } else _cbs.push(cb); }
  function regRaw() { return REG; }

  function regLoad() {
    return fetch('/api/strategy-registry', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j || !j.strategies) return;
        REG = j; _index(); LOADED = true;
        _cbs.splice(0).forEach(function (cb) { try { cb(REG); } catch (e) {} });
      })
      .catch(function () { /* raw keys dikhenge — dekhte hi pata chal jaayega */ });
  }

  window.regLabel = regLabel;
  window.regId = regId;
  window.regFull = regFull;
  window.regHidden = regHidden;
  window.regReady = regReady;
  window.regOnLoad = regOnLoad;
  window.regRaw = regRaw;
  window.regLoad = regLoad;

  regLoad();
})();
