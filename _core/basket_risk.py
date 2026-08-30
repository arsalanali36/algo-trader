"""basket_risk.py — basket SL/target ka SINGLE resolver: size ke saath scale, aur
strategy ke apne validated exit se BAHAR rahe.

WHY (2026-08-30, user ne pakda)
-------------------------------
Do knob alag-alag set ho rahe the:

    position SIZE  = lots            (Goal Planner / config badalta hai)
    risk CAP       = ₹4,000 ABSOLUTE (kabhi kisi ne dobara chhua hi nahi)

Par asli risk = f(size, stop-distance). ₹ ko fix rakho aur lots badhao, to
**stop-distance apne aap sikudta hai**. Kisi ne SL nahi badla, phir bhi SL tight
ho gaya.

Live measurement (isi liye ye file bani):

    bnf_strangle_hedged — apna VALIDATED exit = 50 points
                          ₹4,000 @ 11 lots × 35 = **10.4 points**

Yaani 50-point ke liye tayyar strategy 10 point pe kat rahi thi. User ke shabdon
me: *"30pt ke liye tayyar hue, fir zyada lot me 2-4pt ka scalping karne lag gaye."*
Aur ye sirf theory nahi thi — live `straddle_alert_hedged` ke **23% legs**
GROUP_SL (₹4k cap) se marte the, jabki uska PAPER twin apne hi STRADDLE_SL/TARGET
pe exit karta hai. Do alag strategy ban chuki thin, ek hi naam ke neeche.

DO NIYAM (yahi poora design hai)
--------------------------------
**1. Cap PER-LOT ho, absolute nahi.** Tab lots badhne pe stop-distance wahi
   rehti hai jo backtest me thi. `basket_sl_per_lot_rs` diya ho to wahi chalega;
   na diya ho to purana absolute `basket_sl_rs` (= aaj ka behaviour, bit-exact).

**2. Cap strategy ke apne exit se BAHAR ho — backstop, primary exit nahi.**
   Agar ₹ cap strategy ke validated exit se PEHLE lag jaata hai, to ab **wahi ₹
   cap hi strategy hai** aur backtest ka number fiction ban chuka (Rule 10).
   Aisa mile to hum chup nahi rehte — `verdict="conflict"` + loud alert, poore
   ganit ke saath (kitne lots us budget me sach me fit hote hain).

TEENO ME SE DO HI MIL SAKTE HAIN
--------------------------------
    {N lots} · {validated stop-distance} · {fixed ₹ risk}
Teeno ek saath maangna hi wo galti thi. Ye module ganit saamne rakh deta hai,
faisla user ka.

READ-ONLY decision helper — koi order, koi config write nahi.
"""

import time

# alert dobara-dobara na bhejein (per strategy, per din ek baar kaafi)
_WARNED = {}
_WARN_TTL = 6 * 3600


def _f(v, d=0.0):
    try:
        if v is None or v == "":
            return d
        return float(v)
    except (TypeError, ValueError):
        return d


def resolve(strategy_id, cfg, lots=None, lot_size=None, own_exit_pt=None,
            log=print, notify_on_conflict=True):
    """Basket target/SL nikaalo + coherence verdict.

    cfg          — strategy ka apna config block
    lots         — abhi kitne lots (None = cfg se `qty`/`lots`)
    lot_size     — contract lot size (points→₹ ke liye; None = check skip)
    own_exit_pt  — strategy ka apna validated exit, POINTS me (None = cfg `sl_pt`)

    Returns dict:
      target_rs, sl_rs   — ₹ (sl_rs hamesha POSITIVE magnitude)
      per_lot            — per-lot pe chal raha hai ya nahi (bool)
      sl_points          — ye cap kitne points pe lagta hai (lot_size ho to)
      own_exit_rs        — strategy ka apna exit ₹ me (pata ho to)
      verdict            — "ok" | "conflict" | "unknown"
      max_lots_for_budget— is ₹ budget me kitne lots tak validated exit bachta hai
      note               — insaan ke padhne layak ek line
    """
    cfg = cfg or {}
    if lots is None:
        lots = int(_f(cfg.get("qty") or cfg.get("lots"), 0))
    lots = max(int(lots or 0), 0)

    per_lot_sl = _f(cfg.get("basket_sl_per_lot_rs"), 0.0)
    per_lot_tg = _f(cfg.get("basket_target_per_lot_rs"), 0.0)

    if per_lot_sl > 0 and lots > 0:
        sl_rs = per_lot_sl * lots
        tgt_rs = (per_lot_tg * lots) if per_lot_tg > 0 else _f(cfg.get("basket_target_rs"), 4000.0)
        per_lot = True
    else:
        # LEGACY absolute — aaj ka behaviour bilkul waisa hi (koi silent change nahi)
        sl_rs = abs(_f(cfg.get("basket_sl_rs"), 4000.0))
        tgt_rs = _f(cfg.get("basket_target_rs"), 4000.0)
        per_lot = False

    out = {
        "target_rs": tgt_rs,
        "sl_rs": abs(sl_rs),
        "per_lot": per_lot,
        "lots": lots,
        "sl_points": None,
        "own_exit_rs": None,
        "verdict": "unknown",
        "max_lots_for_budget": None,
        "note": "",
    }

    ls = _f(lot_size, 0.0)
    if ls > 0 and lots > 0:
        out["sl_points"] = out["sl_rs"] / (ls * lots)

    # ⚠️ `sl_pt` sirf tab valid hai jab strategy sach me points-exit pe chalti ho.
    # 02.10.01 jaisi hedged strategies `exit_mode="basket_rs"` pe hain — unme
    # `sl_pt` naked PARENT ka leftover hai aur kabhi fire hota hi nahi. Use
    # "validated exit" maan lena = JHOOTHA conflict alert = guard wallpaper ban
    # jaata hai. Isliye basket_rs pe sl_pt ko haath nahi lagate.
    if own_exit_pt is None and str(cfg.get("exit_mode") or "") == "basket_rs":
        own_pt = 0.0
    else:
        own_pt = own_exit_pt if own_exit_pt is not None else cfg.get("sl_pt")
    own_pt = _f(own_pt, 0.0)
    if own_pt > 0 and ls > 0 and lots > 0:
        own_rs = own_pt * ls * lots
        out["own_exit_rs"] = own_rs
        out["max_lots_for_budget"] = int(out["sl_rs"] // (own_pt * ls)) if (own_pt * ls) else None
        if out["sl_rs"] < own_rs:
            out["verdict"] = "conflict"
            out["note"] = (
                "₹ cap strategy ke apne exit se PEHLE lag raha hai — "
                "cap ₹%.0f (= %.1f pt) vs validated exit %.0f pt (= ₹%.0f). "
                "Yaani ab ₹ cap hi asli strategy hai, backtest ka number nahi. "
                "Is budget me sirf ~%s lot tak validated exit bachta hai (abhi %d)."
                % (out["sl_rs"], out["sl_points"], own_pt, own_rs,
                   out["max_lots_for_budget"], lots)
            )
            if notify_on_conflict:
                _warn(strategy_id, out, log)
        else:
            out["verdict"] = "ok"
            out["note"] = ("₹ cap backstop hai (₹%.0f = %.1f pt), strategy ka apna "
                           "exit %.0f pt pehle lagta hai" % (out["sl_rs"], out["sl_points"], own_pt))
    elif out["sl_points"] is not None:
        out["note"] = "₹ cap ₹%.0f = %.1f pt @ %d lot" % (out["sl_rs"], out["sl_points"], lots)

    return out


def _warn(strategy_id, out, log=print):
    """Ek hi baat baar-baar nahi — par chup bhi nahi (alarm wallpaper na bane)."""
    try:
        k = "basket_conflict:" + str(strategy_id)
        now = time.time()
        if now - _WARNED.get(k, 0) < _WARN_TTL:
            return
        _WARNED[k] = now
        msg = "⚠️ %s — %s" % (strategy_id, out["note"])
        try:
            log(msg)
        except Exception:
            pass
        import notify
        notify.warn(msg, key=k, source="basket_risk")
    except Exception:
        pass       # alert fail hone se trading kabhi na ruke


# ─────────────────────────── RISK-FIRST SIZING (user ka asli design) ─────────
# User (2026-08-30): *"4 lakh me mera 5 lot 4-leg hedge ban jaata, to uska 1% mere
# liye sahi hai. Jaise-jaise scale karenge wahi 1% ka constraint rahe — lot risk se
# adjust ho, aur AUTO ho, mujhe yaad na dilana pade."*
#
# Yahi sahi shape hai, aur abhi ULTA laga hua tha:
#     ABHI :  lots FIX  →  risk float karta hai   (capital badha? risk% chup-chaap badal gaya)
#     SAHI :  risk% FIX →  lots DERIVED           (capital badha? lots khud badh gaye)
#
# Formula (koi jaadu nahi):
#     risk_budget  = capital × risk%
#     per_lot_risk = validated_stop_points × lot_size      <- backtest se aata hai
#     lots         = floor(risk_budget / per_lot_risk)
#     basket_sl    = lots × per_lot_risk                   <- budget se kabhi upar nahi
#
# Isse teen cheezein apne aap sach ho jaati hain:
#   1. stop-distance HAMESHA wahi rehti hai jo backtest me thi (per-lot fix hai)
#   2. risk% hamesha wahi rehta hai jo user ne chuna (budget fix hai)
#   3. capital/scale badle to **lots khud** adjust hote hain — yaad dilane ki
#      zaroorat nahi (yahi Goal Planner/roadmap ka poora point tha)
#
# Jo cheez ab "de dena" padti hai wo sirf EK hai: **validated stop (points)**.
# Wo backtest se aata hai, guess se nahi. Na mile to sizing REFUSE karti hai aur
# purane static `qty` pe chali jaati hai — chup-chaap koi number nahi banati.
def sizing(strategy_id, cfg, lot_size, own_exit_pt=None,
           capital_rs=None, risk_pct=None, log=None):
    """risk% ko constant maan ke LOTS nikaalo (aur usi ka matching basket SL).

    Returns dict:
      ok               — sizing ho paayi ya nahi (False = caller static qty use kare)
      lots             — derived lots
      risk_budget_rs   — capital × risk%
      per_lot_risk_rs  — stop_pt × lot_size
      sl_rs / target_rs— basket cap jo IN lots se exactly match karta hai
      stop_pt          — kaunsa validated stop use hua
      note             — ek line, insaan ke liye
    """
    cfg = cfg or {}
    cap = _f(capital_rs if capital_rs is not None else cfg.get("capital_rs"), 0.0)
    pct = _f(risk_pct if risk_pct is not None else cfg.get("risk_pct"), 0.0)
    stop_pt = _f(own_exit_pt if own_exit_pt is not None else cfg.get("sl_pt"), 0.0)
    ls = _f(lot_size, 0.0)

    out = {"ok": False, "lots": 0, "risk_budget_rs": 0.0, "per_lot_risk_rs": 0.0,
           "sl_rs": 0.0, "target_rs": 0.0, "stop_pt": stop_pt, "note": ""}

    # Per-lot risk teen shakl me aa sakta hai — strategy ka structure decide karta hai:
    #   (a) stop-based      : risk/lot = stop_points × lot_size      (e.g. 02.10.01, 50pt)
    #   (b) defined-risk     : risk/lot = wing − credit               (e.g. 02.17 iron-fly)
    #   (c) long option BUY  : risk/lot = premium × lot_size          (e.g. 04.03.02, max loss = premium)
    # (b)/(c) me koi "stop points" hota hi nahi — unke liye `risk_per_lot_rs`
    # SEEDHA do. Formula aage wahi rehta hai; sirf per-lot risk ka source badalta hai.
    explicit = _f(cfg.get("risk_per_lot_rs"), 0.0)

    need = [("capital_rs", cap), ("risk_pct", pct)]
    if explicit <= 0:
        need += [("validated stop (sl_pt) ya risk_per_lot_rs", stop_pt), ("lot_size", ls)]
    missing = [n for n, v in need if v <= 0]
    if missing:
        out["note"] = ("risk-first sizing OFF — " + ", ".join(missing) + " nahi hai; "
                       "purana static qty chalega (koi number guess nahi kiya)")
        return out

    budget = cap * pct / 100.0
    per_lot = explicit if explicit > 0 else (stop_pt * ls)
    if explicit > 0:
        out["stop_pt"] = None       # is shakl me stop-points ka matlab hi nahi
    lots = int(budget // per_lot)

    out.update(risk_budget_rs=budget, per_lot_risk_rs=per_lot, lots=lots)
    if lots < 1:
        _how = ("%.0f pt × %d" % (stop_pt, ls)) if explicit <= 0 else "defined risk/lot"
        out["note"] = ("is risk-budget me 1 lot bhi nahi aata — 1 lot ka risk ₹%.0f "
                       "(%s) hai par budget sirf ₹%.0f (%.2f%% of ₹%.0f). "
                       "Ya risk%% badhao, ya capital, ya risk/lot chhota karo."
                       % (per_lot, _how, budget, pct, cap))
        return out

    tgt_mult = _f(cfg.get("target_r_multiple"), 0.0)
    out["ok"] = True
    out["sl_rs"] = lots * per_lot
    out["target_rs"] = (out["sl_rs"] * tgt_mult) if tgt_mult > 0 else                        _f(cfg.get("basket_target_rs"), out["sl_rs"])
    _how = ("%.0f pt × %d" % (stop_pt, ls)) if explicit <= 0 else "defined risk/lot"
    out["note"] = ("risk-first: %.2f%% of ₹%.0f = ₹%.0f budget ÷ ₹%.0f/lot "
                   "(%s) = **%d lot**, risk cap ₹%.0f"
                   % (pct, cap, budget, per_lot, _how, lots, out["sl_rs"]))
    if log:
        try:
            log("[basket_risk] %s — %s" % (strategy_id, out["note"]))
        except Exception:
            pass
    return out
