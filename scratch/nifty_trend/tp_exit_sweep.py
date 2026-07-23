"""VRP condor: max-profit early-exit vs hold-to-expiry — tp_frac sweep.

Answers: "overnight VRP condor me max-profit pe exit karun ya expiry tak hold?"
Result (2021-2026, 250 cycles): hold-to-expiry is WORST; exit @50%-credit is best
(Sharpe -0.52 -> -0.05, DD halved, win +10pp). Direction proven; see
VRP_CONDOR_MAXPROFIT_EXIT.md for the two-condor context + caveats.

WHY the resample: the condor needs wide strikes (ATM+-3/+-8). OptChainLake/NIFTY/WEEK
(5m, the engine's default) has only 4 ATM/ATM+1 files -> _px() returns intrinsic-floor 0
for the wings -> 0 trades. OptChainLake_1m/NIFTY/WEEK HAS 42 offset files (1-min) with the
wide strikes. This resamples that 1m lake -> 5m into /tmp/lake5x and points optlake_load at it.

CAVEAT: these are UNGATED numbers (all net-negative — the IV-rank>=0.5 gate makes 02.05
profitable, but the lake's iv column is now empty so the gate can't be reproduced here).
The DIRECTION (early-exit > hold) holds gated or ungated.

Run on the VPS (where the lake lives):
    python tp_exit_sweep.py
"""
import sys, os, glob
sys.path.insert(0, ".")
sys.path.insert(0, os.path.join("scratch", "nifty_trend"))
import _paths  # noqa: F401
import pandas as pd

SRC = "_TRADING_DATA/OptChainLake_1m/NIFTY/WEEK"   # 42 wide-offset 1-min files
DST = "/tmp/lake5x/NIFTY/WEEK"                       # resampled 5m target
AGG = {"open": "first", "high": "max", "low": "min", "close": "last",
       "volume": "sum", "iv": "last", "oi": "last", "strike": "last", "spot": "last"}


def resample_1m_to_5m():
    os.makedirs(DST, exist_ok=True)
    files = glob.glob(os.path.join(SRC, "*.csv"))
    print(f"resampling {len(files)} 1m offset files -> 5m ...", flush=True)
    for p in files:
        df = pd.read_csv(p)
        if df.empty:
            continue
        df["Datetime"] = (pd.to_datetime(df["timestamp"], unit="s", utc=True)
                          .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
        df = df.drop(columns=["timestamp"]).sort_values("Datetime").drop_duplicates("Datetime")
        tt = df.Datetime.dt.time
        df = df[(tt >= pd.Timestamp("09:15").time()) & (tt <= pd.Timestamp("15:29").time())]
        if df.empty:
            continue
        s = (df.set_index("Datetime")
             .resample("5min", label="left", closed="left").agg(AGG)
             .dropna(subset=["close"]).reset_index())
        # unit-agnostic epoch-seconds (Datetime is datetime64[s]; do NOT //10**9)
        dtutc = s["Datetime"].dt.tz_localize("Asia/Kolkata").dt.tz_convert("UTC").dt.tz_localize(None)
        s["timestamp"] = dtutc.values.astype("datetime64[s]").astype("int64")
        cols = ["timestamp", "open", "high", "low", "close", "volume", "iv", "oi", "strike", "spot"]
        s[cols].to_csv(os.path.join(DST, os.path.basename(p)), index=False)
    print("resample done. offset files:", len(glob.glob(os.path.join(DST, "*.csv"))), flush=True)


def main():
    resample_1m_to_5m()

    import optlake_load as ol
    ol.LAKE = "/tmp/lake5x/NIFTY"
    import vrp_ungated_backtest as v
    import bs_option as bs
    import real_struct2 as r2
    r2._G_CACHE.clear()
    lot = bs.get_nifty_lot() or 65
    bs.SLIP_ENABLED = True
    bs.SLIP_MULT = 1.0
    g = r2.grid("WEEK", "5m")
    ivr = ol.iv_rank_daily("WEEK", "5m", v.IV_LOOKBACK)  # empty on current lake (iv col blank)
    print(f"lake {min(g['DAY'])}->{max(g['DAY'])} bars {len(g['DT'])} lot {lot} "
          f"| ivr-days {len(ivr)} (0 = IV gate unavailable, ungated only)", flush=True)
    print("=== iron_condor (body+-3, wing+-5), UNGATED. tp_frac=exit at X% of credit; None=hold to expiry ===")
    print(f"{'tp_frac':>8} {'n':>4} {'PF':>6} {'Sharpe':>7} {'net%':>8} {'DD%':>7} {'win%':>5} {'holdD':>7} {'p':>7}")
    for tp in [None, 0.5, 0.7, 0.85, 0.95, 1.0]:
        tr = v.backtest(g, ivr, lot, mode="cycle_start", struct="iron_condor",
                        wing=5, short_off=3, tp_frac=tp, sl_frac=None, iv_min=0.0)
        m = v.metrics(tr)
        sig = v.significance(tr)
        hold = sum(t.get("held_days", 0) for t in tr) / len(tr) if tr else 0
        pv = sig.get("p_value") if isinstance(sig, dict) else None
        print(f"{str(tp):>8} {m['n']:>4} {m['pf']:>6.2f} {m['sharpe']:>7.2f} "
              f"{m['net_pct']:>8.1f} {m['maxdd']:>7.1f} {m['win_rate']:>5.0f} "
              f"{hold:>7.1f} {str(pv):>7}", flush=True)


if __name__ == "__main__":
    main()
