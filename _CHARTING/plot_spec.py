"""
_CHARTING/plot_spec.py — Pure JSON shaper for the dashboard chart.

No I/O, no strategy logic. Strategies/runners hand this whatever they already
computed (indicator series, zone dicts, pattern tags) and get back the
"plot_spec" fragment that backtest_engine.py merges into the backtest result
JSON, and that backtest_chart.html's generic _renderPlotSpec() consumes.
"""

from _CHARTING.indicators import indicator_series_to_points


def build_plot_spec(df, *, indicators=None, zones=None, pattern_tags=None):
    """
    indicators: list of {"name": str, "series": pd.Series, "type": "line"|"histogram", "color": str}
                OR already-built {"name", "type", "color", "values": [...]} dicts.
    zones: list of zone dicts from _CHARTING.zones (levels_to_chart_zones / zone_box_from_state).
    pattern_tags: list of tag dicts from _CHARTING.patterns.detect_pattern_tags.
    """
    spec = {"indicators": [], "zones": list(zones or []), "pattern_tags": list(pattern_tags or [])}

    for ind in (indicators or []):
        if "values" in ind:
            spec["indicators"].append(ind)
            continue
        spec["indicators"].append({
            "name": ind["name"],
            "type": ind.get("type", "line"),
            "color": ind.get("color", "#8b949e"),
            "overlay": ind.get("overlay", True),   # False = own bottom panel (oscillator)
            "values": indicator_series_to_points(ind["series"], df),
        })

    return spec
