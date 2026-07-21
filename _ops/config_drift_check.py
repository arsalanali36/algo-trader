"""config_drift_check.py — deployed live config == validated backtest params?

WHY (2026-07-21): orb_v1 live config was DEPLOYED wrong from day one — window cut
to 13:00 (backtest 14:00) + atr_sl/rr SWAPPED (live 2.5/1.5, backtest 1.5/2.5) — and
sat silently for ~2 weeks because nothing ever compared the live config against the
backtest winner it claims to deploy. The deploy session even logged "live==backtest
verified" but never checked the PARAMS. This guard closes that gap: it re-derives the
comparison every day so a swap/typo/drift is LOUD the same day, not months later.

Rule 10 (backtest-fidelity) enforced structurally: a strategy is trusted live BECAUSE
of its backtest number. If the live params differ from the backtested params, that
number is fiction. This flags exactly that.

READ-ONLY. No order/risk/live path — reads nifty_config.json + runs/<slug>/meta.json,
compares, reports. Alerts via notify (dashboard bell), stable per-strategy key so a
FIXED drift auto-resolves.

Maps: meta.params uses backtest names (h0/h1 = entry-window hours); the live config
uses win_start/win_end ("HH:MM"). Only VALUE mismatches on params PRESENT in BOTH sides
are flagged — backtest-only sim knobs (vrp_mult, skip_expiry, bs_off) that a live config
legitimately doesn't carry are listed as "info", never a drift failure.

Usage:
    python -X utf8 _ops/config_drift_check.py            # human report, exit=mismatch count
    python -X utf8 _ops/config_drift_check.py --json     # machine
    python -X utf8 _ops/config_drift_check.py --notify    # + write dashboard bell alert
"""
import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import _paths  # noqa: F401,E402  flat imports (_core/notify etc.)

CONFIG = os.path.join(ROOT, "nifty_config.json")
RUNS = os.path.join(ROOT, "scratch", "nifty_trend", "runs")

# config-key  ->  meta-param-key  (name differs between live config and backtest meta)
NAME_MAP = {"win_start": "h0", "win_end": "h1"}
# backtest-only simulation knobs — a live config legitimately may not carry these,
# so their absence/difference is INFO, never a drift failure.
BACKTEST_ONLY = {"vrp_mult", "skip_expiry"}


def _norm(v):
    """'11:00' -> 11 (hour), '1' -> 1, 1.0 -> 1.0 — so 1 vs 1.0 and '14:00' vs 14 match."""
    if isinstance(v, str) and ":" in v:
        try:
            return int(v.split(":")[0])
        except ValueError:
            return v
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


def _meta_params(slug):
    p = os.path.join(RUNS, slug, "meta.json")
    if not os.path.isfile(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8")).get("params") or {}
    except Exception:
        return None


def check():
    """-> list of per-strategy dicts {config_key, slug, mismatches[], info[]}."""
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path, encoding="utf-8"))
    deployed = {r["deployed"]: r["slug"] for r in idx if r.get("deployed")}

    rows = []
    for ck, slug in sorted(deployed.items()):
        o = cfg.get(ck)
        if o is None:
            continue  # deployed run whose config key isn't present — not this check's job
        mp = _meta_params(slug)
        if mp is None:
            rows.append({"config_key": ck, "slug": slug, "mismatches": [],
                         "info": ["backtest meta.params missing"]})
            continue
        mism, info = [], []
        for mk, mv in mp.items():
            # locate the config key that maps to this meta param
            cand = [mk] + [c for c, x in NAME_MAP.items() if x == mk]
            cv, ck_used = None, None
            for c in cand:
                if c in o:
                    cv, ck_used = o[c], c
                    break
            if cv is None:
                if mk not in BACKTEST_ONLY:
                    info.append(f"{mk}={mv} in backtest but not in live config")
                continue
            if _norm(cv) != _norm(mv):
                if mk in BACKTEST_ONLY:
                    info.append(f"{mk}: live={cv} bt={mv} (backtest-only knob)")
                else:
                    mism.append({"param": mk, "config_key": ck_used, "live": cv, "backtest": mv})
        rows.append({"config_key": ck, "slug": slug, "mismatches": mism, "info": info})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true", help="write dashboard bell alert on drift")
    args = ap.parse_args()

    rows = check()
    total = sum(len(r["mismatches"]) for r in rows)

    if args.json:
        print(json.dumps({"drift_count": total, "strategies": rows}, indent=2))
    else:
        for r in rows:
            if r["mismatches"]:
                print(f"⚠️  {r['config_key']} ({r['slug']}) — {len(r['mismatches'])} PARAM DRIFT:")
                for m in r["mismatches"]:
                    print(f"      {m['param']}: live={m['live']}  backtest={m['backtest']}")
            else:
                print(f"✓  {r['config_key']} ({r['slug']}) — matches backtest")
            for i in r.get("info", []):
                print(f"      · {i}")
        print(f"\n{total} param drift(s) across {len(rows)} deployed strategies")

    if args.notify:
        try:
            import notify
            for r in rows:
                key = f"config_drift:{r['config_key']}"
                if r["mismatches"]:
                    parts = ", ".join(f"{m['param']} live={m['live']}≠bt={m['backtest']}"
                                      for m in r["mismatches"])
                    notify.error(
                        f"{r['config_key']}: live config backtest se alag — {parts}. "
                        f"Deployed number ab valid nahi (Rule 10). nifty_config theek karo.",
                        key=key, source=r["config_key"])
                else:
                    notify.resolve(key)   # drift gaya -> bell "✓ fixed"
        except Exception as e:
            print(f"[notify] skipped ({e})", flush=True)

    return total


if __name__ == "__main__":
    sys.exit(min(main(), 255))
