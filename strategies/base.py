"""
base.py — strategy contract documentation.

A strategy module must expose:

    def evaluate(df, cfg, pos) -> str | None
        df  : pandas DataFrame [time, open, high, low, close, volume]
              (oldest first, last row = latest candle)
        cfg : dict — the strategy's config block from nifty_config.json
        pos : str | None — current position for this symbol: 'LONG','SHORT',None

        Returns one of:
            'BUY'        -> open / flip long
            'SELL'       -> open / flip short
            'EXIT'       -> close current position
            None         -> do nothing

Keep evaluate() pure (no I/O, no orders). The engine handles routing,
order placement, position state, caps, and logging.

This file is documentation only — no base class is required.
"""
