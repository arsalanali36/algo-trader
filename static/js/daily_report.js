/* daily_report.js — Daily Report page (/report). Display-only.
   Fetches /api/daily-report + /api/report-notes, renders one-scroll EOD report,
   right-click / long-press notes on any cell / stat / chart bar. */
(function () {
  "use strict";

  var DR = {};
  window.DR = DR;

  // ---- state ----
  // ptcPanes default = "index" (Only index) — single pane loads faster; persisted.
  var S = { date: null, mode: "", range: "day", data: null, notes: [],
            noteFilter: "", metricS: "amt", metricT: "amt", ptcStrat: "",
            ptcPanes: (function () { try { return localStorage.getItem("dr_ptc_panes") || "index"; } catch (e) { return "index"; } })(),
            availDates: null,   // sorted [YYYY-MM-DD] with trade data (for arrow skip-empty)
            curAnchor: "—", popColor: "b", popImgs: [], lastXY: { x: 200, y: 200 } };

  // LOCAL today (toISOString would give UTC → wrong day in IST evenings)
  function todayISO() { var d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
  // TZ-safe date arithmetic — all in UTC so no +5:30 drift (was an off-by-one bug)
  function addDays(iso, n) { var p = iso.split("-"); var dt = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])); dt.setUTCDate(dt.getUTCDate() + n); return dt.toISOString().slice(0, 10); }
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }

  // ---- number / format ----
  function inr(v) {
    if (v == null || isNaN(v)) return "—";
    var n = Math.round(v);
    return (n < 0 ? "−₹" : "₹") + Math.abs(n).toLocaleString("en-IN");
  }
  function sgn(v) { return (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }); }
  function cls(v) { return v > 0 ? "pos" : v < 0 ? "neg" : "mut"; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  // ---- range math ----
  function rangeFor() {
    var d = S.date, to = d, from = d;
    if (S.range === "week") { from = addDays(d, -6); }
    else if (S.range === "month") { from = d.slice(0, 8) + "01"; }
    return { from: from, to: to };
  }

  function subLabel() {
    var r = rangeFor();
    if (S.range === "day") {
      var dt = new Date(S.date + "T00:00");
      return dt.toLocaleDateString("en-GB", { weekday: "long", day: "2-digit", month: "short", year: "numeric" });
    }
    return S.range === "week" ? ("Week · " + r.from + " → " + r.to) : ("Month · " + r.from + " → " + r.to);
  }

  // ================= FETCH =================
  function load() {
    $("loading").style.display = "block";
    var r = rangeFor();
    var qs = "from=" + r.from + "&to=" + r.to + (S.mode ? "&mode=" + S.mode : "");
    $("subDate").textContent = subLabel();
    $("printHead").innerHTML = "<b>📆 Daily Report</b> &nbsp; <span>" + esc(subLabel()) + (S.mode ? " · " + (S.mode === "live" ? "Real" : "Paper") : "") + "</span>";
    Promise.all([
      fetch("/api/daily-report?" + qs).then(function (x) { return x.json(); }),
      fetch("/api/report-notes?date=" + S.date).then(function (x) { return x.json(); }).catch(function () { return { notes: [] }; })
    ]).then(function (res) {
      S.data = res[0] && res[0].ok ? res[0] : null;
      S.notes = (res[1] && res[1].notes) || [];
      renderAll();
      $("loading").style.display = "none";
    }).catch(function (e) {
      $("loading").style.display = "none";
      $("kpis").innerHTML = '<div class="empty">Load fail: ' + esc(e) + "</div>";
    });
  }

  // ================= RENDER =================
  function renderAll() {
    if (!S.data) { $("kpis").innerHTML = '<div class="empty">No data</div>'; ["target-wrap", "stat-wrap", "brk-strategy", "brk-trades", "chart-strategy", "chart-trade", "journey", "ptc-grid"].forEach(function (id) { $(id).innerHTML = ""; }); renderNotes(); return; }
    renderKpis(); renderTarget(); renderStat(); renderBreakdowns(); renderCharts(); renderJourney(); renderPtcStratOptions(); renderPerTradeCharts(); renderNotes(); renderHealthIv();
  }

  // ===== SYSTEM HEALTH + MANUAL INTERVENTION (merged in, Daily mode only) =====
  function renderHealthIv() {
    var daily = (S.range === "day");
    var hc = $("health-card"), ic = $("iv-card");
    if (!hc || !ic) return;
    if (!daily) { hc.style.display = "none"; ic.style.display = "none"; return; }
    hc.style.display = ""; ic.style.display = "";
    renderHealth(false); renderIntervention();
  }

  function renderHealth(replay) {
    var hc = $("health-card");
    hc.innerHTML = '<div class="hd"><h3>⚙ SYSTEM HEALTH</h3><span class="mut">positives / negatives</span>'
      + '<span class="mut" id="health-status" style="margin-left:auto">⏳ health check…</span></div>'
      + '<div class="bd" id="health-body"></div>';
    fetch("/api/daily-report/health?date=" + S.date + (replay ? "&replay=1" : ""))
      .then(function (x) { return x.json(); }).then(function (r) {
        var st = $("health-status");
        if (!r || r.ok === false) { if (st) st.innerHTML = '<span style="color:var(--red)">health load fail</span>'; return; }
        if (st) st.innerHTML = r.replay ? '<span style="color:var(--grn)">🔬 replay checked</span>'
          : '<button class="hdr-btn" onclick="DR.healthDeep()" title="Signal-replay drift (TRAP #108) bhi check karo — thoda slow">🔬 Deep check</button>';
        var bc = { green: ["#12341f30", "#1a7f37", "var(--grn)"], yellow: ["#3a2b0a30", "#7a5c12", "var(--yel)"], red: ["#3d151830", "#5c1a1f", "var(--red)"] }[r.banner_level] || ["#161b22", "var(--bd)", "var(--mut)"];
        var h = '<div style="padding:7px 12px;border-radius:8px;font-size:12px;font-weight:600;margin-bottom:11px;background:' + bc[0] + ';border:1px solid ' + bc[1] + ';color:' + bc[2] + '">' + esc(r.banner_text) + '</div>';
        function col(title, items, symcol, sym) {
          var li = (items || []).map(function (t) { return '<li style="font-size:12px;line-height:1.55;padding:3px 0 3px 18px;position:relative;color:#c9d1d9"><span style="position:absolute;left:0;color:' + symcol + '">' + sym + '</span>' + esc(t) + '</li>'; }).join("");
          return '<div><h4 style="margin:0 0 7px;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:' + symcol + '">' + title + '</h4><ul style="margin:0;padding:0;list-style:none">' + (li || '<li style="color:var(--mut);font-size:12px">—</li>') + '</ul></div>';
        }
        h += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px">'
          + col("✅ Positives", r.positives, "var(--grn)", "✓")
          + col("❌ Negatives / dhyaan do", r.negatives, "var(--red)", "✗") + '</div>';
        $("health-body").innerHTML = h;
      }).catch(function () { var st = $("health-status"); if (st) st.innerHTML = '<span style="color:var(--red)">health load fail</span>'; });
  }
  DR.healthDeep = function () { renderHealth(true); };

  function renderIntervention() {
    var ic = $("iv-card");
    ic.innerHTML = '<div class="hd"><h3>🖐 MANUAL INTERVENTION</h3><span class="mut">haath se cut na karte to kya hota</span>'
      + '<span class="mut" id="iv-status" style="margin-left:auto">⏳ …</span></div><div class="bd" id="iv-body"></div>';
    fetch("/api/intervention?date=" + S.date + (S.mode ? "&mode=" + S.mode : ""))
      .then(function (x) { return x.json(); }).then(function (R) {
        var st = $("iv-status"); if (st) st.textContent = "";
        if (!R || R.ok === false) { $("iv-body").innerHTML = '<div style="color:var(--red)">load fail</div>'; return; }
        window._drIvCuts = R.cuts || []; window._drIvDate = R.date || S.date;
        var net = R.net_impact || 0, nc = net > 0 ? "var(--grn)" : (net < 0 ? "var(--red)" : "var(--mut)");
        var box = function (l, v, vc) { return '<div style="background:var(--card2);border:1px solid var(--bd);border-radius:9px;padding:11px 13px"><div style="font-size:10px;text-transform:uppercase;color:var(--mut)">' + l + '</div><div style="font-size:20px;font-weight:700;color:' + vc + '">' + v + '</div></div>'; };
        var h = '<div style="display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:11px;margin-bottom:12px">'
          + '<div style="background:var(--card2);border:1px solid ' + (net > 0 ? '#1a7f37' : (net < 0 ? '#5c1a1f' : 'var(--bd)')) + ';border-radius:9px;padding:11px 13px"><div style="font-size:10px;text-transform:uppercase;color:var(--mut)">Intervention impact</div><div style="font-size:22px;font-weight:700;color:' + nc + '">' + inr(net) + '</div><div style="font-size:11px;color:var(--mut)">' + R.n_cut + ' cuts</div></div>'
          + box("Actual din", inr(R.day_actual), R.day_actual < 0 ? "var(--red)" : "var(--grn)")
          + box("Kuch cut na karte", inr(R.if_never_cut), "var(--yel)") + '</div>';
        h += '<div style="display:flex;gap:7px;flex-wrap:wrap;margin-bottom:10px">'
          + '<span style="padding:4px 11px;font-size:11px;border-radius:20px;background:#12341f30;border:1px solid #1a7f37;color:var(--grn)">✓ Helped ' + R.helped_n + ' (' + inr(R.helped_sum) + ')</span>'
          + '<span style="padding:4px 11px;font-size:11px;border-radius:20px;background:#3d151830;border:1px solid #5c1a1f;color:var(--red)">✗ Hurt ' + R.hurt_n + ' (' + inr(R.hurt_sum) + ')</span></div>';
        var th = function (t, a) { return '<th style="text-align:' + a + ';font-size:9px;color:var(--mut);text-transform:uppercase;padding:7px 8px;border-bottom:1px solid var(--bd2)">' + t + '</th>'; };
        h += '<div style="overflow-x:auto;-webkit-overflow-scrolling:touch"><table style="width:100%;min-width:520px;border-collapse:collapse"><thead><tr>' + th("Position · strategy", "left") + th("Aap ne cut", "right") + th("Strategy karti", "right") + th("Impact", "right") + '</tr></thead><tbody>';
        var td = function (inner, col, extra) { return '<td style="text-align:right;padding:8px;border-top:1px solid #12161d;color:' + col + ';' + (extra || '') + '">' + inner + '</td>'; };
        (R.cuts || []).forEach(function (c, ci) {
          var imp = c.impact, nod = (imp == null), icol = nod ? "var(--mut)" : (imp > 0 ? "var(--grn)" : "var(--red)");
          var chip = nod ? "—" : (imp > 0 ? "✓" : "✗");
          h += '<tr><td style="text-align:left;padding:8px;border-top:1px solid #12161d"><div>' + esc(c.symbol || c.instrument) + '<span onclick="DR.ivChart(' + ci + ')" title="Chart kholo" style="cursor:pointer;opacity:.72;margin-left:6px">📈</span></div><div style="font-size:9px;color:var(--mut)">' + esc(c.strategy_label || c.strategy) + ' · ' + esc(c.exit_reason || '') + '</div></td>'
            + td(inr(c.actual_pnl) + ' <span style="color:var(--mut);font-size:9px">' + esc(c.exit_hm) + '</span>', c.actual_pnl >= 0 ? "var(--grn)" : "var(--red)")
            + td((c.cf_pnl == null ? '<span style="color:var(--mut)">no data</span>' : inr(c.cf_pnl)) + ' <span style="color:var(--mut);font-size:9px">' + esc(c.cf_method || '') + (c.cf_exit_hm ? (' ' + esc(c.cf_exit_hm)) : '') + '</span>', "#adb6c0")
            + td(chip + ' ' + (nod ? '' : inr(imp)), icol, "font-weight:600") + '</tr>';
        });
        if (!(R.cuts || []).length) h += '<tr><td colspan="4" style="text-align:center;color:var(--mut);padding:16px">is din koi manual cut nahi</td></tr>';
        h += '</tbody></table></div>';
        $("iv-body").innerHTML = h;
      }).catch(function () { $("iv-body").innerHTML = '<div style="color:var(--red)">load fail</div>'; });
  }

  function drHmEp(date, hm) { if (!date || !hm) return null; var t = Date.parse(date + "T" + hm + ":00Z"); return isNaN(t) ? null : Math.floor(t / 1000); }
  DR.ivChart = function (ci) {
    var c = (window._drIvCuts || [])[ci]; if (!c) return;
    var date = window._drIvDate, ov = $("ivchart-ov");
    var cuts = (c.exit_legs || []).map(function (l) { return l.hm + " @" + l.price + (l.qty ? (" ×" + l.qty) : ""); }).join(", ");
    var sub = "Entry " + esc(c.entry_hm) + " @" + c.entry_price + "  ·  Tumhara cut: " + esc(cuts || "—") + "  ·  Strategy: " + esc(c.cf_method || "—") + (c.cf_exit_hm ? (" @" + esc(c.cf_exit_hm)) : "") + (c.cf_price != null ? (" (" + c.cf_price + ")") : "");
    ov.style.display = "flex";
    ov.innerHTML = '<div style="background:var(--card);border:1px solid var(--bd);border-radius:12px;width:min(940px,96vw);max-height:92vh;overflow:hidden;display:flex;flex-direction:column" onclick="event.stopPropagation()">'
      + '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;padding:12px 16px;border-bottom:1px solid var(--bd2)"><div><div style="font-weight:700">' + esc(c.instrument || c.symbol) + ' <span style="color:var(--mut);font-weight:400">· ' + esc(date) + '</span></div><div style="font-size:11px;color:var(--mut);margin-top:3px;line-height:1.5">' + sub + '</div></div><button onclick="DR.closeIvChart()" style="background:var(--card2);border:1px solid var(--bd);border-radius:6px;color:var(--tx);font-size:13px;padding:4px 9px;cursor:pointer">✕</button></div>'
      + '<div style="display:flex;gap:16px;padding:7px 16px;font-size:11px;flex-wrap:wrap;border-bottom:1px solid var(--bd2)"><span style="color:#58a6ff">▲ Entry</span><span style="color:var(--red)">✕ Tumhara cut</span><span style="color:var(--yel)">◆ Strategy exit</span></div>'
      + '<div id="drivc" style="flex:1;min-height:390px;padding:6px 10px 12px"></div></div>';
    var host = $("drivc");
    fetch("/api/intervention/chart?sec_id=" + encodeURIComponent(c.sec_id || "") + "&date=" + encodeURIComponent(date || "") + "&symbol=" + encodeURIComponent(c.symbol || "") + "&trad_sym=" + encodeURIComponent(c.instrument || ""))
      .then(function (r) { return r.json(); }).then(function (d) {
        var bars = (d && d.bars) || []; host.innerHTML = "";
        if (!bars.length) { host.innerHTML = '<div style="padding:44px;text-align:center;color:var(--mut)">is option ke premium bars capture nahi hue</div>'; return; }
        if (typeof LightweightCharts === "undefined") { host.innerHTML = '<div style="padding:44px;text-align:center;color:var(--red)">chart lib load nahi hui</div>'; return; }
        var ch = LightweightCharts.createChart(host, { width: host.clientWidth || 880, height: 390, layout: { background: { color: "transparent" }, textColor: "#8b949e" }, grid: { vertLines: { color: "#161b22" }, horzLines: { color: "#161b22" } }, timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#30363d" }, rightPriceScale: { borderColor: "#30363d" } });
        var s = ch.addCandlestickSeries({ upColor: "#3fb950", downColor: "#f85149", wickUpColor: "#3fb950", wickDownColor: "#f85149", borderVisible: false });
        s.setData(bars.map(function (b) { return { time: b.t, open: b.o, high: b.h, low: b.l, close: b.c }; }));
        var mk = [], e = drHmEp(date, c.entry_hm);
        if (e) mk.push({ time: e, position: "belowBar", color: "#58a6ff", shape: "arrowUp", text: "Entry " + c.entry_price });
        (c.exit_legs || []).forEach(function (l) { var t = drHmEp(date, l.hm); if (t) mk.push({ time: t, position: "aboveBar", color: "#f85149", shape: "arrowDown", text: "Cut " + l.price }); });
        if (c.cf_exit_ep) mk.push({ time: c.cf_exit_ep, position: "aboveBar", color: "#d29922", shape: "circle", text: "Strategy " + (c.cf_price != null ? c.cf_price : "") });
        mk.sort(function (a, b) { return a.time - b.time; }); s.setMarkers(mk);
        if (c.entry_price) s.createPriceLine({ price: c.entry_price, color: "#58a6ff", lineWidth: 1, lineStyle: 2, title: "entry" });
        if (c.cf_price != null) s.createPriceLine({ price: c.cf_price, color: "#d29922", lineWidth: 1, lineStyle: 2, title: "strat exit" });
        ch.timeScale().fitContent(); window._drIvCh = ch;
      }).catch(function () { host.innerHTML = '<div style="padding:44px;text-align:center;color:var(--red)">chart load fail</div>'; });
  };
  DR.closeIvChart = function () { var ov = $("ivchart-ov"); if (ov) ov.style.display = "none"; if (window._drIvCh) { try { window._drIvCh.remove(); } catch (e) { } } window._drIvCh = null; };

  function renderKpis() {
    var k = S.data.kpis, h = "";
    function tile(anchor, label, val, valcls, sub) {
      return '<div class="kpi" data-anchor="' + esc(anchor) + '"><div class="l">' + label + '</div><div class="v num ' + (valcls || "") + '">' + val + '</div><div class="s mut">' + (sub || "") + "</div></div>";
    }
    h += tile("KPI · Net P&L", "Net P&L", inr(k.net), cls(k.net), k.net_pct != null ? (k.net_pct >= 0 ? "▲ " : "▼ ") + Math.abs(k.net_pct) + "% cap" : "gross " + inr(k.gross));
    h += tile("KPI · Points", "Total Points", sgn(k.points), cls(k.points), (S.data.by_strategy.length) + " strat");
    h += tile("KPI · Trades", "Trades", k.trades, "", k.wins + "W · " + k.losses + "L");
    h += tile("KPI · Win%", "Win Rate", k.win_rate + "%", "", "PF " + (k.pf == null ? "—" : k.pf));
    h += tile("KPI · Charges", "Charges/Tax", inr(-k.charges), "neg", "STT+GST");
    $("kpis").innerHTML = h;
  }

  function renderTarget() {
    var rows = S.data.target;
    if (!rows.length) { $("target-wrap").innerHTML = '<div class="empty">No trades</div>'; return; }
    var tot = { lots: 0, exp: 0, real: 0, hasExp: false };
    var body = rows.map(function (r) {
      if (r.expected != null) { tot.exp += r.expected; tot.hasExp = true; }
      tot.lots += r.lots || 0; tot.real += r.real || 0;
      return "<tr><td>" + esc(r.label) + "</td><td class='num mut'>" + (r.lots || 0) + "</td><td class='num'>" + (r.expected == null ? "<span class='mut'>set</span>" : sgn(r.expected)) + "</td><td class='num " + cls(r.real) + "'>" + sgn(r.real) + "</td><td class='num " + (r.delta == null ? "mut" : cls(r.delta)) + "'>" + (r.delta == null ? "—" : sgn(r.delta)) + "</td></tr>";
    }).join("");
    var foot = "<tr class='tfoot'><td>Total</td><td class='num'>" + Math.round(tot.lots * 10) / 10 + "</td><td class='num'>" + (tot.hasExp ? sgn(tot.exp) : "—") + "</td><td class='num " + cls(tot.real) + "'>" + sgn(tot.real) + "</td><td class='num " + cls(tot.real - tot.exp) + "'>" + (tot.hasExp ? sgn(tot.real - tot.exp) : "—") + "</td></tr>";
    $("target-wrap").innerHTML = "<table><thead><tr><th>Strategy</th><th>Lots</th><th>Expected</th><th>Real</th><th>Δ Exp</th></tr></thead><tbody>" + body + "</tbody><tfoot>" + foot + "</tfoot></table>";
  }

  function renderStat() {
    var s = S.data.stat;
    var pairs = [
      ["Avg Win", inr(s.avg_win), "pos"], ["Win / Loss ratio", s.wl_ratio == null ? "—" : s.wl_ratio, ""],
      ["Avg Loss", inr(s.avg_loss), "neg"], ["Win %", s.win_pct + "%", ""],
      ["Max Run-up", inr(s.max_runup), "pos"], ["Max Drawdown", inr(s.max_drawdown), "neg"],
      ["Tax / Charges", inr(-s.tax), "neg"], ["Total Points", sgn(s.total_points), cls(s.total_points)],
      ["Total Trades", s.total_trades, ""]
    ];
    $("stat-wrap").innerHTML = pairs.map(function (p) {
      return "<div class='stat' data-anchor='Stat · " + esc(p[0]) + "'><span class='k'>" + p[0] + "</span><span class='v " + p[2] + "'>" + p[1] + "</span></div>";
    }).join("");
  }

  function renderBreakdowns() {
    var bs = S.data.by_strategy;
    if (!bs.length) { $("brk-strategy").innerHTML = '<div class="empty">No trades</div>'; }
    else {
      var body = bs.map(function (r) {
        var wl = "<span class='badge " + (r.wins >= r.losses ? "b-w" : "b-l") + "'>" + r.wins + "W/" + r.losses + "L</span>";
        return "<tr><td>" + esc(r.label) + "</td><td class='num " + cls(r.points) + "'>" + sgn(r.points) + "</td><td class='num " + cls(r.net) + "'>" + sgn(r.net) + "</td><td class='num mut'>" + sgn(-r.tax) + "</td><td>" + wl + "</td></tr>";
      }).join("");
      var k = S.data.kpis;
      var foot = "<tr class='tfoot'><td>Total</td><td class='num " + cls(k.points) + "'>" + sgn(k.points) + "</td><td class='num " + cls(k.net) + "'>" + sgn(k.net) + "</td><td class='num neg'>" + sgn(-k.charges) + "</td><td><span class='badge b-w'>" + k.wins + "/" + k.losses + "</span></td></tr>";
      $("brk-strategy").innerHTML = "<table><thead><tr><th>Strategy</th><th>Point</th><th>Amt</th><th>Tax</th><th>W/L</th></tr></thead><tbody>" + body + "</tbody><tfoot>" + foot + "</tfoot></table>";
    }
    var tr = S.data.trades;
    if (!tr.length) { $("brk-trades").innerHTML = '<div class="empty">No trades</div>'; return; }
    var tbody = tr.map(function (t) {
      var wl = "<span class='badge " + (t.wl === "W" ? "b-w" : "b-l") + "'>" + t.wl + "</span>";
      return "<tr data-tid='" + t.id + "' data-sym='" + esc(t.sym) + "'><td>T" + t.n + " · " + esc(t.label.replace(/^\d+\.\d+\s*-\s*/, "")) + "</td><td class='num " + cls(t.points) + "'>" + sgn(t.points) + "</td><td class='num " + cls(t.net) + "'>" + sgn(t.net) + "</td><td class='num mut'>" + sgn(-t.tax) + "</td><td>" + wl + "</td><td class='num mut'>" + t.dur + "</td></tr>";
    }).join("");
    $("brk-trades").innerHTML = "<table><thead><tr><th>Trade</th><th>Point</th><th>Amt</th><th>Tax</th><th>W/L</th><th>Dur</th></tr></thead><tbody>" + tbody + "</tbody></table>";
    // row click → load per-trade chart panel (embeds existing /trade-chart)
    $("brk-trades").querySelectorAll("tr[data-tid]").forEach(function (row) {
      row.addEventListener("click", function (e) {
        if (e.button === 2) return;
        var tid = +row.getAttribute("data-tid");
        var t = (S.data.trades || []).filter(function (x) { return x.id === tid; })[0];
        if (t) DR.showTradeChart(t);
      });
    });
  }

  // ---- per-trade charts grid (all trades, auto lazy-loaded on scroll) ----
  function tcUrl(g) {
    // g = array of trades on the SAME contract (same-symbol trades merged into one
    // chart). Entry/exit times joined as comma-lists → server returns all markers.
    var t0 = g[0];
    var d = t0.exit_date || t0.entry_date || S.date;
    var ets = g.map(function (t) { return t.entry_time || ""; }).filter(Boolean).join(",");
    var xts = g.map(function (t) { return t.exit_time || ""; }).filter(Boolean).join(",");
    return "/trade-chart?sym=" + encodeURIComponent(t0.sym) + "&side=" + (t0.side || "") +
      "&entry=" + (t0.entry_price || 0) + "&exit=" + (t0.exit_price || 0) +
      "&et=" + encodeURIComponent(ets) + "&xt=" + encodeURIComponent(xts) +
      "&qty=" + (t0.qty || 0) + "&date=" + d + "&auto=0&embed=1" +
      ((S.ptcPanes && S.ptcPanes !== "both") ? ("&panes=" + S.ptcPanes) : "") +
      (t0.strategy ? "&strategy=" + encodeURIComponent(t0.strategy) : "");
  }
  var _io = null;
  function loadCard(ph) {
    var url = ph.getAttribute("data-src"); if (!url) return;
    var f = document.createElement("iframe"); f.src = url; f.loading = "lazy";
    ph.replaceWith(f);
  }
  function observeCharts() {
    if (_io) _io.disconnect();
    if (!("IntersectionObserver" in window)) { $("ptc-grid").querySelectorAll(".ph[data-src]").forEach(loadCard); return; }
    _io = new IntersectionObserver(function (ents) {
      ents.forEach(function (e) { if (e.isIntersecting) { loadCard(e.target); _io.unobserve(e.target); } });
    }, { rootMargin: "150px" });
    $("ptc-grid").querySelectorAll(".ph[data-src]").forEach(function (ph) { _io.observe(ph); });
  }
  function renderPtcStratOptions() {
    var sel = $("ptc-strat"); if (!sel) return;
    var by = (S.data && S.data.by_strategy) || [];
    // reset selection if that strategy is gone from this date
    if (S.ptcStrat && !by.some(function (s) { return s.strategy === S.ptcStrat; })) S.ptcStrat = "";
    sel.innerHTML = '<option value="">All strategies</option>' + by.map(function (s) {
      return '<option value="' + esc(s.strategy) + '"' + (s.strategy === S.ptcStrat ? " selected" : "") +
        ">" + esc(s.label) + " (" + (s.wins + s.losses) + ")</option>";
    }).join("");
  }
  DR.setPtcStrat = function (v) { S.ptcStrat = v; renderPerTradeCharts(); };
  DR.setPtcPanes = function (v) { S.ptcPanes = v; try { localStorage.setItem("dr_ptc_panes", v); } catch (e) { } renderPerTradeCharts(); };

  function renderPerTradeCharts() {
    var all = ((S.data && S.data.trades) || []).filter(function (t) { return t.sym && t.sym !== "null"; });
    var tr = S.ptcStrat ? all.filter(function (t) { return t.strategy === S.ptcStrat; }) : all;
    // Group same-contract (+strategy) trades into ONE card so multiple same-symbol
    // trades don't each get a card showing the others' RSI crossunders (confusion fix).
    var groups = [], gmap = {};
    tr.forEach(function (t) {
      var k = t.sym + "|" + (t.strategy || "");
      if (!(k in gmap)) { gmap[k] = groups.length; groups.push([t]); }
      else groups[gmap[k]].push(t);
    });
    $("ptc-count").textContent = groups.length ? "(" + groups.length + " charts" + (S.ptcStrat ? " · filtered" : "") + " — scroll pe auto-load)" : "";
    if (!groups.length) { $("ptc-grid").innerHTML = '<div class="empty">Is strategy ka koi option-trade chart nahi</div>'; return; }
    $("ptc-grid").innerHTML = groups.map(function (g) {
      var t0 = g[0], url = tcUrl(g), nm = t0.label.replace(/^\d+\.\d+\s*-\s*/, "");
      var multi = g.length > 1;
      var net = g.reduce(function (a, t) { return a + (t.net || 0); }, 0);
      var tlabel = "T" + g.map(function (t) { return t.n; }).join(multi ? "," : "");
      var wl = multi ? (g.filter(function (t) { return (t.net || 0) >= 0; }).length + "W/" +
                        g.filter(function (t) { return (t.net || 0) < 0; }).length + "L") : t0.wl;
      return '<div class="ptc-card" id="ptc-' + t0.id + '"><div class="h">' +
        '<div class="hmeta"><div class="hl1" title="' + esc(nm + " " + t0.sym) + '">🕯 ' + tlabel + " · " + esc(nm) + " · " + esc(t0.sym) + (multi ? (' · ' + g.length + ' trades') : '') + '</div>' +
        '<div class="hl2 ' + cls(net) + '">' + sgn(net) + " (" + wl + ')</div></div>' +
        '<span class="o" title="Full tab" onclick="window.open(\'' + url + '\',\'_blank\')">⤢</span></div>' +
        '<div class="ph" data-src="' + esc(url) + '">⏳ chart load ho raha…</div></div>';
    }).join("");
    observeCharts();
    // eager-load only the FIRST so the feature is visible; rest lazy on scroll
    // (loading all/many at once caused ERR_CONNECTION_RESET on slow mobile networks)
    var phs = $("ptc-grid").querySelectorAll(".ph[data-src]");
    for (var k = 0; k < Math.min(1, phs.length); k++) loadCard(phs[k]);
  }
  DR.loadAllCharts = function () {
    $("ptc-grid").querySelectorAll(".ph[data-src]").forEach(loadCard);
  };
  // row-click → scroll to that trade's chart card (+ force-load it)
  DR.showTradeChart = function (t) {
    var card = $("ptc-" + t.id); if (!card) return;
    var ph = card.querySelector(".ph[data-src]"); if (ph) loadCard(ph);
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // ---- SVG bar chart ----
  function barChart(rows, metric, labelFn) {
    if (!rows.length) return '<div class="empty">No data</div>';
    var W = 420, H = 180, pad = 26, n = rows.length;
    var vals = rows.map(function (r) { return r[metric] || 0; });
    var mx = Math.max(1, Math.max.apply(null, vals.map(Math.abs)));
    var bw = Math.min(46, (W - 20) / n - 8);
    var gap = (W - 20 - bw * n) / (n + 1);
    var zeroY = H - pad - (H - 2 * pad) * (0 - (-mx)) / (2 * mx); // baseline for +/-
    var mid = H / 2;
    var svg = '<svg viewBox="0 0 ' + W + " " + H + '">';
    svg += '<line x1="6" y1="' + mid + '" x2="' + (W - 6) + '" y2="' + mid + '" stroke="#30363d"/>';
    rows.forEach(function (r, i) {
      var v = r[metric] || 0, x = 10 + gap + i * (bw + gap);
      var hgt = Math.abs(v) / mx * (mid - 14);
      var y = v >= 0 ? mid - hgt : mid;
      var color = v >= 0 ? "#3fb950" : "#f85149";
      svg += '<rect class="bar" data-anchor="' + esc("Chart · " + labelFn(r, i)) + '" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + Math.max(1, hgt).toFixed(1) + '" rx="3" fill="' + color + '"><title>' + esc(labelFn(r, i)) + ": " + sgn(v) + "</title></rect>";
      var lbl = (r.label || labelFn(r, i)).replace(/^\d+\.\d+\s*-\s*/, "").slice(0, 8);
      svg += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - 6) + '" fill="#8b949e" font-size="8" text-anchor="middle">' + esc(lbl) + "</text>";
    });
    svg += "</svg>";
    return svg;
  }

  function renderCharts() {
    $("chart-strategy").innerHTML = barChart(S.data.chart_by_strategy, S.metricS, function (r) { return r.label; });
    $("chart-trade").innerHTML = barChart(S.data.chart_by_trade, S.metricT, function (r, i) { return (r.label || ("T" + (i + 1))) + (r.sym ? " " + r.sym : ""); });
  }

  function renderJourney() {
    // equity curve = REALIZED P&L timeline → order trades by EXIT (not entry).
    // copy so other renders keep the backend's entry-order; fall back to entry
    // date/time when a trade has no exit stamp.
    var tr = (((S.data && S.data.trades) || []).slice()).sort(function (a, b) {
      var ka = (a.exit_date || a.entry_date || "") + " " + (a.exit_time || a.entry_time || "");
      var kb = (b.exit_date || b.entry_date || "") + " " + (b.exit_time || b.entry_time || "");
      return ka < kb ? -1 : ka > kb ? 1 : 0;
    });
    if (!tr.length) { $("journey").innerHTML = '<div class="empty">No trades</div>'; return; }
    var W = 900, H = 170, pad = 10, cum = 0;
    var series = [0]; tr.forEach(function (t) { cum += t.net; series.push(cum); });
    var mn = Math.min.apply(null, series), mx = Math.max.apply(null, series), rng = (mx - mn) || 1;
    var pts = series.map(function (v, i) {
      return [i / (series.length - 1) * W, H - pad - (v - mn) / rng * (H - 2 * pad)];
    });
    var line = pts.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
    var zeroY = (H - pad - (0 - mn) / rng * (H - 2 * pad)).toFixed(1);
    var last = pts[pts.length - 1], end = cum >= 0 ? "#3fb950" : "#f85149";
    var svg = '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" style="height:150px;display:block">' +
      '<defs><linearGradient id="jg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="' + end + '55"/><stop offset="1" stop-color="' + end + '00"/></linearGradient></defs>' +
      '<line x1="0" y1="' + zeroY + '" x2="' + W + '" y2="' + zeroY + '" stroke="#30363d" stroke-dasharray="4 4"/>' +
      '<path d="' + line + " L" + W + " " + zeroY + " L0 " + zeroY + ' Z" fill="url(#jg)"/>' +
      '<path d="' + line + '" fill="none" stroke="' + end + '" stroke-width="2"/>' +
      '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="4" fill="' + end + '"/>' +
      '<line id="jvl" x1="0" y1="0" x2="0" y2="' + H + '" stroke="#8b949e" stroke-dasharray="3 3" style="display:none"/>' +
      "</svg>" +
      '<div id="jtip"></div>' +
      '<div class="mut" style="font-size:11px;margin-top:4px">Cumulative net across ' + tr.length + " trades · by exit time · end " + inr(cum) + " · hover for detail</div>";
    var box = $("journey"); box.style.position = "relative"; box.innerHTML = svg;
    var svgEl = box.querySelector("svg"), vl = $("jvl"), tip = $("jtip");
    function onMove(clientX) {
      var r = svgEl.getBoundingClientRect();
      var frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
      var idx = Math.round(frac * (series.length - 1));
      vl.setAttribute("x1", pts[idx][0]); vl.setAttribute("x2", pts[idx][0]); vl.style.display = "";
      var px = frac * r.width;
      tip.style.display = "block"; tip.style.top = "2px";
      tip.style.left = Math.min(Math.max(px - 45, 2), r.width - 96) + "px";
      if (idx === 0) { tip.innerHTML = "Start · <b>₹0</b>"; }
      else {
        var t = tr[idx - 1];
        tip.innerHTML = "<b>T" + t.n + "</b> · " + esc(t.sym || "") + "<br>cum <b class='" + cls(series[idx]) +
          "'>" + inr(series[idx]) + "</b> · this " + sgn(t.net);
      }
    }
    svgEl.addEventListener("mousemove", function (e) { onMove(e.clientX); });
    svgEl.addEventListener("touchmove", function (e) { if (e.touches[0]) onMove(e.touches[0].clientX); }, { passive: true });
    box.addEventListener("mouseleave", function () { vl.style.display = "none"; tip.style.display = "none"; });
  }

  // ================= NOTES =================
  function renderNotes() {
    var list = S.notes.filter(function (n) { return !S.noteFilter || n.color === S.noteFilter; });
    $("note-count").textContent = S.notes.length;
    if (!list.length) { $("notes-list").innerHTML = '<div class="empty">Koi note nahi. Right-click / long-press se add karo.</div>'; return; }
    $("notes-list").innerHTML = list.map(function (n) {
      var t = new Date((n.ts || 0) * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
      var imgs = (n.images || []).map(function (f) { return '<img src="/report-note-image/' + S.date + "/" + encodeURIComponent(f) + '" onclick="window.open(this.src)">'; }).join("");
      return '<div class="note ' + (n.color || "b") + '"><div class="top"><span class="anchor">' + esc(n.anchor) + '</span><span class="tm">' + t + '</span><span class="del" onclick="DR.delNote(\'' + n.id + '\')">✕</span></div><p>' + esc(n.text) + "</p>" + (imgs ? '<div class="imgs">' + imgs + "</div>" : "") + "</div>";
    }).join("");
  }

  DR.delNote = function (id) {
    fetch("/api/report-notes", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ date: S.date, id: id }) })
      .then(function (x) { return x.json(); }).then(function () { S.notes = S.notes.filter(function (n) { return n.id !== id; }); renderNotes(); });
  };

  // ---- anchor resolution (cells, stats, KPIs, chart bars) ----
  function cellAnchor(td) {
    var tr = td.closest("tr"), table = td.closest("table");
    var idx = Array.prototype.indexOf.call(tr.children, td);
    var head = table.tHead && table.tHead.rows[0].cells[idx];
    var col = head ? head.textContent.replace(/[▲▼]/g, "").trim() : ("Col" + (idx + 1));
    var rowName = tr.cells[0].textContent.trim();
    var card = td.closest(".card"), title = card ? (card.querySelector("h3") || {}).textContent || "" : "";
    title = title.replace(/^[^\w]+/, "").trim();
    return (title ? title + " · " : "") + rowName + (idx > 0 ? " · " + col : "");
  }
  function anchorFor(t) {
    if (!t || !t.closest) return null;
    var a = t.closest("[data-anchor]"); if (a) return a.getAttribute("data-anchor");
    var td = t.closest("td"); if (td && td.closest("table")) { td.classList.add("noteable"); return cellAnchor(td); }
    return null;
  }

  DR.pickCol = function (elm) { elm.parentElement.querySelectorAll("span").forEach(function (s) { s.classList.remove("sel"); }); elm.classList.add("sel"); S.popColor = elm.getAttribute("data-c"); };
  DR.hidePop = function () { $("pop").style.display = "none"; };
  DR.openNoteFromCtx = function () { $("ctx").style.display = "none"; showPop(); };

  function showPop() {
    $("popAnchor").textContent = "Note on: " + S.curAnchor;
    $("popText").value = ""; S.popImgs = []; $("popImgN").textContent = "";
    var w = Math.min(300, innerWidth * 0.92);
    $("pop").style.left = Math.min(S.lastXY.x, innerWidth - w - 8) + "px";
    $("pop").style.top = Math.min(S.lastXY.y, innerHeight - 230) + "px";
    $("pop").style.display = "block";
  }

  DR.saveNote = function () {
    var text = $("popText").value.trim();
    if (!text && !S.popImgs.length) { DR.hidePop(); return; }
    fetch("/api/report-notes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ date: S.date, anchor: S.curAnchor, text: text, color: S.popColor, images: S.popImgs }) })
      .then(function (x) { return x.json(); }).then(function (r) {
        if (r.ok && r.note) { S.notes.push(r.note); renderNotes(); }
        DR.hidePop();
      });
  };

  function uploadImgs(files) {
    var done = 0;
    Array.prototype.forEach.call(files, function (f) {
      var fd = new FormData(); fd.append("date", S.date); fd.append("image", f);
      fetch("/api/report-notes/image", { method: "POST", body: fd }).then(function (x) { return x.json(); }).then(function (r) {
        if (r.ok) S.popImgs.push(r.filename);
        done++; $("popImgN").textContent = S.popImgs.length + " attached";
      });
    });
  }

  // ================= EVENTS =================
  // Available-dates (dates that actually have trade data, respecting the current
  // mode filter) — arrows jump to the nearest such day instead of walking onto
  // empty ones. Fetched once per mode, cached in S.availDates.
  function loadAvailDates() {
    S.availDates = null;   // mark loading (arrows fall back to ±1 day until ready)
    var qs = S.mode ? ("?mode=" + S.mode) : "";
    fetch("/api/daily-report/dates" + qs).then(function (x) { return x.json(); })
      .then(function (r) { S.availDates = (r && r.dates) || []; })
      .catch(function () { S.availDates = []; });
  }
  // nearest date WITH data in direction dir (+1 fwd / -1 back) from the current
  // date; null if none exists that way. Handles landing on a data-day or a gap.
  function nextDataDate(dir) {
    var ds = S.availDates;
    if (!ds || !ds.length) return null;
    var cur = S.date;
    if (dir > 0) {
      for (var i = 0; i < ds.length; i++) { if (ds[i] > cur) return ds[i]; }
      return null;
    }
    for (var j = ds.length - 1; j >= 0; j--) { if (ds[j] < cur) return ds[j]; }
    return null;
  }

  DR.step = function (d) {
    // Day mode + dates loaded → skip empty days (jump to nearest day with data).
    // Week/Month mode (or dates not yet loaded) → plain ±1 day as before.
    if (S.range === "day" && Array.isArray(S.availDates)) {
      var nd = nextDataDate(d);
      if (nd) { S.date = nd; $("dnav").value = S.date; load(); return; }
      return;   // no more data-days that way — stay put (don't wander into empty)
    }
    S.date = addDays(S.date, d); $("dnav").value = S.date; load();
  };

  DR.openPicker = function () {
    var i = $("dnav");
    try { i.showPicker(); } catch (e) { i.focus(); i.click(); }
  };
  DR.toggleMenu = function (e) { if (e) e.stopPropagation(); $("ovmenu").classList.toggle("on"); };
  DR.closeMenu = function () { $("ovmenu").classList.remove("on"); };

  // ---- settings modal (Expected targets + capital) ----
  DR.openSettings = function () {
    fetch("/api/report-settings").then(function (x) { return x.json(); }).then(function (s) {
      $("set-capital").value = s.capital == null ? "" : s.capital;
      var seen = {};
      (S.data ? S.data.by_strategy : []).forEach(function (bs) { seen[bs.strategy] = bs.label; });
      Object.keys(s.targets || {}).forEach(function (k) { if (!(k in seen)) seen[k] = k; });
      var keys = Object.keys(seen);
      $("set-targets").innerHTML = keys.length ? keys.map(function (k) {
        var v = (s.targets && s.targets[k] != null) ? s.targets[k] : "";
        return '<div class="trow"><label>' + esc(seen[k]) + '</label><input type="number" data-strat="' + esc(k) + '" placeholder="set" value="' + v + '"></div>';
      }).join("") : '<div class="mut" style="font-size:11px">Koi strategy nahi — pehle date load karo.</div>';
      $("settingsBg").classList.add("on");
    });
  };
  DR.closeSettings = function () { $("settingsBg").classList.remove("on"); };
  DR.saveSettings = function () {
    var cap = $("set-capital").value;
    var targets = {};
    $("set-targets").querySelectorAll("input[data-strat]").forEach(function (i) {
      if (i.value !== "") targets[i.getAttribute("data-strat")] = parseFloat(i.value);
    });
    fetch("/api/report-settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ capital: cap === "" ? "" : parseFloat(cap), targets: targets }) })
      .then(function (x) { return x.json(); }).then(function () { DR.closeSettings(); load(); });
  };

  function initSeg(id, key, cb) {
    $(id).addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      $(id).querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on"); S[key] = b.getAttribute("data-v"); cb();
    });
  }

  function init() {
    S.date = todayISO();
    $("dnav").value = S.date;
    // reflect the persisted "Only index" (default) panes choice in the selector
    var _pp = $("ptc-panes"); if (_pp) _pp.value = S.ptcPanes;
    $("dnav").addEventListener("change", function () { S.date = $("dnav").value; load(); });
    // mode filter changes which days have data → refresh the skip-empty date list
    initSeg("segMode", "mode", function () { loadAvailDates(); load(); });
    initSeg("segRange", "range", load);
    initSeg("segMetricS", "metricS", renderCharts);
    initSeg("segMetricT", "metricT", renderCharts);
    $("note-filter").addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      $("note-filter").querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on"); S.noteFilter = b.getAttribute("data-c"); renderNotes();
    });
    $("popImg").addEventListener("change", function () { uploadImgs(this.files); });

    document.addEventListener("keydown", function (e) {
      if (e.target.tagName === "TEXTAREA") return;
      // allow arrows even when the date input is focused (step instead of native segment edit)
      var typingText = e.target.tagName === "INPUT" && e.target.type !== "date";
      if (typingText) return;
      if (e.key === "ArrowLeft") { e.preventDefault(); DR.step(-1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); DR.step(1); }
      else if (e.key === "d" || e.key === "D") { e.preventDefault(); DR.openPicker(); }
    });

    // desktop right-click
    document.addEventListener("contextmenu", function (e) {
      var an = anchorFor(e.target); if (!an) return;
      e.preventDefault(); S.curAnchor = an; S.lastXY = { x: e.clientX, y: e.clientY };
      $("ctxAn").textContent = an;
      $("ctx").style.left = Math.min(e.clientX, innerWidth - 190) + "px";
      $("ctx").style.top = Math.min(e.clientY, innerHeight - 130) + "px";
      $("ctx").style.display = "block";
    });
    // mobile long-press
    var lp;
    document.addEventListener("touchstart", function (e) {
      var an = anchorFor(e.target); if (!an) return;
      var tp = e.touches[0];
      lp = setTimeout(function () { S.curAnchor = an; S.lastXY = { x: tp.clientX, y: tp.clientY }; showPop(); }, 480);
    }, { passive: true });
    ["touchend", "touchmove", "touchcancel"].forEach(function (ev) { document.addEventListener(ev, function () { clearTimeout(lp); }, { passive: true }); });
    document.addEventListener("click", function (e) {
      if (!$("ctx").contains(e.target)) $("ctx").style.display = "none";
      var m = $("ovmenu");
      if (m && !m.contains(e.target) && !(e.target.closest && e.target.closest(".mobbtn"))) m.classList.remove("on");
    });

    loadAvailDates();   // date arrows skip empty days once this resolves
    load();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
