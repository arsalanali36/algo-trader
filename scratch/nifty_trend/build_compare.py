"""Build runs/compare.json — compact all-strategies comparison data for compare.html.

Extracts from every runs/<slug>/results.js (which are ~10MB each, too heavy for the
browser to load 9x) just the numbers the compare table needs. Re-run after any new
run lands:  python build_compare.py
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")


def _metrics(R, key):
    c = R.get("combos", {}).get(key)
    if not c:
        return None
    m = c.get("metrics", {})
    return {k: m.get(k) for k in ("sharpe", "net_pct", "maxdd", "win_rate",
                                  "trades", "profit_factor", "fees", "expectancy",
                                  "avg_win", "avg_loss")}


def _daily_pnl(R):
    """date -> summed NET pnl from the bs|full trade list (deployable pass)."""
    c = R.get("combos", {}).get("bs|full") or {}
    out = {}
    for t in c.get("all_trades", []):
        d = str(t.get("exit_dt", ""))[:10]
        if d:
            out[d] = out.get(d, 0.0) + float(t.get("pnl", 0) or 0)
    return out


def _correlation(series_by_slug):
    """Pairwise Pearson corr of daily P&L (union of dates, missing day = 0 —
    'us din strategy flat thi'). Returns {slugs:[...], matrix:[[...]]}"""
    import math
    slugs = list(series_by_slug.keys())
    all_days = sorted(set().union(*[set(s) for s in series_by_slug.values()]))
    vecs = {sl: [series_by_slug[sl].get(d, 0.0) for d in all_days] for sl in slugs}

    def corr(a, b):
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        va = sum((x - ma) ** 2 for x in a); vb = sum((x - mb) ** 2 for x in b)
        if va <= 0 or vb <= 0:
            return None
        cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
        return round(cov / math.sqrt(va * vb), 3)

    mat = [[(1.0 if i == j else corr(vecs[slugs[i]], vecs[slugs[j]]))
            for j in range(len(slugs))] for i in range(len(slugs))]
    return {"slugs": slugs, "matrix": mat, "days": len(all_days)}


def main():
    idx = json.load(open(os.path.join(RUNS, "index.json")))
    out = []
    daily = {}
    for row in idx:
        slug = row.get("slug")
        rp = os.path.join(RUNS, slug, "results.js")
        if not os.path.exists(rp):
            continue
        txt = open(rp, encoding="utf-8").read()
        R = json.loads(txt[txt.index("=") + 1:].rstrip(";"))
        meta = R.get("meta", {})
        design = meta.get("design", row.get("title", slug))
        sig = (R.get("combos", {}).get("bs|full") or {}).get("significance") or {}
        rec = {
            "slug": slug,
            "title": row.get("title", design),
            "design": design,
            "tf": meta.get("tf", row.get("tf")),
            "type": "Positional" if "position" in design.lower() or "position" in slug else "Intraday",
            "window": meta.get("window"),
            "days": meta.get("days"),
            "lot_size": meta.get("lot_size"),
            "dna": meta.get("dna", row.get("params", {})),
            "exit": (meta.get("dna") or {}).get("exit", row.get("exit")),
            "p_value": sig.get("p_value", row.get("p_value")),
            "significant": sig.get("significant", row.get("significant")),
            "passes": meta.get("passes", []),
            "m": {
                "bs_full": _metrics(R, "bs|full"),
                "bs_train": _metrics(R, "bs|train"),
                "bs_oos": _metrics(R, "bs|oos"),
                "instr_full": _metrics(R, "instrument|full"),
                "instr_oos": _metrics(R, "instrument|oos"),
                "rms_full": _metrics(R, "rms|full"),
                # execution-structure variants if merged into this run
                "bs_naked_full": _metrics(R, "bs_naked|full"),
                "bs_credit_full": _metrics(R, "bs_credit|full"),
            },
            "mc": ((R.get("combos", {}).get("bs|full") or {}).get("mc") or {}).get("sharpe_dist"),
            "created": row.get("created"),
        }
        out.append(rec)
        daily[slug] = _daily_pnl(R)
    payload = {"strategies": out, "correlation": _correlation(daily)}
    json.dump(payload, open(os.path.join(RUNS, "compare.json"), "w"), indent=1)
    print(f"wrote runs/compare.json — {len(out)} strategies + corr matrix")
    for r in out:
        bf = r["m"]["bs_full"] or {}
        print(f"  {r['slug']:24s} tf={r['tf']:4s} bs_sharpe={bf.get('sharpe')}")


if __name__ == "__main__":
    main()
