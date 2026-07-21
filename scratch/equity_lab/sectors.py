"""NSE sector map for the F&O universe (v1, curated for the major liquid names).
Unmapped symbols fall to 'Other' via SECTOR_OF.get(sym, 'Other'). Refine as needed —
this feeds sector_rotation. Groupings follow common NSE sectoral-index buckets."""

_SECTORS = {
    "Bank": ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "INDUSINDBK",
             "BANKBARODA", "PNB", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK",
             "CANBK", "RBLBANK"],
    "IT": ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "PERSISTENT", "COFORGE",
           "MPHASIS", "LTTS", "OFSS"],
    "Auto": ["MARUTI", "M&M", "TATAMOTORS", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO",
             "TVSMOTOR", "ASHOKLEY", "BHARATFORG", "MOTHERSON", "BOSCHLTD", "BALKRISIND",
             "TIINDIA", "MRF", "APOLLOTYRE"],
    "Pharma": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "AUROPHARMA", "LUPIN",
               "TORNTPHARM", "ALKEM", "BIOCON", "ZYDUSLIFE", "GLENMARK", "LAURUSLABS",
               "MANKIND", "ABBOTINDIA", "GRANULES", "SYNGENE"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "TATACONSUM", "DABUR",
             "GODREJCP", "MARICO", "COLPAL", "VBL", "UBL", "PGHH"],
    "Metal": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "SAIL",
              "NMDC", "NATIONALUM", "HINDZINC", "APLAPOLLO", "JSL"],
    "Energy": ["RELIANCE", "ONGC", "NTPC", "POWERGRID", "COALINDIA", "IOC", "BPCL",
               "GAIL", "TATAPOWER", "ADANIGREEN", "ADANIENSOL", "NHPC", "OIL", "PETRONET",
               "IGL", "GUJGASLTD", "TORNTPOWER", "JSWENERGY"],
    "Finance": ["BAJFINANCE", "BAJAJFINSV", "SBILIFE", "HDFCLIFE", "ICICIPRULI",
                "ICICIGI", "SBICARD", "CHOLAFIN", "MUTHOOTFIN", "PFC", "RECLTD",
                "LICHSGFIN", "MANAPPURAM", "SHRIRAMFIN", "IRFC", "HUDCO", "ABCAPITAL",
                "LICI", "ANGELONE", "360ONE", "BSE", "CDSL", "MCX", "POLICYBZR", "PAYTM"],
    "Cement": ["ULTRACEMCO", "SHREECEM", "GRASIM", "AMBUJACEM", "ACC", "DALBHARAT",
               "RAMCOCEM", "JKCEMENT"],
    "Realty": ["DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "LODHA", "PHOENIXLTD"],
    "Infra": ["LT", "ADANIPORTS", "SIEMENS", "ABB", "BHEL", "CGPOWER", "POWERINDIA",
              "GVT&D", "NBCC", "RVNL", "IRCTC", "CONCOR", "GMRAIRPORT", "INDIGO",
              "CUMMINSIND", "THERMAX", "KEI", "POLYCAB", "HAVELLS", "SUPREMEIND"],
    "Chemical": ["PIDILITIND", "SRF", "PIIND", "UPL", "DEEPAKNTR", "AARTIIND",
                 "TATACHEM", "NAVINFLUOR", "ATUL", "SOLARINDS", "FLUOROCHEM", "COROMANDEL"],
    "Consumer": ["TITAN", "TRENT", "DMART", "ASIANPAINT", "BERGEPAINT", "PAGEIND",
                 "VOLTAS", "DIXON", "AMBER", "BLUESTARCO", "KALYANKJIL", "JUBLFOOD",
                 "NAUKRI", "INDHOTEL", "ZOMATO", "NYKAA", "KAYNES"],
    "Defence": ["HAL", "BEL", "MAZDOCK", "BDL", "COCHINSHIP", "SOLARINDS", "DATAPATTNS"],
    "Telecom": ["BHARTIARTL", "IDEA", "INDUSTOWER", "TATACOMM", "HFCL"],
    "PSU-Misc": ["GAIL", "SJVN", "IREDA", "MMTC", "RITES"],
}

SECTOR_OF = {}
for _sec, _syms in _SECTORS.items():
    for _s in _syms:
        SECTOR_OF.setdefault(_s, _sec)   # first wins (a few overlap, e.g. SOLARINDS)


def sector_counts(symbols):
    from collections import Counter
    return Counter(SECTOR_OF.get(s, "Other") for s in symbols)
