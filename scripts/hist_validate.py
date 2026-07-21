#!/usr/bin/env python3
"""HISTORICAL validation of the daily-discipline framework (max6 + Rs5000 lock)
over ~1.5yr on the discretionary stock basket, with a LOCKED holdout.

Pipeline (reuses existing machinery, Rule 6B):
  1. backtest_engine._run_rsi/_run_range over the basket (native exits) -> spot trades
  2. bs_option BS ATM-option pricing (directional BUY; per-stock lot + realised-vol sigma)
     + charges.option_charges (date-aware) + slip_cost_leg  -> per-trade net Rs
  3. daily-caps overlay (max6 + Rs5000 per strategy/day)
  4. HONEST split: config (max6/5000) was PICKED on 2026-06-22..07-15, so everything
     BEFORE 2026-06-22 is genuine out-of-sample. Report full + OOS + lockbox(>=2026-04-15).
  5. significance on OOS: sign-flip p (daily+trade) + day-bootstrap MC + deflated-Sharpe

FIDELITY CAVEATS (printed): equity option P&L = BS approx (realised-vol sigma, ATM K=S,
per-stock lot); modeled as directional BUY (research showed these edges = BUY not sell).
Cost (charges+DOM slip) = accurate. This validates the CAPS edge OOS, not a deployable Rs.

Run on VPS (background): venv/bin/python scripts/hist_validate.py > logs/hist_validate.log 2>&1 &
"""
import sys, os, math, statistics, random, json, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/ -> project root
sys.path.insert(0, os.path.join(ROOT, 'scratch', 'nifty_trend'))
sys.path.insert(0, os.path.join(ROOT, '_TOOLS'))
sys.path.insert(0, ROOT)
import _paths  # noqa
import backtest_engine as be
import bs_option as bs
import charges as ch
import dhan_master as dm
import pandas as pd
from collections import defaultdict

STOCKS = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN']
FROM, TO = '2025-01-01', '2026-07-15'
MAXT, LOCK = 6, 5000
SEARCH_START = '2026-06-22'    # config picked on/after this -> before = clean OOS
LOCKBOX = '2026-04-15'
LOT_FALLBACK = {'RELIANCE': 500, 'TCS': 175, 'INFY': 400, 'HDFCBANK': 550,
                'ICICIBANK': 700, 'SBIN': 750}
STRATS = {
    'rsi':   (be._run_rsi,   {'rsi_period': 14, 'oversold': 30, 'overbought': 70,
                              'rsi_exit': 50, 'timeframe': '5m', 'max_trades_per_symbol': 1}),
    'range': (be._run_range, {'exit_atr': True, 'exit_main': True, 'max_trades_per_symbol': 2}),
}


def _inr(x): return f"{round(x):,}"


def _daily_close(df):
    d = df.copy()
    if 'datetime' in d.columns:
        d = d.set_index('datetime')
    d.index = pd.to_datetime(d.index)
    return d['close'].resample('1D').last().dropna()


def _lot(sym, spot):
    try:
        r = dm.get_option_contract(sym, spot, 0)
        if r and len(r) >= 3 and r[2]:
            return int(float(r[2]))
    except Exception:
        pass
    return LOT_FALLBACK.get(sym, 500)


def generate_and_price():
    """-> list of {strat, sym, date, et, net}"""
    out = []
    for strat, (runner, cfg0) in STRATS.items():
        for sym in STOCKS:
            t0 = time.time()
            try:
                cfg = dict(cfg0, symbol=sym)
                trades, df, _ = runner(FROM, TO, cfg)
            except Exception as e:
                print(f"  [{strat}/{sym}] RUN ERR {e}", flush=True); continue
            if not trades or df is None or df.empty:
                print(f"  [{strat}/{sym}] 0 trades", flush=True); continue
            dc = _daily_close(df)
            vm = bs.realised_vol_map(dc)
            vm = {str(k)[:10]: float(v) for k, v in dict(vm).items()}
            lot = _lot(sym, float(dc.iloc[-1]))
            n = 0
            for t in trades:
                try:
                    et_ts = pd.Timestamp(t['entry_time']); xt_ts = pd.Timestamp(t['exit_time'])
                    dstr = str(et_ts)[:10]
                    Se = float(t['entry_price']); Sx = float(t['exit_price'])
                    opt = 'CE' if str(t['side']).lower().startswith('l') else 'PE'
                    sig = vm.get(dstr, 0.30)
                    K = round(Se)  # ATM (K ~ S)
                    Te = max(bs.tte_years(et_ts), 1e-4); Tx = max(bs.tte_years(xt_ts), 1e-5)
                    ep = bs.bs_price(Se, K, Te, sig, opt=opt)
                    xp = bs.bs_price(Sx, K, Tx, sig, opt=opt)
                    if ep <= 0.05:
                        continue
                    gross = (xp - ep) * lot                      # directional BUY
                    cost = ch.option_charges(ep, xp, lot, 'BUY', when=et_ts) \
                        + bs.slip_cost_leg(ep, xp, lot)
                    out.append({'strat': strat, 'sym': sym, 'date': dstr,
                                'et': str(et_ts)[11:16], 'net': gross - cost})
                    n += 1
                except Exception:
                    continue
            print(f"  [{strat}/{sym}] {n} priced trades, lot={lot}  ({time.time()-t0:.0f}s)", flush=True)
    return out


def apply_caps(recs, capped):
    g = defaultdict(list)
    for r in recs:
        g[(r['strat'], r['date'])].append(r)
    day = defaultdict(float); trades = []
    for items in g.values():
        items.sort(key=lambda x: x['et'])
        cum = 0.0; cnt = 0
        for r in items:
            if capped and (cnt >= MAXT or cum >= LOCK):
                continue
            cum += r['net']; cnt += 1; day[r['date']] += r['net']; trades.append(r['net'])
    return day, trades


def _metrics(day, trades):
    dn = [day[d] for d in sorted(day)]
    if not dn:
        return None
    total = sum(dn); wins = [t for t in trades if t > 0]; loss = [t for t in trades if t <= 0]
    pf = sum(wins)/abs(sum(loss)) if loss else 99
    eq = 0.0; pk = 0.0; dd = 0.0
    for x in dn:
        eq += x; pk = max(pk, eq); dd = max(dd, pk-eq)
    return dict(total=total, ndays=len(dn), green=sum(1 for x in dn if x > 0),
                avg=total/len(dn), pf=pf, dd=dd, trades=len(trades), dn=dn, tr=trades)


def _signflip(vals, N=20000):
    if len(vals) < 3:
        return 1.0
    obs = statistics.mean(vals); ge = 0
    for _ in range(N):
        if statistics.mean([v if random.random() < 0.5 else -v for v in vals]) >= obs:
            ge += 1
    return ge/N


def _bootstrap(dn, N=20000):
    n = len(dn); c = 0
    for _ in range(N):
        if sum(dn[int(random.random()*n)] for _ in range(n)) > 0:
            c += 1
    return c/N


def _dsr(dn, n_trials):
    """deflated-Sharpe-lite: prob observed daily Sharpe > 0 given n_trials searched."""
    if len(dn) < 5:
        return 0.0
    mu = statistics.mean(dn); sd = statistics.pstdev(dn) or 1e-9
    sr = mu/sd  # per-day
    T = len(dn)
    # expected max sharpe of n_trials random strategies (Bailey/LdP approx)
    import math as m
    e = 0.5772
    z = (1-e)*_ppf(1-1.0/n_trials) + e*_ppf(1-1.0/(n_trials*m.e))
    sr_star = z / m.sqrt(T)     # threshold per-day sharpe
    # prob sr > sr_star
    se = m.sqrt((1 + 0.5*sr*sr)/(T-1))
    return _cdf((sr - sr_star)/se)


def _ppf(p):  # inverse normal (Acklam)
    a=[-3.969683028665376e+01,2.209460984245205e+02,-2.759285104469687e+02,1.383577518672690e+02,-3.066479806614716e+01,2.506628277459239e+00]
    b=[-5.447609879822406e+01,1.615858368580409e+02,-1.556989798598866e+02,6.680131188771972e+01,-1.328068155288572e+01]
    c=[-7.784894002430293e-03,-3.223964580411365e-01,-2.400758277161838e+00,-2.549732539343734e+00,4.374664141464968e+00,2.938163982698783e+00]
    d=[7.784695709041462e-03,3.224671290700398e-01,2.445134137142996e+00,3.754408661907416e+00]
    pl=0.02425
    if p<pl:
        q=math.sqrt(-2*math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p<=1-pl:
        q=p-0.5; r=q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q=math.sqrt(-2*math.log(1-p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def _cdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))


def report(recs, label, N_TRIALS):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    for capped in (False, True):
        day, trades = apply_caps(recs, capped)
        m = _metrics(day, trades)
        if not m:
            print("  (no data)"); continue
        tag = 'CAPS (max6+₹5000)' if capped else 'NO caps (all trades)'
        line = (f"  {tag:22} net {_inr(m['total']):>10} | {m['trades']:>4} trades | "
                f"avg/day {_inr(m['avg']):>7} | green {m['green']}/{m['ndays']} | "
                f"PF {m['pf']:.2f} | maxDD {_inr(m['dd'])}")
        print(line)
        if capped:
            random.seed(42)
            p_d = _signflip(m['dn']); p_t = _signflip(m['tr'])
            boot = _bootstrap(m['dn']); dsr = _dsr(m['dn'], N_TRIALS)
            sr = (statistics.mean(m['dn'])/(statistics.pstdev(m['dn']) or 1e-9))*math.sqrt(252)
            print(f"    -> significance: daily p={p_d:.3f} | trade p={p_t:.3f} | "
                  f"bootstrap {100*boot:.1f}% profitable | Sharpe(ann) {sr:.2f} | "
                  f"DSR(prob>0, {N_TRIALS} trials)={dsr:.2f} {'PASS' if dsr>=0.95 else 'FAIL'}")


def main():
    print("Fidelity: equity option P&L = BS approx (realised-vol σ, ATM K=S, per-stock lot),"
          " modeled as directional BUY. Cost (charges+DOM slip) accurate.", flush=True)
    print(f"Generating {FROM}..{TO} basket trade stream + BS pricing ...", flush=True)
    t0 = time.time()
    recs = generate_and_price()
    print(f"\nTotal priced trades: {len(recs)}  ({time.time()-t0:.0f}s)", flush=True)
    try:
        json.dump(recs, open('data/hist_validate_trades.json', 'w'))
    except Exception:
        pass
    N_TRIALS = 30   # honest multiple-testing count (configs tried today)
    report(recs, f"FULL PERIOD ({FROM}..{TO})", N_TRIALS)
    report([r for r in recs if r['date'] < SEARCH_START],
           f"OOS: BEFORE search window (<{SEARCH_START}) — config never saw this", N_TRIALS)
    report([r for r in recs if r['date'] >= LOCKBOX and r['date'] < SEARCH_START],
           f"LOCKBOX HOLDOUT ({LOCKBOX}..{SEARCH_START}) — cleanest recent OOS", N_TRIALS)
    print("\nDONE.", flush=True)


if __name__ == "__main__":
    main()
