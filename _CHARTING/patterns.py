"""
_CHARTING/patterns.py — Candle pattern detection (hammer/engulfing/harami).

Extracted verbatim from _TRADERS/range_trader.py (AA_CandlePatterns port).
range_trader.py re-exports these names so existing callers (validate_strategy.py,
run_signal_engine) keep working unchanged.
"""

DEFAULT_MIN_BODY_SIZE = 0.5    # minimum body in points (Pine minBodySize)
DEFAULT_WICK_RATIO    = 2.5    # wick >= WICK_RATIO * body (Pine wickRatio=2.5)


def _body(o, c):
    return abs(c - o)

def _lower_wick(o, c, l):
    return min(o, c) - l

def _upper_wick(o, c, h):
    return h - max(o, c)


def green_hammer(o, h, l, c, min_body=DEFAULT_MIN_BODY_SIZE, wick_ratio=DEFAULT_WICK_RATIO):
    body = _body(o, c)
    if body < min_body or c <= o:   # must be green
        return False
    lw = _lower_wick(o, c, l)
    uw = _upper_wick(o, c, h)
    return lw >= wick_ratio * body and uw <= body

def red_hammer(o, h, l, c, min_body=DEFAULT_MIN_BODY_SIZE, wick_ratio=DEFAULT_WICK_RATIO):
    body = _body(o, c)
    if body < min_body or c >= o:   # must be red
        return False
    lw = _lower_wick(o, c, l)
    uw = _upper_wick(o, c, h)
    return lw >= wick_ratio * body and uw <= body   # Pine: upperWick <= bodySize

def inv_red_hammer(o, h, l, c, min_body=DEFAULT_MIN_BODY_SIZE, wick_ratio=DEFAULT_WICK_RATIO):
    body = _body(o, c)
    if body < min_body or c >= o:   # must be red
        return False
    uw = _upper_wick(o, c, h)
    lw = _lower_wick(o, c, l)
    return uw >= wick_ratio * body and lw <= body   # Pine: lowerWick <= bodySize

def bull_engulfing(po, ph, pl, pc, o, h, l, c):
    prev_red   = pc < po
    curr_green = c > o
    prev_body = abs(po - pc)
    curr_body = abs(o - c)
    if not prev_red or not curr_green:
        return False
    return c >= po and o <= pc and curr_body > prev_body and prev_body >= 0.5

def bear_engulfing(po, ph, pl, pc, o, h, l, c):
    prev_green = pc > po
    curr_red   = c < o
    prev_body = abs(po - pc)
    curr_body = abs(o - c)
    if not prev_green or not curr_red:
        return False
    return c <= po and o >= pc and curr_body > prev_body and prev_body >= 0.5

def bull_harami(po, ph, pl, pc, o, h, l, c):
    prev_red   = pc < po
    curr_green = c > o
    prev_body  = abs(pc - po)
    curr_body  = abs(c - o)
    body50pct = curr_body >= prev_body * 0.5
    if not prev_red or not curr_green:
        return False
    return o > pc and c < po and body50pct

def bear_harami(po, ph, pl, pc, o, h, l, c):
    prev_green = pc > po
    curr_red   = c < o
    prev_body  = abs(pc - po)
    curr_body  = abs(c - o)
    body50pct = curr_body >= prev_body * 0.5
    if not prev_green or not curr_red:
        return False
    return o < pc and c > po and body50pct

def is_bullish_pattern(df, idx, min_body=DEFAULT_MIN_BODY_SIZE, wick_ratio=DEFAULT_WICK_RATIO):
    if idx < 1:
        return False
    r  = df.iloc[idx]
    rp = df.iloc[idx - 1]
    o, h, l, c    = float(r["open"]),  float(r["high"]),  float(r["low"]),  float(r["close"])
    po, ph, pl, pc = float(rp["open"]), float(rp["high"]), float(rp["low"]), float(rp["close"])
    return (green_hammer(o, h, l, c, min_body, wick_ratio) or
            bull_engulfing(po, ph, pl, pc, o, h, l, c))

def is_bearish_pattern(df, idx, min_body=DEFAULT_MIN_BODY_SIZE, wick_ratio=DEFAULT_WICK_RATIO):
    if idx < 1:
        return False
    r  = df.iloc[idx]
    rp = df.iloc[idx - 1]
    o, h, l, c    = float(r["open"]),  float(r["high"]),  float(r["low"]),  float(r["close"])
    po, ph, pl, pc = float(rp["open"]), float(rp["high"]), float(rp["low"]), float(rp["close"])
    return (red_hammer(o, h, l, c, min_body, wick_ratio) or
            inv_red_hammer(o, h, l, c, min_body, wick_ratio) or
            bear_engulfing(po, ph, pl, pc, o, h, l, c))


_PATTERN_CHECKS = {
    "green_hammer": ("bull", lambda o, h, l, c, po, ph, pl, pc, mb, wr: green_hammer(o, h, l, c, mb, wr)),
    "red_hammer": ("bear", lambda o, h, l, c, po, ph, pl, pc, mb, wr: red_hammer(o, h, l, c, mb, wr)),
    "inv_red_hammer": ("bear", lambda o, h, l, c, po, ph, pl, pc, mb, wr: inv_red_hammer(o, h, l, c, mb, wr)),
    "bull_engulfing": ("bull", lambda o, h, l, c, po, ph, pl, pc, mb, wr: bull_engulfing(po, ph, pl, pc, o, h, l, c)),
    "bear_engulfing": ("bear", lambda o, h, l, c, po, ph, pl, pc, mb, wr: bear_engulfing(po, ph, pl, pc, o, h, l, c)),
    "bull_harami": ("bull", lambda o, h, l, c, po, ph, pl, pc, mb, wr: bull_harami(po, ph, pl, pc, o, h, l, c)),
    "bear_harami": ("bear", lambda o, h, l, c, po, ph, pl, pc, mb, wr: bear_harami(po, ph, pl, pc, o, h, l, c)),
}


def detect_pattern_tags(df, min_body=DEFAULT_MIN_BODY_SIZE, wick_ratio=DEFAULT_WICK_RATIO, time_col=None):
    """
    Walk df once, tag every bar that matches any known candle pattern.
    Chart-marker use only — never consumed by signal/entry logic.
    Returns [{"time": unix_seconds, "bar_index": i, "pattern": name, "direction": "bull"/"bear"}, ...]
    time_col: name of the datetime column to use (e.g. "time" or "date"); falls
    back to "date" then "time" then the row index if not given.
    """
    tags = []
    if df is None or len(df) < 2:
        return tags
    col = time_col or ("date" if "date" in df.columns else ("time" if "time" in df.columns else None))
    for i in range(1, len(df)):
        r, rp = df.iloc[i], df.iloc[i - 1]
        o, h, l, c     = float(r["open"]),  float(r["high"]),  float(r["low"]),  float(r["close"])
        po, ph, pl, pc = float(rp["open"]), float(rp["high"]), float(rp["low"]), float(rp["close"])
        ts = r[col] if col else r.name
        unix_ts = int(ts.timestamp()) if hasattr(ts, "timestamp") else int(ts)
        for name, (direction, check) in _PATTERN_CHECKS.items():
            if check(o, h, l, c, po, ph, pl, pc, min_body, wick_ratio):
                tags.append({"time": unix_ts, "bar_index": i, "pattern": name, "direction": direction})
    return tags
