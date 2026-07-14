import sqlite3
try:
    db = sqlite3.connect('/root/ARSALAN/CODE3B- TV BACKTEST ENGINE/data/trades.db')
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT DISTINCT status FROM orders WHERE date >= '2026-06-22'").fetchall()
    print([r['status'] for r in rows])
except Exception as e:
    print(e)
