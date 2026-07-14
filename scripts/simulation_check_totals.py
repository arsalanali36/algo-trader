import csv
from collections import defaultdict

totals = defaultdict(float)
try:
    with open('C:\\Users\\arsal\\.gemini\\antigravity-ide\\brain\\a4fbb0f0-a076-40db-a03f-50ce119c468d\\trades_simulation.csv', 'r') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if not row or row[0] == "TOTAL": continue
            try:
                date = row[0]
                val = float(row[4])
                totals[date] += val
            except:
                pass
    for k, v in sorted(totals.items()):
        print(f"{k}: {v}")
except Exception as e:
    print(e)
