"""Mission numbering convention (user 2026-07-10/12) — har strategy apna number har jagah:
hub titles (runs/index.json + meta.json) AND dashboard headers (results.js meta.design).
Failures = NO number (❌ prefix); #04 ke execution-variants = 04-N/04-C/04-P sub-numbers.
Run BOTH locally and on the VPS (identical runs/ trees)."""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")

TITLES = {
    "mid_orb_nifty":        "00 - Mid-Day ORB (reference, live)",
    "long_straddle_orb":    "01 - Long Straddle @ ORB breakout",
    "debit_vertical_orb":   "02 - Debit Vertical (Bull-Call / Bear-Put) @ ORB",
    "orb_supertrend":       "03 - ORB + Supertrend",
    "chain_zone_longatm":   "04 - Auto Rev-Chain — Zone Breakout (ATM long option)",
    "chain_zone_positional":"04-P - Auto Rev-Chain POSITIONAL (monthly ATM long)",
    "chain_zone_naked":     "04-N - Auto Rev-Chain (NAKED sell variant ⚠️ rejected)",
    "chain_zone_credit":    "04-C - Auto Rev-Chain (credit-spread variant ❌ rejected)",
    "ratio_backspread":     "05 - Ratio Backspread (sell 1 ATM + buy 2 OTM) @ Mid-day ORB",
    "shortvol_ironfly":     "06 - Short-Vol Iron-Fly ❌ CORRECTED-REJECTED (TRAP #109)",
    "banknifty_hunt":       "07 - Mid-Day ORB [BANKNIFTY]",
    "pivot_continuation":   "08 - Pivot-Extreme Continuation (S3-S5/R3-R5 breakdown)",
    "gamma_scalp":          "❌ Gamma Scalping (REJECTED — VRP loser)",
    "long_strangle_orb":    "❌ Long Strangle @ ORB (failed sig p=0.07)",
}
# dashboard header prefix (design string) — sirf number lagana hai, baaki design text same
NUM = {s: t.split(" - ")[0] for s, t in TITLES.items() if not t.startswith("❌")}


def main():
    from hunt_guard import flock          # parallel hunts share index.json
    idxp = os.path.join(RUNS, "index.json")
    with flock("runs_index"):
        idx = json.load(open(idxp))
        for row in idx:
            s = row.get("slug")
            if s in TITLES:
                row["title"] = TITLES[s]
        json.dump(idx, open(idxp, "w"), indent=2)
    print("index.json titles set")

    for s, t in TITLES.items():
        mp = os.path.join(RUNS, s, "meta.json")
        if os.path.exists(mp):
            m = json.load(open(mp)); m["title"] = t
            json.dump(m, open(mp, "w"), indent=2)
        rp = os.path.join(RUNS, s, "results.js")
        if os.path.exists(rp):
            txt = open(rp, encoding="utf-8").read()
            R = json.loads(txt[txt.index("=") + 1:].rstrip(";"))
            dsg = R.get("meta", {}).get("design", "")
            num = NUM.get(s)
            if num and not dsg.startswith(num):
                # strip any older number prefix then prepend
                if dsg[:2].isdigit() and " - " in dsg[:8]:
                    dsg = dsg.split(" - ", 1)[1]
                R["meta"]["design"] = f"{num} - {dsg}"
            elif not num and not dsg.startswith("❌"):
                R["meta"]["design"] = TITLES[s].split(" (")[0] + " — " + dsg if "❌" not in dsg else dsg
            open(rp, "w", encoding="utf-8").write("window.RESULTS = " + json.dumps(R, default=float) + ";")
            print(("  %s: '%s'" % (s, R["meta"]["design"][:70])).encode("ascii", "replace").decode())


if __name__ == "__main__":
    main()
