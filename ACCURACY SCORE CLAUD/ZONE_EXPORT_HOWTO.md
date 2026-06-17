# TradingView se Zone-bar data export karna (zone forensics ke liye)

Goal: har **zone formation** ka bar-time + type + high/low + kaunsi line touch hui —
taaki engine ke zones se exact compare karke remaining mismatches pinpoint ho sakein.
(List of Trades sirf entry/exit deta hai; zone bars nahi.)

## Step 1 — Pine mein yeh code ADD karo

Strategy ke andar, **zone formation wale block ke turant baad** (jahan
`Bullish_Candle_exitingZone` / `Bearish_Candle_exitingZone` use hote hain —
approx line 1051-1068, jahan `Green_Zone := ...` set hota hai), yeh paste karo:

```pinescript
// ===== ZONE EXPORT (forensics) — har zone formation log karo =====
if (Bullish_Candle_exitingZone or Bearish_Candle_exitingZone) and inDateRange
    string _zt = Bullish_Candle_exitingZone ? "GREEN" : "RED"
    log.info("ZONE " + _zt
         + " " + str.format_time(time, "yyyy-MM-dd HH:mm", "Asia/Kolkata")
         + " hi=" + str.tostring(high, "#.##")
         + " lo=" + str.tostring(low, "#.##")
         + " line=" + lineType)
```

Optional (touches bhi chahiye to) — touch wale block ke baad:
```pinescript
if touched and inDateRange
    log.info("TOUCH " + str.format_time(time, "yyyy-MM-dd HH:mm", "Asia/Kolkata")
         + " line=" + lineType + " px=" + str.tostring(selectedLine, "#.##"))
```

## Step 2 — Date range chhota rakho (logs limited hote hain)
Settings → Backtest Start/End ko **ek mahine** pe set karo pehle (e.g.
2026-01-01 .. 2026-02-01). Pine Logs sirf recent ~hazaar lines rakhta hai, isliye
poore 5 mahine ek saath mat lo — mahine-wise alag export better.

## Step 3 — Logs nikaalo
1. Pine Editor (neeche panel) → strategy chart pe add karo.
2. Pine Editor ke **"Pine Logs"** tab kholo (Editor ke top-right ya bottom panel).
3. Saare log lines **select + copy** karo.
4. Ek `.txt` file mein paste karke save karo, e.g.
   `ACCURACY SCORE CLAUD/zones_2026-01.txt`

## Step 4 — Mujhe do
Woh `zones_<month>.txt` file mujhe do. Format aisa dikhega:
```
ZONE RED   2026-01-13 12:40 hi=25714.60 lo=25688.00 line=CP
ZONE GREEN 2026-01-13 13:20 hi=25676.80 lo=25650.20 line=CP
TOUCH      2026-01-13 12:35 line=CP px=25692.00
```

Main isse engine ke zone trace (`--debug DATE`) se **bar-by-bar** compare karunga:
- kaunse zone TV banata hai jo engine nahi (ya alag bar pe) → exact bug
- phir zone-formation logic fix → 68% se 90%+ target

## Alternative (agar log.info se dikkat ho)
TradingView **"Export chart data"** (paid plans) — `plot(zone_Upper_line_price)`,
`plot(zone_Lower_line_price)`, aur ek numeric zone-flag `plot(Bullish_Candle_exitingZone ? 1 : Bearish_Candle_exitingZone ? -1 : 0)`
add karke chart data CSV export karo. Lekin `log.info` wala tarika zyada clean hai
(sirf zone bars, har bar nahi).
