import pandas as pd
import glob
import os
from range_trader import traditional_pivots

DATA_DIR = r'D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Index\NIFTY'
files = sorted(glob.glob(os.path.join(DATA_DIR, '*_*.csv')))
daily_rows = []
for f in files:
    date_str = os.path.basename(f).lower().replace('nifty_', '').replace('.csv', '')
    if date_str > '2026-04-29': 
        continue
    df = pd.read_csv(f)
    if not df.empty:
        df.columns = [c.lower() for c in df.columns]
        daily_rows.append({
            'date': date_str, 
            'high': float(df['high'].max()), 
            'low': float(df['low'].min()), 
            'close': float(df.iloc[-1]['close'])
        })

daily_df = pd.DataFrame(daily_rows)
prev = daily_df.iloc[-1]
ph, pl, pc = prev['high'], prev['low'], prev['close']
print(f"Previous Day (29 April) - H: {ph}, L: {pl}, C: {pc}")

piv = traditional_pivots(ph, pl, pc)
print(f"Pivot: {piv['P']:.2f}")
print(f"R1: {piv['R1']:.2f}, R2: {piv['R2']:.2f}, R3: {piv['R3']:.2f}")
print(f"S1: {piv['S1']:.2f}, S2: {piv['S2']:.2f}, S3: {piv['S3']:.2f}")

h_chain = []
l_chain = []
h_thresh = ph
for i in range(len(daily_df)-2, max(len(daily_df)-22, -1), -1):
    row_h = float(daily_df.iloc[i]['high'])
    if row_h > h_thresh:
        jump = (row_h - h_thresh) / h_thresh * 100
        if jump <= 10.0:
            h_chain.append(row_h)
            h_thresh = row_h

l_thresh = pl
for i in range(len(daily_df)-2, max(len(daily_df)-22, -1), -1):
    row_l = float(daily_df.iloc[i]['low'])
    if row_l < l_thresh:
        drop = (l_thresh - row_l) / l_thresh * 100
        if drop <= 10.0:
            l_chain.append(row_l)
            l_thresh = row_l

print(f"High Chain: {h_chain}")
print(f"Low Chain: {l_chain}")
