import itertools
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import time
import sys
import os

# Ensure the root directory is in sys.path if not already
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _TOOLS import backtest_engine

def _run_single_worker(kwargs):
    strat_type = kwargs['strat_type']
    cfg = kwargs['cfg']
    date_from = kwargs['date_from']
    date_to = kwargs['date_to']
    
    try:
        res = backtest_engine.run_backtest(strat_type, cfg, date_from, date_to)
        
        if "error" in res:
            return {"cfg": cfg, "error": res["error"]}
            
        summary = res.get("summary", {})
        net_pnl = summary.get("pnl_points", 0)
        win_rate = summary.get("win_rate", 0)
        total_trades = summary.get("n_trades", 0)
        max_dd = summary.get("max_drawdown", 0)
        profit_factor = summary.get("profit_factor", 0)
        sharpe = summary.get("sharpe", 0)
        
        return {
            "cfg": cfg,
            "net_pnl": net_pnl,
            "win_rate": win_rate,
            "total_trades": total_trades,
            "max_dd": max_dd,
            "profit_factor": profit_factor,
            "sharpe": sharpe,
            "error": None
        }
    except Exception as e:
        return {"cfg": cfg, "error": str(e)}

import json
from datetime import datetime

def run_optimization_stream(strat_type, grid, date_from, date_to, symbols):
    """
    Generator that yields progress dictionary: {"progress": int}
    And finally yields the full results list: {"results": list}
    """
    keys, values = zip(*grid.items())
    # Generate all combinations. Handle lists vs single strings if user didn't split them.
    # We expect grid values to be lists.
    permutations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    tasks = []
    for p in permutations:
        cfg = {"instrument": "equity", "symbols": symbols}
        # Add all param permutations into cfg
        for k, v in p.items():
            cfg[k] = str(v).strip()
            
        tasks.append({
            "strat_type": strat_type,
            "cfg": cfg,
            "date_from": date_from,
            "date_to": date_to
        })
        
    total_tasks = len(tasks)
    
    # Pre-flight data check to ensure data is downloaded by the main thread 
    # before workers attempt parallel downloads.
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    for sym in symbol_list:
        backtest_engine.ensure_equity_data(sym, date_from, date_to)

    results = []
    
    if total_tasks == 0:
        yield {"results": results}
        return

    # Yield initial progress
    yield {"progress": 0, "total": total_tasks}

    # Parallel processing
    completed = 0
    with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
        futures = {executor.submit(_run_single_worker, task): task for task in tasks}
        
        for future in as_completed(futures):
            completed += 1
            res = future.result()
            if not res.get("error"):
                results.append(res)
            
            # Yield progress update
            pct = int((completed / total_tasks) * 100)
            yield {"progress": pct, "completed": completed, "total": total_tasks}
            
    # Sort results by Net PnL (descending)
    results = sorted(results, key=lambda x: x.get("net_pnl", 0), reverse=True)
    top_results = results[:100]
    
    # Save to history file
    try:
        hist_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "saved_optimizations.json")
        hist = []
        if os.path.exists(hist_file):
            with open(hist_file, "r") as f:
                try: hist = json.load(f)
                except: pass
                
        # Generate a unique ID and title
        run_id = int(time.time() * 1000)
        hist.insert(0, {
            "id": run_id,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "strat_type": strat_type,
            "symbols": symbols,
            "date_from": date_from,
            "date_to": date_to,
            "results": top_results
        })
        
        # Keep only the last 20 optimizations to save space
        with open(hist_file, "w") as f:
            json.dump(hist[:20], f, indent=2)
    except Exception as e:
        print("Failed to save optimization history:", e)
    
    yield {"results": top_results}
