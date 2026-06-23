import sqlite3
import os

db_path = os.path.join(r"d:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\data\trades.db")
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM orders")
    conn.commit()
    conn.close()
    print("All trades cleared from the database.")
else:
    print("Database not found.")
