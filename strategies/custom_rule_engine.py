import pandas as pd
from _CHARTING import indicators as chind
from _CHARTING import spec as chspec
from _TOOLS.backtest_engine import _cfg_symbol, TF_MIN, _buffered_from, ensure_and_load_symbol, _fill

def _run_bb(date_from, date_to, cfg):
    symbol = _cfg_symbol(cfg)
    tf_min = TF_MIN.get(cfg.get("timeframe", "1D"), 1440)
    buffered_from = _buffered_from(date_from, symbol)
    df = ensure_and_load_symbol(symbol, buffered_from, date_to, tf_min)
    if df.empty:
        # Caller expects 3 values (trades, df, spec)
        return [], pd.DataFrame(), None
        
    cutoff_ts = pd.to_datetime(date_from) if date_from else None
    window = int(cfg.get("bb_window", 20))
    std_dev = float(cfg.get("bb_std", 2.0))
    allow_short = cfg.get("allow_short", False)
    if isinstance(allow_short, str): allow_short = allow_short.lower() == "true"
    
    sl_pct = float(cfg.get("sl_pct", 0.0))
    tp_pct = float(cfg.get("tp_pct", 0.0))
    
    # Custom formula strings
    entry_long_str = str(cfg.get("entry_long", "c_close < c_lower"))
    entry_short_str = str(cfg.get("entry_short", "allow_short and c_close > c_upper"))
    exit_long_str = str(cfg.get("exit_long", "c_close > c_sma"))
    exit_short_str = str(cfg.get("exit_short", "c_close < c_sma"))
    
    # Compile for speed
    el_code = compile(entry_long_str, "<string>", "eval")
    es_code = compile(entry_short_str, "<string>", "eval")
    xl_code = compile(exit_long_str, "<string>", "eval")
    xs_code = compile(exit_short_str, "<string>", "eval")
    
    # Vectorized computations
    close_s = df["close"].astype(float)
    df["sma"] = close_s.rolling(window=window).mean()
    std_s = close_s.rolling(window=window).std()
    df["upper"] = df["sma"] + (std_s * std_dev)
    df["lower"] = df["sma"] - (std_s * std_dev)
    
    # 52-week EMA calculation (52 weeks * 5 trading days = 260 periods for daily)
    ema_52_period = int(cfg.get("ema_52_period", 260))  # Default 260 for daily (52 weeks * 5 days)
    df["ema_52"] = close_s.ewm(span=ema_52_period, adjust=False).mean()
    
    # 100-period EMA calculation
    ema_100_period = int(cfg.get("ema_100_period", 100))  # Default 100
    df["ema_100"] = close_s.ewm(span=ema_100_period, adjust=False).mean()
    
    atr_len = int(cfg.get("atr_len", 14))
    df["prev_c"] = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["prev_c"]).abs()
    tr3 = (df["low"] - df["prev_c"]).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.rolling(atr_len).mean()
    
    trades, pos, cur = [], 0, None
    n = len(df)
    
    max_trades = int(cfg.get("max_trades_per_day", 0))
    trades_today = 0
    last_trade_date = None
    
    for i in range(window + 2, n):
        row = df.iloc[i]
        c_close = float(row["close"])
        c_sma = float(row["sma"])
        c_upper = float(row["upper"])
        c_lower = float(row["lower"])
        c_ema_52 = float(row["ema_52"]) if pd.notnull(row["ema_52"]) else c_sma  # Fallback to SMA if EMA not available
        c_ema_100 = float(row["ema_100"]) if pd.notnull(row["ema_100"]) else c_sma  # Fallback to SMA if EMA not available
        
        c_high = float(row["high"])
        c_low = float(row["low"])
        c_open = float(row["open"])
        c_body_pct = abs(c_open - c_close) / c_close * 100
        c_atr = float(row["atr"]) if pd.notnull(row["atr"]) else 0.0
        
        env = {
            "abs": abs,
            "c_open": c_open,
            "c_close": c_close,
            "c_high": c_high,
            "c_low": c_low,
            "c_body_pct": c_body_pct,
            "c_atr": c_atr,
            "c_sma": c_sma,
            "c_upper": c_upper,
            "c_lower": c_lower,
            "c_ema_52": c_ema_52,  # 52-week EMA in environment
            "c_ema_100": c_ema_100,  # 100-period EMA in environment
            "allow_short": allow_short,
            "entry_price": cur["entry_price"] if cur else None,
            "entry_candle_high": cur["entry_candle_high"] if cur else None,
            "entry_candle_low": cur["entry_candle_low"] if cur else None
        }
        
        current_date = row["time"].date()
        if current_date != last_trade_date:
            trades_today = 0
            last_trade_date = current_date
            
        sig = None
        if pos == 0:
            if max_trades == 0 or trades_today < max_trades:
                if eval(el_code, {}, env):
                    sig = "BUY"
                elif eval(es_code, {}, env):
                    sig = "SELL"
        elif pos == 1:
            ep = cur["entry_price"]
            if sl_pct > 0 and c_close <= ep * (1 - sl_pct/100):
                sig = "EXIT"
                exit_reason = "SL Hit"
            elif tp_pct > 0 and c_close >= ep * (1 + tp_pct/100):
                sig = "EXIT"
                exit_reason = "TP Hit"
            elif eval(xl_code, {}, env):
                sig = "EXIT"
                exit_reason = "Rule Exit"
        elif pos == -1:
            ep = cur["entry_price"]
            if sl_pct > 0 and c_close >= ep * (1 + sl_pct/100):
                sig = "EXIT"
                exit_reason = "SL Hit"
            elif tp_pct > 0 and c_close <= ep * (1 - tp_pct/100):
                sig = "EXIT"
                exit_reason = "TP Hit"
            elif eval(xs_code, {}, env):
                sig = "EXIT"
                exit_reason = "Rule Exit"
        
        if sig == "EXIT" and pos != 0:
            ft, fp = _fill(df, i)
            cur["exit_time"], cur["exit_price"], cur["exit_reason"] = ft, fp, locals().get("exit_reason", "Rule Exit")
            trades.append(cur); cur, pos = None, 0
        elif sig in ("BUY", "SELL") and pos == 0:
            ft, fp = _fill(df, i)
            side = "Long" if sig == "BUY" else "Short"
            cur = {"entry_time": ft, "entry_price": fp, "side": side, "exit_time": None, "exit_price": None, "exit_reason": None, "entry_candle_high": c_high, "entry_candle_low": c_low}
            pos = 1 if sig == "BUY" else -1
            trades_today += 1
            
    if cur:
        last = df.iloc[-1]
        cur["exit_time"], cur["exit_price"], cur["exit_reason"] = last["time"], float(last["close"]), "EOD"
        trades.append(cur)
        
    # Calculate indicators BEFORE truncating the warm-up buffer!
    close_s = df["close"].astype(float)
    sma_s = chind.compute_indicator(df, "SMA", period=window)
    std_s = close_s.rolling(window=window).std()
    up_s = sma_s + (std_s * std_dev)
    dn_s = sma_s - (std_s * std_dev)
    
    # Calculate 52-week EMA for plotting
    ema_52_period_plot = int(cfg.get("ema_52_period", 260))
    ema_52_s = close_s.ewm(span=ema_52_period_plot, adjust=False).mean()
    
    # Calculate 100-period EMA for plotting
    ema_100_period_plot = int(cfg.get("ema_100_period", 100))
    ema_100_s = close_s.ewm(span=ema_100_period_plot, adjust=False).mean()
    
    if cutoff_ts is not None:
        trades = [tr for tr in trades if tr["entry_time"] >= cutoff_ts]
        mask = df["time"] >= cutoff_ts
        df = df[mask].reset_index(drop=True)
        sma_s = sma_s[mask].reset_index(drop=True)
        up_s = up_s[mask].reset_index(drop=True)
        dn_s = dn_s[mask].reset_index(drop=True)
        ema_52_s = ema_52_s[mask].reset_index(drop=True)
        ema_100_s = ema_100_s[mask].reset_index(drop=True)
        
    spec = chspec.build_plot_spec(df, indicators=[
        {"name": f"BB_Mid({window})", "series": sma_s, "type": "line", "color": "#d29922"},
        {"name": f"BB_Up", "series": up_s, "type": "line", "color": "#8b949e"},
        {"name": f"BB_Dn", "series": dn_s, "type": "line", "color": "#8b949e"},
        {"name": f"EMA_52", "series": ema_52_s, "type": "line", "color": "#f0883e"},  # Orange color for 52-week EMA
        {"name": f"EMA_100", "series": ema_100_s, "type": "line", "color": "#58a6ff"},  # Blue color for 100 EMA
    ])
    return trades, df, spec