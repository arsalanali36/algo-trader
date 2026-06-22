import itertools
import multiprocessing
import pandas as pd
import time
from datetime import datetime
import sys
import os

# Ensure the root directory is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from _TOOLS import backtest_engine
from _TOOLS.backtest_engine import ensure_equity_data

def run_single_backtest(kwargs):
    strat_type = kwargs['strat_type']
    cfg = kwargs['cfg']
    date_from = kwargs['date_from']
    date_to = kwargs['date_to']
    
    try:
        res = backtest_engine.run_backtest(strat_type, cfg, date_from, date_to)
        
        if "error" in res:
            return {"cfg": cfg, "error": res["error"]}
            
        summary = res.get("summary", {})
        net_pnl = summary.get("net_pnl", 0)
        win_rate = summary.get("win_rate", 0)
        total_trades = summary.get("total_trades", 0)
        max_dd = summary.get("max_dd", 0)
        
        return {
            "cfg": cfg,
            "net_pnl": net_pnl,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "max_dd": max_dd,
            "error": None
        }
    except Exception as e:
        return {"cfg": cfg, "error": str(e)}

if __name__ == '__main__':
    # ---------------------------------------------------------
    # 1. SETUP YOUR OPTIMIZATION PARAMETERS HERE
    # ---------------------------------------------------------
    strat_type = "bb"
    date_from = "2025-01-01"
    date_to = datetime.today().strftime('%Y-%m-%d')
    
    # You can specify multiple symbols separated by commas
    symbols = "RELIANCE" 
    
    grid = {
        "timeframe": ["1m", "3m", "5m", "10m", "15m", "30m", "1h"],
        "bb_window": [10, 15, 20, 25, 30],
        "bb_std": [1.5, 2.0, 2.5],
        "allow_short": ["false", "true"]
    }
    
    # ---------------------------------------------------------
    # 2. PRE-FLIGHT: DOWNLOAD DATA IF MISSING (AVOIDS RACE CONDITIONS)
    # ---------------------------------------------------------
    print(f"Checking data for symbols: {symbols}")
    symbol_list = [s.strip() for s in symbols.split(",")]
    for sym in symbol_list:
        ensure_equity_data(sym, date_from, date_to)
    
    # ---------------------------------------------------------
    # 3. GENERATE ALL COMBINATIONS
    # ---------------------------------------------------------
    keys, values = zip(*grid.items())
    permutations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    tasks = []
    for p in permutations:
        cfg = {
            "instrument": "equity",
            "symbols": symbols,
            "timeframe": p["timeframe"],
            "bb_window": str(p["bb_window"]),
            "bb_std": str(p["bb_std"]),
            "allow_short": p["allow_short"]
        }
        tasks.append({
            "strat_type": strat_type,
            "cfg": cfg,
            "date_from": date_from,
            "date_to": date_to
        })
        
    print(f"\nStarting optimizer for {strat_type.upper()}...")
    print(f"Total combinations to test: {len(tasks)}")
    print(f"Using {multiprocessing.cpu_count()} CPU cores in parallel. Please wait...\n")
    
    start_time = time.time()
    results = []
    
    # ---------------------------------------------------------
    # 4. RUN IN PARALLEL USING MULTIPROCESSING
    # ---------------------------------------------------------
    from concurrent.futures import ProcessPoolExecutor, as_completed
    
    with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
        futures = {executor.submit(run_single_backtest, task): task for task in tasks}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            res = future.result()
            if not res.get("error"):
                results.append(res)
            
            # Simple progress print
            if completed % 10 == 0 or completed == len(tasks):
                print(f"Progress: {completed}/{len(tasks)} combinations evaluated...")
                
    end_time = time.time()
    
    if not results:
        print("\nAll combinations failed or returned empty! Check dates or symbols.")
        sys.exit(0)
        
    # ---------------------------------------------------------
    # 5. SORT AND DISPLAY RESULTS
    # ---------------------------------------------------------
    df = pd.DataFrame(results)
    cfg_df = pd.json_normalize(df['cfg'])
    df = pd.concat([cfg_df, df.drop(columns=['cfg', 'error'])], axis=1)
    
    # Clean up formatting for display
    if 'win_rate' in df.columns:
        df['win_rate'] = df['win_rate'].round(1).astype(str) + '%'
    if 'net_pnl' in df.columns:
        df['net_pnl'] = df['net_pnl'].round(2)
        
    # Sort by Net PnL descending
    df_sorted = df.sort_values(by="net_pnl", ascending=False)
    
    print(f"\n✅ Optimization finished in {round(end_time - start_time, 2)} seconds!")
    print("-" * 90)
    print("TOP 10 MOST PROFITABLE COMBINATIONS:")
    print("-" * 90)
    
    cols_to_show = ["timeframe", "bb_window", "bb_std", "allow_short", "total_trades", "win_rate", "max_dd", "net_pnl"]
    print(df_sorted[cols_to_show].head(10).to_string(index=False))
    
    csv_file = "optimization_results_bb.csv"
    df_sorted.to_csv(csv_file, index=False)
    print(f"\n💾 Full report with all {len(tasks)} combinations saved to: {csv_file}")
