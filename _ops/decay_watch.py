"""decay_watch.py - "strategy chup-chaap marr to nahi gayi?" ka alarm.

DISPLAY/ALERT ONLY. Koi order/risk path nahi chhuta - sirf order_store padhta hai,
backtest run padhta hai, aur notify karta hai.

## Problem jo ye hal karta hai

Live me bura daur aur mari hui edge **bilkul ek jaise dikhte hain**. 2023 me 04.03.02 ne
Rs11,457 khoye - wo normal tha (backtest me bhi aisa saal hai). Par agar edge sach me
marr jaye, wo bhi *exactly* aisa hi dikhega. Aankh se farq nahi kar sakte.

## Jawab: sample-size-aware bootstrap

Backtest ki apni per-trade distribution se **N trades ka sample** baar-baar nikalo
(N = live trades ki ginti). Isse pata chalta hai: "agar edge zinda hoti, to N trades me
sabse bura kaisa dikh sakta tha?" Live us range ke andar hai to bura daur; neeche hai to
edge sach me badal gayi.

Ye khud-ba-khud sample-size adjust karta hai - 10 trades pe bahut bada gap chahiye
(kyunki 10 trades me kuch bhi ho sakta hai), 200 trades pe chhota gap kaafi hai.

## Do check (dono per-UNIT pe, charges ke baad)

  1. EXPECTANCY   live mean vs bootstrap distribution ka 5th percentile
  2. DRAWDOWN     live peak-to-trough vs run ka apna MC worst-5% DD
                  (bad-luck ordering pe bhi jitna hona chahiye tha, usse zyada gira?)

## Jaan-boojh ke jo NAHI karta

  * MIN_TRADES se kam pe **kabhi fire nahi karta** - "pata nahi" bolta hai. Jhootha
    alarm channel ko wallpaper bana deta hai (TRAP #192 ka sabak).
  * Paper aur live ko alag nahi karta - paper hi to forward-test hai; mode report me
    dikhta hai.
  * Backtest ke bina strategy pe kuch nahi bolta (compare karne ko kuch hai hi nahi).
  * Kabhi koi strategy band nahi karta - sirf BATATA hai. Band karna insaan ka faisla.

Usage:
    python _ops/decay_watch.py              # sab strategies, report
    python _ops/decay_watch.py --notify     # + bell/telegram alert
    python _ops/decay_watch.py --strategy chainzone_v1
    python _ops/decay_watch.py --window 90  # sirf pichhle 90 din ki live trades
"""
import argparse
import datetime as dt
import io
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import _paths  # noqa: F401
import order_store
import strategy_registry as sreg

try:
    import notify
except Exception:
    notify = None

RUNS = os.path.join(ROOT, "scratch", "nifty_trend", "runs")

# ── thresholds (jaan-boojh ke conservative) ────────────────────────────────
MIN_TRADES = 30        # isse kam pe kabhi verdict nahi
P_DEAD = 0.05          # p < 0.05  -> edge badal gayi (RED)
P_WATCH = 0.20         # p < 0.20  -> nazar rakho (AMBER)
BOOT_ITERS = 20000
LOOKBACK_DAYS = 400    # order_store range (positional netting, TRAP #141)

_RID_CACHE = {}


def _resolved_id(key):
    """Kisi bhi strategy string ko uski canonical registry id pe le jao (alias/case/slug
    sab). Resolve na ho to lowercased raw - taaki do unresolved keys galti se merge na hon."""
    k = str(key or "").strip()
    if not k:
        return ""
    if k in _RID_CACHE:
        return _RID_CACHE[k]
    rid = k.lower()
    try:
        e = sreg.resolve(k)
        if isinstance(e, dict) and e.get("id"):
            rid = str(e["id"])
        elif isinstance(e, str) and e:
            rid = e
    except Exception:
        pass
    _RID_CACHE[k] = rid
    return rid


# NOTE (why per-UNIT, not per-lot):
# Pehla version har trade ko lot_size se divide karta tha. dhan_master ka lot-size lookup
# EXPIRED contracts pe kachra deta hai - straddle_v1 ki har trade qty=65 thi par resolve
# hue "lots" 0.02 se 2.6 tak the, jisse per-lot numbers 26x inflate ho gaye (raw gross
# -Rs10,940 -> mera -Rs2,82,510). Bilkul TRAP #197 wali shakl, aur wo bhi usi tool me jo
# aise bug pakadne ko bana hai.
# `qty` dono taraf (backtest all_trades aur order_store) EXACT record hoti hai, isliye
# per-unit (pnl/qty) hi wo paimana hai jo bina kisi lookup ke sach rehta hai.


# ── backtest side ──────────────────────────────────────────────────────────
def load_backtest(slug):
    """Run ka per-UNIT net array + uska apna MC worst-5% DD (per unit). None if absent."""
    p = os.path.join(RUNS, slug, "results.js")
    if not os.path.exists(p):
        return None
    try:
        raw = io.open(p, encoding="utf-8").read().strip()
        R = json.loads(raw[len("window.RESULTS = "):].rstrip(";"))
    except Exception:
        return None
    combos = R.get("combos") or {}
    c = combos.get("bs|full") or combos.get("full")
    if not c:
        return None
    meta = R.get("meta") or {}
    nets = []
    for t in c.get("all_trades") or []:
        if t.get("pnl") is None:
            continue
        qty = float(t.get("qty") or 0)
        if qty <= 0:
            continue
        nets.append(float(t["pnl"]) / qty)          # per UNIT
    if len(nets) < 50:
        return None
    net = np.array(nets, float)
    # MC worst-5% DD ko bhi per-UNIT me laao: run ka DD% x start_cap = Rs at the run's
    # own size; usko run ke apne avg qty se divide karo.
    dd_unit = None
    try:
        mc = (c.get("mc") or {}).get("table", {}).get("maxdd")
        cap = float(meta.get("start_cap") or 0)
        qtys = [float(t.get("qty") or 0) for t in (c.get("all_trades") or [])]
        qtys = [q for q in qtys if q > 0]
        avg_qty = (sum(qtys) / len(qtys)) if qtys else 0
        if mc and len(mc) > 1 and cap and avg_qty:
            dd_unit = abs(float(mc[1])) / 100.0 * cap / avg_qty
    except Exception:
        dd_unit = None
    if not dd_unit:
        eq = np.cumsum(net)
        dd_unit = abs(float((eq - np.maximum.accumulate(eq)).min()))
    return dict(net=net, dd_per_lot=dd_unit, n=len(net))


# ── live side ──────────────────────────────────────────────────────────────
def load_live(config_key, window_days=None, _cache={}):
    """Live/paper completed trades -> per-UNIT NET array (charges ke baad), time-ordered."""
    key = "rows"
    if key not in _cache:
        today = dt.date.today()
        frm = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
        try:
            _cache[key] = order_store.trades_for_range(frm, today.isoformat())["details"]
        except Exception:
            _cache[key] = []
    rows = _cache[key]

    try:
        import charges as CH
    except Exception:
        CH = None

    cutoff = None
    if window_days:
        cutoff = (dt.date.today() - dt.timedelta(days=int(window_days))).isoformat()

    # match on RESOLVED identity, not the raw string - order_store rows carry aliases,
    # case variants and old keys (TRAP #132). resolve() knows all of them.
    want = _resolved_id(config_key)
    out = []
    for t in rows:
        if _resolved_id(t.get("strategy")) != want:
            continue
        xd = str(t.get("exit_date") or t.get("entry_date") or "")
        if cutoff and xd < cutoff:
            continue
        qty = float(t.get("qty") or 0)
        if qty <= 0:
            continue
        gross = float(t.get("pnl") or 0.0)
        fee = 0.0
        if CH is not None:
            try:
                fee = float(CH.option_charges(
                    float(t.get("entry_price") or 0), float(t.get("exit_price") or 0),
                    qty, entry_side=str(t.get("entry") or "BUY"),
                    when=t.get("entry_date")))
            except Exception:
                fee = 0.0
        out.append(dict(d=xd, net=(gross - fee) / qty, mode=t.get("mode")))   # per UNIT
    out.sort(key=lambda r: r["d"])
    return out


# ── the test ───────────────────────────────────────────────────────────────
def _bootstrap_p(bt_net, live_mean, n, iters=BOOT_ITERS, seed=11):
    """P(backtest ke N-trade sample ka mean <= live mean). Chhota = live bahut peeche."""
    rng = np.random.default_rng(seed)
    means = bt_net[rng.integers(0, len(bt_net), size=(iters, n))].mean(axis=1)
    return float((means <= live_mean).mean()), float(np.percentile(means, 5))


def assess(sid, name, config_key, slug, window_days=None):
    bt = load_backtest(slug)
    if bt is None:
        return dict(id=sid, name=name, ck=config_key, verdict="no_backtest",
                    msg="is strategy ka koi run nahi - compare karne ko kuch nahi")
    live = load_live(config_key, window_days)
    n = len(live)
    if n < MIN_TRADES:
        return dict(id=sid, name=name, ck=config_key, verdict="insufficient", n=n,
                    msg=f"sirf {n} trades - {MIN_TRADES} se kam pe koi faisla nahi")

    L = np.array([r["net"] for r in live], float)
    live_mean = float(L.mean())
    bt_mean = float(bt["net"].mean())
    p, floor5 = _bootstrap_p(bt["net"], live_mean, n)

    eq = np.cumsum(L)
    live_dd = abs(float((eq - np.maximum.accumulate(eq)).min()))
    dd_breach = live_dd > bt["dd_per_lot"]

    # DD breach aur decay DO ALAG cheezein hain. 02.07 pe live expectancy backtest se
    # BEHTAR thi (p=1.000) par DD bada tha - use "edge mar gayi" bolna jhooth hota.
    if p < P_DEAD:
        verdict = "decayed"          # kamai backtest se saaf peeche
    elif dd_breach:
        verdict = "risk"             # kamai theek, par girawat bad-luck limit se aage
    elif p < P_WATCH:
        verdict = "watch"
    else:
        verdict = "healthy"

    modes = sorted({str(r.get("mode") or "?") for r in live})
    return dict(id=sid, name=name, ck=config_key, slug=slug, verdict=verdict,
                n=n, live_mean=round(live_mean, 1), bt_mean=round(bt_mean, 1),
                floor5=round(floor5, 1), p=round(p, 4),
                live_dd=round(live_dd), bt_dd=round(bt["dd_per_lot"]),
                dd_breach=dd_breach, net=round(float(L.sum())),
                modes=",".join(modes), first=live[0]["d"], last=live[-1]["d"])


def _targets():
    """Registry se (id, name, config_key, slug) - sirf wo jinke paas dono hain."""
    reg = sreg.load() if hasattr(sreg, "load") else None
    if reg is None:
        reg = json.load(io.open(os.path.join(ROOT, "strategy_registry.json"), encoding="utf-8"))
    out = []

    def walk(d):
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            if isinstance(v, dict):
                if v.get("config_key") and v.get("slug"):
                    out.append((k, v.get("name") or k, v["config_key"], v["slug"]))
                walk(v)
    walk(reg.get("strategies") or reg)
    return sorted(set(out))


def run(window_days=None, only=None, do_notify=False):
    rows = []
    for sid, name, ck, slug in _targets():
        if only and ck != only and sid != only:
            continue
        try:
            rows.append(assess(sid, name, ck, slug, window_days))
        except Exception as e:
            rows.append(dict(id=sid, name=name, ck=ck, verdict="error", msg=str(e)[:120]))

    if do_notify and notify is not None:
        for r in rows:
            key = "decay:%s" % r.get("ck")
            if r["verdict"] == "risk":
                notify.warn("%s - %s: kamai theek hai (p=%.2f) par girawat Rs%s/unit bad-luck "
                            "limit Rs%s se aage. Size/DD budget dekho."
                            % (r["id"], r["name"], r["p"], r["live_dd"], r["bt_dd"]),
                            key=key, source="decay")
            elif r["verdict"] == "decayed":
                why = []
                if r.get("p", 1) < P_DEAD:
                    why.append("expectancy Rs%s/unit vs backtest Rs%s (p=%.3f)"
                               % (r["live_mean"], r["bt_mean"], r["p"]))
                if r.get("dd_breach"):
                    why.append("drawdown Rs%s/unit > bad-luck limit Rs%s" % (r["live_dd"], r["bt_dd"]))
                notify.error("%s - %s ke %d trades backtest se alag chal rahe hain: %s. "
                             "Review karo (system ne band NAHI kiya)."
                             % (r["id"], r["name"], r["n"], "; ".join(why)),
                             key=key, source="decay")
            elif r["verdict"] == "watch":
                notify.warn("%s - %s: %d trades me expectancy backtest se peeche "
                            "(p=%.3f). Abhi alarm nahi, nazar rakho."
                            % (r["id"], r["name"], r["n"], r["p"]),
                            key=key, source="decay")
            else:
                try:
                    notify.resolve(key)
                except Exception:
                    pass
    return rows


_ICON = {"decayed": "RED  ", "risk": "RISK ", "watch": "AMBER", "healthy": "green",
         "insufficient": "  -  ", "no_backtest": "  -  ", "error": "ERR  "}


def print_report(rows):
    print("=" * 104)
    print("STRATEGY DECAY WATCH  -  live/paper trades vs unke apne backtest ki distribution")
    print("=" * 104)
    live = [r for r in rows if r["verdict"] in ("decayed", "risk", "watch", "healthy")]
    if live:
        print("\n%-9s %-28s %6s %11s %11s %8s %10s %10s" %
              ("verdict", "strategy", "n", "live/unit", "backtest", "p", "live DD", "limit"))
        _ord = {"decayed": 0, "risk": 1, "watch": 2, "healthy": 3}
        for r in sorted(live, key=lambda x: (_ord.get(x["verdict"], 9), x["id"])):
            print("%-9s %-28s %6d %11s %11s %8.3f %10s %10s%s" %
                  (_ICON[r["verdict"]], ("%s %s" % (r["id"], r["name"]))[:28], r["n"],
                   r["live_mean"], r["bt_mean"], r["p"],
                   "%s" % r["live_dd"], "%s" % r["bt_dd"],
                   "  <- DD breach" if r.get("dd_breach") else ""))
    skipped = [r for r in rows if r["verdict"] in ("insufficient", "no_backtest", "error")]
    if skipped:
        print("\n  faisla nahi ho sakta (%d):" % len(skipped))
        for r in sorted(skipped, key=lambda x: x["id"]):
            print("    %-9s %-28s %s" % (_ICON[r["verdict"]], ("%s %s" % (r["id"], r["name"]))[:28],
                                         r.get("msg", "")))
    print("\n  p = agar edge ZINDA hoti, to itne trades me itna bura ya usse bura kitni baar aata.")
    print("  p >= 0.20 healthy  |  0.05-0.20 nazar rakho  |  < 0.05 edge badal gayi")
    print("  RISK = kamai theek par girawat backtest ki bad-luck limit se aage (size ka sawaal).")
    print("  Sab numbers PER UNIT hain (per lot nahi) - qty dono taraf exact record hoti hai.")
    print("  Ye kabhi kuch band nahi karta - sirf batata hai.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=None, help="sirf pichhle N din ki live trades")
    ap.add_argument("--strategy", default=None, help="ek hi strategy (config_key ya id)")
    ap.add_argument("--notify", action="store_true", help="bell/telegram alert bhejo")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = run(a.window, a.strategy, a.notify)
    if a.json:
        print(json.dumps(rows, indent=1, default=str))
    else:
        print_report(rows)
    if a.notify and notify is not None:
        try:
            import telegram_notify
            telegram_notify.flush()      # one-shot process - TRAP #192
        except Exception:
            pass
    return 3 if any(r["verdict"] == "decayed" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
