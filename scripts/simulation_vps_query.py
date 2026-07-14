import sqlite3
import json
import sys
import csv
from datetime import datetime

sys.path.insert(0, '/root/ARSALAN/CODE3B- TV BACKTEST ENGINE')
try:
    from _core import dhan_master
except ImportError:
    dhan_master = None

def get_lot_size(trad_sym):
    return 1

lot_sizes = {}
try:
    with open('/root/ARSALAN/CODE3B- TV BACKTEST ENGINE/data/api-scrip-master.csv', 'r') as f:
        for i, line in enumerate(f):
            if i == 0: continue
            parts = line.split(',')
            if len(parts) > 6:
                tsym = parts[5]
                lsize = parts[6]
                if tsym and lsize:
                    try:
                        lot_sizes[tsym] = float(lsize)
                    except:
                        pass
except:
    pass

def parse_time(ts_str):
    try:
        if "T" in ts_str:
            dt = datetime.fromisoformat(ts_str)
        else:
            return ts_str.split(" ")[1][:5]
        return dt.strftime("%H:%M")
    except Exception:
        if " " in ts_str: return ts_str.split(" ")[1][:5]
        if "T" in ts_str: return ts_str.split("T")[1][:5]
        return ts_str[:5]

def calculate_results_rs(entry_px, run_up_pts, run_down_pts, qty, lot_size):
    lots = qty / lot_size if lot_size > 0 else 1
    if lots < 1: lots = 1
    
    run_up_rs = run_up_pts * qty
    run_down_rs = run_down_pts * qty
    
    def calc_legacy(t_rs, sl_rs):
        if run_down_rs >= sl_rs: return "Hit SL", -sl_rs
        elif run_up_rs >= t_rs: return "Hit Target", t_rs
        else: return "Open/Actual", None

    def calc_aggr(t_rs, sl_rs, step_rs):
        steps = int(run_up_rs // step_rs)
        if steps > 0: current_sl_rs = -sl_rs + (steps * step_rs)
        else: current_sl_rs = -sl_rs
        
        # Super-trail after 30%
        aggr_threshold = t_rs * 0.30
        if run_up_rs >= aggr_threshold:
            base_steps = int(aggr_threshold // step_rs)
            extra_steps = int((run_up_rs - aggr_threshold) // step_rs)
            current_sl_rs = -sl_rs + (base_steps * step_rs) + (extra_steps * step_rs * 2)
            
        if current_sl_rs > run_up_rs: current_sl_rs = run_up_rs
        
        if run_up_rs >= t_rs: return "Hit Target", t_rs
        elif run_down_rs >= sl_rs and run_up_rs < step_rs: return "Hit Init SL", -sl_rs
        elif run_down_rs >= abs(current_sl_rs) if current_sl_rs < 0 else True: 
            if current_sl_rs > 0: return "Hit Trail SL", current_sl_rs
            else:
                if run_down_rs >= abs(current_sl_rs): return "Hit Trail SL", current_sl_rs
                else: return "Open/Actual", None
        else: return "Open/Actual", None

    # Base Variations
    l1_str, l1_rs = calc_legacy(5000 * lots, 3000 * lots)
    a1_str, a1_rs = calc_aggr(2000 * lots, 1000 * lots, 100 * lots)

    # Variation 2: Closer Legacy, Wider Aggr
    l2_str, l2_rs = calc_legacy(3000 * lots, 1500 * lots)  # 1:2 tight
    a2_str, a2_rs = calc_aggr(4000 * lots, 1500 * lots, 200 * lots) # Give it room to breathe

    # Variation 3: Huge Legacy, Massive Aggr Runner
    l3_str, l3_rs = calc_legacy(8000 * lots, 3000 * lots)  # Giant swing
    a3_str, a3_rs = calc_aggr(10000 * lots, 1000 * lots, 100 * lots) # Infinite runner with tight lock

    return (l1_str, l1_rs, a1_str, a1_rs, 
            l2_str, l2_rs, a2_str, a2_rs,
            l3_str, l3_rs, a3_str, a3_rs)

try:
    db = sqlite3.connect('/root/ARSALAN/CODE3B- TV BACKTEST ENGINE/data/trades.db')
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT id, ts, date, symbol, trad_sym, side, qty, price, tags, correlation_id, group_id, status FROM orders WHERE date >= '2026-06-21' ORDER BY id").fetchall()
    
    entries = {}
    completed_trades = []
    total_actual = 0; total_legacy = 0; total_aggr = 0
    
    for r in rows:
        tag_str = r['tags'] or "[]"
        tags = json.loads(tag_str) if tag_str.startswith('[') else []
        
        # Skip rejected or capital blocked trades!
        if "CAPITAL_BLOCKED" in tags or (r['status'] and r['status'].upper() in ['REJECTED', 'CANCELED', 'CANCELLED', 'BLOCKED', 'REJECT']):
            continue
        
        matched_e_id = None
        for e_id, entry in list(entries.items()):
            if entry['trad_sym'] == r['trad_sym'] and entry['side'] != r['side'] and entry['date'] == r['date']:
                matched_e_id = e_id
                break
                
        if matched_e_id is not None:
            entry = entries[matched_e_id]
            match_qty = min(entry['qty'], r['qty'])
            ep = entry['price']
            xp = r['price']
            
            ls = lot_sizes.get(entry['trad_sym'], match_qty)
            
            max_ltp = None; min_ltp = None
            for t in tags:
                if t.startswith("MAX_LTP:"): max_ltp = float(t.split(":")[1])
                if t.startswith("MIN_LTP:"): min_ltp = float(t.split(":")[1])
                
            if max_ltp is None:
                etag_str = entry['tags'] or "[]"
                etags = json.loads(etag_str) if etag_str.startswith('[') else []
                for t in etags:
                    if t.startswith("MAX_LTP:"): max_ltp = float(t.split(":")[1])
                    if t.startswith("MIN_LTP:"): min_ltp = float(t.split(":")[1])
            
            if entry['side'] == 'BUY':
                run_up_pts = (max_ltp - ep) if max_ltp else 0
                run_down_pts = (ep - min_ltp) if min_ltp else 0
                actual_net_pts = xp - ep
            else:
                run_up_pts = (ep - min_ltp) if min_ltp else 0
                run_down_pts = (max_ltp - ep) if max_ltp else 0
                actual_net_pts = ep - xp
                
            actual_rs = actual_net_pts * match_qty
            total_actual += actual_rs
                
            (l1_str, l1_rs, a1_str, a1_rs, 
             l2_str, l2_rs, a2_str, a2_rs,
             l3_str, l3_rs, a3_str, a3_rs) = calculate_results_rs(ep, run_up_pts, run_down_pts, match_qty, ls)
            
            def final_rs(rs, actual): return actual if rs is None else rs
            def adjust_open_str(s, rs, actual): return ("Open/Actual", actual) if "Trail" in s and actual > rs else (s, rs)
            def adjust_open_l_str(s, rs, actual): return (s, actual) if "Target" not in s and "SL" not in s and actual > rs else (s, rs)
            
            l1_rs = final_rs(l1_rs, actual_rs)
            a1_rs = final_rs(a1_rs, actual_rs)
            l2_rs = final_rs(l2_rs, actual_rs)
            a2_rs = final_rs(a2_rs, actual_rs)
            l3_rs = final_rs(l3_rs, actual_rs)
            a3_rs = final_rs(a3_rs, actual_rs)
            
            a1_str, a1_rs = adjust_open_str(a1_str, a1_rs, actual_rs)
            a2_str, a2_rs = adjust_open_str(a2_str, a2_rs, actual_rs)
            a3_str, a3_rs = adjust_open_str(a3_str, a3_rs, actual_rs)
            
            l1_str, l1_rs = adjust_open_l_str(l1_str, l1_rs, actual_rs)
            l2_str, l2_rs = adjust_open_l_str(l2_str, l2_rs, actual_rs)
            l3_str, l3_rs = adjust_open_l_str(l3_str, l3_rs, actual_rs)
            
            # Legacy Init SL adjustment (Wait, legacy doesn't trail, so if open, it's just open. But let's keep original logic for open failures)
            def adjust_init_sl(s, rs, step_pts, sl_pts):
                if "Open" in s and run_down_pts*match_qty >= sl_pts and run_up_pts*match_qty < step_pts:
                    return "Hit Init SL", -sl_pts
                return s, rs
                
            lots = match_qty/ls if ls>0 else 1
            a1_str, a1_rs = adjust_init_sl(a1_str, a1_rs, 100*lots, 1000*lots)
            a2_str, a2_rs = adjust_init_sl(a2_str, a2_rs, 200*lots, 1500*lots)
            a3_str, a3_rs = adjust_init_sl(a3_str, a3_rs, 100*lots, 1000*lots)
            
            if "Open" in l1_str and run_down_pts*match_qty >= 3000*lots: l1_str, l1_rs = "Hit SL", -3000*lots
            if "Open" in l2_str and run_down_pts*match_qty >= 1500*lots: l2_str, l2_rs = "Hit SL", -1500*lots
            if "Open" in l3_str and run_down_pts*match_qty >= 3000*lots: l3_str, l3_rs = "Hit SL", -3000*lots
            
            total_legacy += l1_rs
            total_aggr += a1_rs

            disp_sym = entry['symbol'] if entry['symbol'] else entry['trad_sym']
            trade_date = entry['date']
            in_time = parse_time(entry['ts'])
            out_time = parse_time(r['ts'])
            
            completed_trades.append({
                "ts_raw": entry['ts'], 
                "row": [
                    trade_date, f"{in_time} -> {out_time}", f"{disp_sym} ({entry['side']})", match_qty,
                    round(actual_rs, 2), round(run_up_pts * match_qty, 2), round(run_down_pts * match_qty, 2),
                    l1_str, round(l1_rs, 2), a1_str, round(a1_rs, 2),
                    l2_str, round(l2_rs, 2), a2_str, round(a2_rs, 2),
                    l3_str, round(l3_rs, 2), a3_str, round(a3_rs, 2)
                ]
            })
            
            if entry['qty'] <= r['qty']:
                del entries[matched_e_id]
            else:
                entry['qty'] -= r['qty']
        else:
            entries[r['id']] = dict(r)

    completed_trades.sort(key=lambda x: x['ts_raw'])

    writer = csv.writer(sys.stdout)
    writer.writerow([
        "Date", "Time", "Symbol", "Qty", "Actual Net (Rs)", "Run-Up (Rs)", "Run-Down (Rs)",
        "Legacy V1 (5k/3k) Status", "Legacy V1 Net (Rs)", "Aggressive V1 (2k/1k) Status", "Aggressive V1 Net (Rs)",
        "Legacy V2 (3k/1.5k) Status", "Legacy V2 Net (Rs)", "Aggressive V2 (4k/1.5k) Status", "Aggressive V2 Net (Rs)",
        "Legacy V3 (8k/3k) Status", "Legacy V3 Net (Rs)", "Aggressive V3 (10k/1k) Status", "Aggressive V3 Net (Rs)"
    ])
    
    total_l2 = 0; total_a2 = 0; total_l3 = 0; total_a3 = 0
    for ct in completed_trades:
        writer.writerow(ct["row"])
        total_l2 += ct["row"][12]
        total_a2 += ct["row"][14]
        total_l3 += ct["row"][16]
        total_a3 += ct["row"][18]

    writer.writerow([])
    writer.writerow(["TOTAL", "", "", "", round(total_actual, 2), "", "", "", round(total_legacy, 2), "", round(total_aggr, 2), "", round(total_l2, 2), "", round(total_a2, 2), "", round(total_l3, 2), "", round(total_a3, 2)])

except Exception as e:
    print(f"Error: {e}")
