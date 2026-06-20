"""
_CHARTING/zones.py — Pivot/key-level building + chart-renderable zone shapes.

traditional_pivots() and build_key_levels() extracted verbatim from
_TRADERS/range_trader.py. range_trader.py re-exports these names so
existing callers (validate_strategy.py, run_signal_engine) keep working
unchanged.
"""

import numpy as np

_LEVEL_COLORS = {
    "RESISTANCE": "#f85149",
    "SUPPORT":    "#3fb950",
    "CP":         "#d29922",
    "PD_H":       "#f85149",
    "PD_C":       "#1f6feb",
    "PD_L":       "#3fb950",
}


def traditional_pivots(h, l, c):
    """Traditional pivot points from prev day H/L/C."""
    P  = (h + l + c) / 3
    R1 = 2 * P - l
    S1 = 2 * P - h
    R2 = P + (h - l)
    S2 = P - (h - l)
    R3 = h + 2 * (P - l)
    S3 = l - 2 * (h - P)
    R4 = R3 + (h - l)
    S4 = S3 - (h - l)
    R5 = R4 + (h - l)
    S5 = S4 - (h - l)
    return dict(P=P, R1=R1, R2=R2, R3=R3, R4=R4, R5=R5,
                S1=S1, S2=S2, S3=S3, S4=S4, S5=S5)


def build_key_levels(daily_df, is_index=False, max_jump_pct=50.0):
    """
    Build all key levels: pivot + prev-day HLC + high/low chain.
    Returns list of (price, level_type) tuples.
    Sorted: resistances first, then CP, then supports, then chain.
    """
    if daily_df is None or len(daily_df) < 2:
        return []

    mj = 10.0 if is_index else max_jump_pct

    # Prev day (index -2 = yesterday, -1 = today/current)
    prev = daily_df.iloc[-2]
    ph, pl, pc = float(prev["high"]), float(prev["low"]), float(prev["close"])

    levels = []  # (price, type)

    # Pivot points
    piv = traditional_pivots(ph, pl, pc)
    for name in ["R5","R4","R3","R2","R1"]:
        levels.append((piv[name], "RESISTANCE"))
    levels.append((piv["P"], "CP"))
    for name in ["S1","S2","S3","S4","S5"]:
        levels.append((piv[name], "SUPPORT"))

    # Prev day H/L/C
    levels.append((ph, "PD_H"))
    levels.append((pc, "PD_C"))
    levels.append((pl, "PD_L"))

    # High chain: consecutive higher highs going back from prev day
    h_thresh = ph
    for i in range(len(daily_df) - 3, max(len(daily_df) - 23, -1), -1):
        row_h = float(daily_df.iloc[i]["high"])
        if row_h > h_thresh:
            jump = (row_h - h_thresh) / h_thresh * 100
            if jump <= mj:
                levels.append((row_h, "RESISTANCE"))
                h_thresh = row_h

    # Low chain: consecutive lower lows going back from prev day
    l_thresh = pl
    for i in range(len(daily_df) - 3, max(len(daily_df) - 23, -1), -1):
        row_l = float(daily_df.iloc[i]["low"])
        if row_l < l_thresh:
            drop = (l_thresh - row_l) / l_thresh * 100
            if drop <= mj:
                levels.append((row_l, "SUPPORT"))
                l_thresh = row_l

    # Remove NaN / zero
    levels = [(p, t) for p, t in levels if p and not np.isnan(p) and p > 0]
    return levels


def levels_to_chart_zones(key_levels, day_start_ts=None, day_end_ts=None):
    """
    Turn build_key_levels()'s (price, level_type) tuples into renderable
    price-line dicts for the chart. Pure formatting — no detection logic.
    """
    zones = []
    for price, ltype in key_levels:
        zones.append({
            "kind": "line",
            "price": float(price),
            "label": ltype,
            "color": _LEVEL_COLORS.get(ltype, "#8b949e"),
            "start_time": day_start_ts,
            "end_time": day_end_ts,
        })
    return zones


def zone_box_from_state(zone_upper, zone_lower, zone_type, start_time, end_time):
    """
    Turn one active GREEN/RED zone (as tracked by run_signal_engine's state
    machine) into a renderable zone-box dict. Stateless — caller supplies the
    bar's own time range; this function does not read or mutate strategy state.
    """
    if zone_upper is None or zone_lower is None:
        return None
    color = "#3fb95033" if zone_type == "GREEN" else "#f8514933"
    return {
        "kind": "box",
        "price_upper": float(zone_upper),
        "price_lower": float(zone_lower),
        "label": f"{zone_type} ZONE" if zone_type else "ZONE",
        "color": color,
        "start_time": start_time,
        "end_time": end_time,
    }
