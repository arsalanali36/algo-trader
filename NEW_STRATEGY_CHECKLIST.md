
13. **Selection-time lookahead (LESSONS TRAP #190).** For every parameter the strategy
    CHOOSES — strike, symbol, expiry, size, direction, side — state the timestamp of the data
    used to choose it. It must be at or before the decision instant. Then sweep the decision
    lag and check performance is NOT monotonically better the earlier you decide; that ramp is
    lookahead, not edge. This is invisible to cost realism, significance tests and train/OOS.
