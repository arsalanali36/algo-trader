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
