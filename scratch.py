import sqlite3
c = sqlite3.connect('d:/KHAZANA/KHAZANA/PYTHON/CODE3B- TV BACKTEST ENGINE/data/trades.db')
c.row_factory=sqlite3.Row
r = c.execute("select id, trad_sym, side, group_id, correlation_id from orders where correlation_id like '%RANGE%' limit 10").fetchall()
for row in r:
    print(dict(row))
