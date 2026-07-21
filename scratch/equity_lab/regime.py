"""Regime gates for equity baskets. A regime_fn(rebal_date) -> bool decides, at each
rebalance, whether to hold the basket (True) or sit in cash (False). Halves drawdown and
skips 'dead' regimes (the 200-DMA gate lifted the momentum basket's honest Sharpe 0.63->0.98
and cut maxDD -44% -> -16%)."""
import pandas as pd


def dma_regime(nifty, period=200):
    """Hold when NIFTY > its N-day moving average at (or just before) the rebalance date."""
    sma = nifty.rolling(period).mean()
    def regime_fn(rd):
        idx = nifty.index[nifty.index <= rd]
        if len(idx) == 0:
            return True  # no data -> don't gate out (fail-open)
        d = idx[-1]
        v, s = nifty.get(d), sma.get(d)
        if pd.isna(v) or pd.isna(s):
            return True
        return bool(v > s)
    return regime_fn


def always_on():
    return lambda rd: True
