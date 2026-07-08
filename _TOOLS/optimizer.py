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

    # CRITICAL: run_backtest's runners read the SINGLE `symbol` key (or fall back
    # to "NIFTY"). The optimizer only ever set `symbols` (plural, a comma STRING)
    # — which _run_range ignores outright and _cfg_symbol only honors as a LIST —
    # so EVERY optimize combo silently ran on NIFTY regardless of the picked
    # symbol. That's why an SBIN optimize showed different stats than "Load"
    # (which runs on the real symbol): they were different symbols entirely.
    # Fix: set `symbol` per run. Single symbol → use run_backtest's own summary
    # (bit-identical to Load). Multi symbol → run each and combine trades via the
    # SAME _compute_stats Load's _runMultiSymbol mirrors, so optimize == Load.
    try:
        syms = [s.strip() for s in str(cfg.get("symbols", "")).split(",") if s.strip()]
        if not syms:
            syms = ["NIFTY"]

        if len(syms) == 1:
            csym = dict(cfg); csym["symbol"] = syms[0]
            res = backtest_engine.run_backtest(strat_type, csym, date_from, date_to)
            if "error" in res:
                return {"cfg": cfg, "error": res["error"]}
            summary = res.get("summary", {})
        else:
            all_trades = []
            errors = []
            for sym in syms:
                csym = dict(cfg); csym["symbol"] = sym
                res = backtest_engine.run_backtest(strat_type, csym, date_from, date_to)
                if "error" in res:
                    errors.append(f"{sym}: {res['error']}")
                    continue
                all_trades.extend(res.get("trades") or [])
            if not all_trades and errors:
                return {"cfg": cfg, "error": "; ".join(errors)}
            # _trades_json rows carry side/entry_price/exit_price — exactly what
            # _compute_stats needs — so the combined summary matches a Load-time
            # multi-symbol run (which combines the same way, client-side).
            summary = backtest_engine._compute_stats(all_trades)

        return {
            "cfg": cfg,
            "net_pnl": summary.get("pnl_points", 0),
            "win_rate": summary.get("win_rate", 0),
            "total_trades": summary.get("n_trades", 0),
            "max_dd": summary.get("max_drawdown", 0),
            "profit_factor": summary.get("profit_factor", 0),
            "sharpe": summary.get("sharpe", 0),
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
        # Add all param permutations into cfg — keep the ORIGINAL type (int/
        # float/bool), don't str() it. The builtin runners (_run_range/_run_rsi/
        # _run_ema) do numeric comparisons on these (e.g. max_candle_size), so a
        # stringified "25" crashed with "'>' not supported between float and str"
        # — which is why range/rsi/ema optimize never produced results (only bb,
        # whose custom_rule_engine parses strings, ever worked). Grid values
        # already arrive correctly typed from the frontend's coercion.
        for k, v in p.items():
            cfg[k] = v.strip() if isinstance(v, str) else v
            
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
