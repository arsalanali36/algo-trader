"""Core engine — indicators, two trend strategies, event-driven backtester,
and the full Jesse-style metric set. NIFTY spot, 1H, long+short, 3% risk/trade.

Two exit variants (video's two strategies):
  variant="trail"  -> NiftyTrendTrail : ATR trailing stop
  variant="atr"    -> NiftyTrendATR   : fixed ATR stop + ATR target

Two modes:
  mode="intraday"  -> force-exit on the day's last hourly bar, no entry on it
  mode="positional"-> hold across days until an exit condition fires

Entry (both): trend-filtered Bollinger breakout, long AND short.
  LONG  : close crosses above BB upper  AND close > trend EMA
  SHORT : close crosses below BB lower  AND close < trend EMA
Sizing : qty = (risk_pct * equity) / (initial_stop_distance_points)
"""
import numpy as np
import pandas as pd

RISK_PCT   = 0.03
START_CAP  = 1_000_000.0
LOT_POINT  = 1.0        # spot backtest: 1 unit = 1 index point of P&L per qty
BARS_PER_DAY = 7        # 09..15 hourly


# ---------------- indicators ----------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def atr(df, n=14):
    h, l, c = df.High, df.Low, df.Close
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()      # Wilder RMA

def bollinger(s, n, dev):
    ma = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    return ma + dev * sd, ma, ma - dev * sd


DEFAULTS = {
    "trail": dict(bb_period=20, bb_dev=2.0, ema_period=200, atr_period=14,
                  stop_mult=2.5, trail_atr=2.5),
    "atr":   dict(bb_period=20, bb_dev=2.0, ema_period=200, atr_period=14,
                  atr_sl=2.0, atr_tp=4.0),
}


def prep(df, p):
    d = df.copy().reset_index(drop=True)
    up, mid, lo = bollinger(d.Close, int(p["bb_period"]), p["bb_dev"])
    d["bb_up"], d["bb_lo"] = up, lo
    d["ema"] = ema(d.Close, int(p["ema_period"]))
    d["atr"] = atr(d, int(p["atr_period"]))
    d["day"] = pd.to_datetime(d.Datetime).dt.date
    d["is_last"] = d["day"] != d["day"].shift(-1)     # last bar of the day
    return d


# ---------------- backtest ----------------
def backtest(df, variant="trail", mode="intraday", params=None):
    p = dict(DEFAULTS[variant]); p.update(params or {})
    d = prep(df, p)
    n = len(d)
    warm = int(max(p["ema_period"], p["bb_period"], p["atr_period"])) + 2

    equity = START_CAP
    pos = None                      # dict: side, entry, qty, stop, target, entry_i, entry_dt
    trades = []
    eq_curve = []                   # (Datetime, equity_marked)

    C = d.Close.values; H = d.High.values; L = d.Low.values; O = d.Open.values
    UP = d.bb_up.values; LO = d.bb_lo.values; EM = d.ema.values; AT = d.atr.values
    ISLAST = d.is_last.values; DT = d.Datetime.values

    def close_pos(i, price, reason):
        nonlocal equity, pos
        pnl = (price - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "long" else -1)
        fee = 40.0 + abs(price * pos["qty"]) * 0.0002       # rough charges model
        equity += pnl - fee
        trades.append(dict(side=pos["side"], entry=pos["entry"], exit=price,
                           qty=pos["qty"], pnl=pnl - fee, points=(price - pos["entry"]) *
                           (1 if pos["side"] == "long" else -1),
                           entry_i=pos["entry_i"], exit_i=i,
                           entry_dt=pos["entry_dt"], exit_dt=DT[i], reason=reason,
                           bars=i - pos["entry_i"]))
        pos = None

    for i in range(warm, n):
        # ---- manage open position (intrabar stop/target on THIS bar) ----
        if pos is not None:
            side = pos["side"]
            if variant == "trail":
                # update trailing stop using prior close (already set), then breach-check
                if side == "long":
                    pos["stop"] = max(pos["stop"], C[i] - p["trail_atr"] * AT[i])
                    if L[i] <= pos["stop"]:
                        close_pos(i, pos["stop"], "Trailing SL")
                else:
                    pos["stop"] = min(pos["stop"], C[i] + p["trail_atr"] * AT[i])
                    if H[i] >= pos["stop"]:
                        close_pos(i, pos["stop"], "Trailing SL")
            else:  # atr: fixed stop + target, stop checked first if both hit
                if side == "long":
                    if L[i] <= pos["stop"]:
                        close_pos(i, pos["stop"], "ATR SL")
                    elif H[i] >= pos["target"]:
                        close_pos(i, pos["target"], "ATR target")
                else:
                    if H[i] >= pos["stop"]:
                        close_pos(i, pos["stop"], "ATR SL")
                    elif L[i] <= pos["target"]:
                        close_pos(i, pos["target"], "ATR target")

        # ---- intraday forced exit on last bar ----
        if pos is not None and mode == "intraday" and ISLAST[i]:
            close_pos(i, C[i], "EOD 3:15")

        # ---- entries (on close of bar i-> next bar open); skip last bar in intraday ----
        can_enter = pos is None and not (mode == "intraday" and ISLAST[i])
        if can_enter and i + 1 < n:
            long_sig  = C[i] > UP[i] and C[i-1] <= UP[i-1] and C[i] > EM[i]
            short_sig = C[i] < LO[i] and C[i-1] >= LO[i-1] and C[i] < EM[i]
            if long_sig or short_sig:
                side = "long" if long_sig else "short"
                entry = O[i + 1]
                a = AT[i] if AT[i] > 0 else max(1.0, entry * 0.001)
                stop_mult = p.get("stop_mult", p.get("atr_sl", 2.0))
                stop_dist = stop_mult * a
                risk_pct = p.get("risk_pct", RISK_PCT)
                qty = (risk_pct * equity) / max(stop_dist, 1e-6)
                # leverage cap: notional (qty*entry) must not exceed lev_cap x equity.
                # lev_cap=1.0 => NO leverage (position never bigger than capital).
                lev_cap = p.get("leverage_cap", 1.0)
                if lev_cap:
                    qty = min(qty, lev_cap * equity / entry)
                stop = entry - stop_dist if side == "long" else entry + stop_dist
                target = None
                if variant == "atr":
                    tp = p["atr_tp"] * a
                    target = entry + tp if side == "long" else entry - tp
                pos = dict(side=side, entry=entry, qty=qty, stop=stop, target=target,
                           entry_i=i + 1, entry_dt=DT[i + 1])

        # ---- mark equity ----
        m = equity
        if pos is not None:
            m += (C[i] - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "long" else -1)
        eq_curve.append((DT[i], m))

    if pos is not None:
        close_pos(n - 1, C[n - 1], "End")

    eqdf = pd.DataFrame(eq_curve, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant=variant, mode=mode)


# ---------------- metrics ----------------
def _annualize_sharpe(eqdf):
    e = eqdf.copy()
    e["day"] = pd.to_datetime(e.Datetime).dt.date
    daily = e.groupby("day").equity.last()
    ret = daily.pct_change().dropna()
    if ret.std() == 0 or len(ret) < 5:
        return 0.0, 0.0, ret
    sharpe = ret.mean() / ret.std() * np.sqrt(252)
    downside = ret[ret < 0].std()
    sortino = ret.mean() / downside * np.sqrt(252) if downside and downside > 0 else 0.0
    return sharpe, sortino, ret


def metrics(res):
    tr = pd.DataFrame(res["trades"])
    eq = res["equity"]
    out = {"trades": len(tr), "variant": res["variant"], "mode": res["mode"],
           "params": res["params"]}
    if tr.empty:
        out.update(dict(net_pct=0, sharpe=0, maxdd=0, win_rate=0))
        return out, tr

    final = res["final"]
    net = final - START_CAP
    out["start_cap"] = START_CAP
    out["final_cap"] = final
    out["net_abs"] = net
    out["net_pct"] = net / START_CAP * 100

    # drawdown / underwater on marked equity
    e = eq.equity.values
    peak = np.maximum.accumulate(e)
    uw = (e / peak - 1) * 100
    out["maxdd"] = uw.min()
    # max underwater period in days
    edt = pd.to_datetime(eq.Datetime)
    under = uw < -0.01
    out["underwater_days"] = int(_max_underwater_days(edt, under))

    sharpe, sortino, dret = _annualize_sharpe(eq)
    out["sharpe"] = sharpe
    out["sortino"] = sortino

    span_days = (edt.iloc[-1] - edt.iloc[0]).days or 1
    years = span_days / 365.25
    out["years"] = years
    out["annual_return"] = ((final / START_CAP) ** (1 / years) - 1) * 100 if years > 0 else 0
    out["calmar"] = out["annual_return"] / abs(out["maxdd"]) if out["maxdd"] else 0

    wins = tr[tr.pnl > 0]; losses = tr[tr.pnl <= 0]
    out["win_rate"] = len(wins) / len(tr) * 100
    out["avg_win"] = wins.pnl.mean() if len(wins) else 0
    out["avg_loss"] = losses.pnl.mean() if len(losses) else 0
    out["wl_ratio"] = abs(out["avg_win"] / out["avg_loss"]) if out["avg_loss"] else 0
    gp = wins.pnl.sum(); gl = abs(losses.pnl.sum())
    out["profit_factor"] = gp / gl if gl else 0
    out["expectancy"] = tr.pnl.mean()
    out["largest_win"] = tr.pnl.max()
    out["largest_loss"] = tr.pnl.min()
    out["total_wins"] = len(wins); out["total_losses"] = len(losses)

    lo = tr[tr.side == "long"]; sh = tr[tr.side == "short"]
    out["win_long"] = (lo.pnl > 0).mean() * 100 if len(lo) else 0
    out["win_short"] = (sh.pnl > 0).mean() * 100 if len(sh) else 0
    out["pct_long"] = len(lo) / len(tr) * 100
    out["pct_short"] = len(sh) / len(tr) * 100

    out["avg_bars"] = tr.bars.mean()
    out["win_avg_bars"] = wins.bars.mean() if len(wins) else 0
    out["loss_avg_bars"] = losses.bars.mean() if len(losses) else 0

    # streaks
    signs = (tr.pnl > 0).astype(int).values
    out["win_streak"] = _max_run(signs, 1)
    out["loss_streak"] = _max_run(signs, 0)

    out["fees"] = len(tr) * 40.0 + (tr.exit * tr.qty).abs().sum() * 0.0002
    td = tr.shape[0]
    out["trades_per_day"] = td / max(1, pd.to_datetime(eq.Datetime).dt.date.nunique())
    out["trades_per_week"] = out["trades_per_day"] * 5
    out["trades_per_month"] = out["trades_per_day"] * 21

    out["daily_returns"] = dret.tolist()
    out["underwater"] = uw.tolist()
    return out, tr


def _max_run(arr, val):
    best = cur = 0
    for x in arr:
        if x == val:
            cur += 1; best = max(best, cur)
        else:
            cur = 0
    return best


def _max_underwater_days(dts, under):
    best = cur_start = 0
    cur = None
    for t, u in zip(dts, under):
        if u:
            if cur is None:
                cur = t
        else:
            if cur is not None:
                best = max(best, (t - cur).days); cur = None
    if cur is not None:
        best = max(best, (dts.iloc[-1] - cur).days)
    return best


def load(path="nifty_1h.csv"):
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    df = pd.read_csv(os.path.join(here, path), parse_dates=["Datetime"])
    return df


if __name__ == "__main__":
    df = load()
    for variant in ("trail", "atr"):
        for mode in ("intraday", "positional"):
            res = backtest(df, variant, mode)
            m, tr = metrics(res)
            print(f"{variant:5s} {mode:10s}  trades={m['trades']:4d}  "
                  f"net={m.get('net_pct',0):7.1f}%  sharpe={m.get('sharpe',0):5.2f}  "
                  f"maxDD={m.get('maxdd',0):6.1f}%  win={m.get('win_rate',0):4.1f}%")
