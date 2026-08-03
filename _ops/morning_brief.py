# -*- coding: utf-8 -*-
"""
morning_brief.py — subha ek-nazar market snapshot (display-only, Rule 10).

Kya deta hai (har section FAIL-SAFE — ek source mare to baaki chalte rahein,
brief partial data pe bhi ban jata hai; subha reliability > completeness):

  india   -> NIFTY / BANKNIFTY prev-session close + day-change + VIX
             SOURCE: option-chain collector lake (Rule 6B: `option_curves`
             reader reuse) — ZERO Dhan call, wahi lake jo /curves + /gex padhte.
  flows   -> FII/DII cash + index-fut net + daily PCR + max-pain
             SOURCE: `fii_flow_view.series()` (free NSE lake).
  gift    -> GIFT Nifty vs prev NIFTY close (Dhan/NSE) — VPS pe wire; abhi optional.
  crypto  -> BTC / ETH (CoinGecko, no key).
  news    -> top headlines, multi-RSS merge+dedup (ET/Livemint/MC/NDTV/Hindu BL).
  events  -> curated recurring calendar (RBI/Fed/CPI) + auto weekly-expiry flag.
  reddit  -> r/IndianStreetBets buzz (app-only OAuth) — creds config me daalte hi ON.
  bias    -> in sabse ek 1-line auto read.

NOTE: is module ka koi order/risk/live path se lena-dena NAHI (display-only).
Reddit/crypto/news external free sources — internet chahiye; na mile to graceful.
"""
import os
import re
import json
import time
import base64
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

# path bootstrap so flat imports (option_curves / fii_flow_view) resolve
try:
    import _paths  # noqa
except Exception:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        import _paths  # noqa
    except Exception:
        pass

IST = timezone(timedelta(hours=5, minutes=30))
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ------------------------------------------------------------------ tiny cache
# per-section TTL cache — brief din me 2-3 baar khulta, har baar live fetch bekaar.
_CACHE = {}   # key -> (expiry_ts, value)

def _cached(key, ttl, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = fn()
    # sirf successful fetch cache karo; fail ko short-cache (retry jaldi)
    ok = isinstance(val, dict) and val.get("ok")
    _CACHE[key] = (now + (ttl if ok else 30), val)
    return val

def _get(url, timeout=12, headers=None, data=None):
    h = {"User-Agent": _UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def _now_ist():
    return datetime.now(IST)

# =================================================================== CRYPTO
def _fetch_crypto():
    try:
        d = _get("https://api.coingecko.com/api/v3/simple/price"
                 "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true", timeout=20)
        j = json.loads(d)
        items = []
        for cid, nm in (("bitcoin", "Bitcoin"), ("ethereum", "Ethereum")):
            if cid in j:
                items.append({"name": nm, "price": j[cid]["usd"],
                              "chg_pct": round(j[cid].get("usd_24h_change", 0.0), 2)})
        return {"ok": bool(items), "items": items}
    except Exception as e:
        return {"ok": False, "err": str(e)[:120], "items": []}

def get_crypto():
    return _cached("crypto", 300, _fetch_crypto)

# =================================================================== NEWS
_RSS_FEEDS = (
    ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Livemint",       "https://www.livemint.com/rss/markets"),
    ("Moneycontrol",   "https://www.moneycontrol.com/rss/business.xml"),
    ("NDTV Profit",    "https://feeds.feedburner.com/ndtvprofit-latest"),
    ("Hindu BL",       "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
)

def _parse_rss(xml, src, cap=8):
    items = []
    for m in re.finditer(r"<item\b[^>]*>(.*?)</item>", xml, re.S | re.I):
        blk = m.group(1)

        def tag(t):
            mm = re.search(rf"<{t}\b[^>]*>(.*?)</{t}>", blk, re.S | re.I)
            if not mm:
                return ""
            v = mm.group(1)
            cd = re.search(r"<!\[CDATA\[(.*?)\]\]>", v, re.S)
            v = cd.group(1) if cd else v
            return re.sub(r"<[^>]+>", "", v).strip()

        title = tag("title")
        if not title:
            continue
        items.append({"src": src, "title": title[:160], "link": tag("link"), "pub": tag("pubDate")})
        if len(items) >= cap:
            break
    return items

def _pub_ts(s):
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(s.strip(), fmt).timestamp()
        except Exception:
            pass
    return 0.0

def _fetch_news(limit=8):
    got = []

    def one(feed):
        src, url = feed
        try:
            return _parse_rss(_get(url, timeout=10).decode("utf-8", "ignore"), src)
        except Exception:
            return []
    try:
        with ThreadPoolExecutor(max_workers=5) as ex:
            for r in ex.map(one, _RSS_FEEDS):
                got.extend(r)
    except Exception as e:
        return {"ok": False, "err": str(e)[:120], "items": []}
    seen, uniq = set(), []
    for it in sorted(got, key=lambda x: _pub_ts(x["pub"]), reverse=True):
        k = it["title"].lower()[:60]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    return {"ok": bool(uniq), "items": uniq[:limit], "total_fetched": len(got)}

def get_news(limit=8):
    return _cached("news", 300, lambda: _fetch_news(limit))

# =================================================================== REDDIT (plug-ready)
_RTOK = {"tok": None, "exp": 0.0}

def _reddit_token(cid, secret):
    if _RTOK["tok"] and _RTOK["exp"] > time.time() + 30:
        return _RTOK["tok"]
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    d = _get("https://www.reddit.com/api/v1/access_token",
             headers={"Authorization": f"Basic {auth}",
                      "Content-Type": "application/x-www-form-urlencoded",
                      "User-Agent": "khazana-brief/0.1"},
             data=body, timeout=12)
    j = json.loads(d)
    _RTOK["tok"] = j["access_token"]
    _RTOK["exp"] = time.time() + int(j.get("expires_in", 3600))
    return _RTOK["tok"]

def _fetch_reddit(cid, secret, sub, limit):
    try:
        tok = _reddit_token(cid, secret)
        d = _get(f"https://oauth.reddit.com/r/{sub}/hot?limit={limit + 3}&raw_json=1",
                 headers={"Authorization": f"Bearer {tok}", "User-Agent": "khazana-brief/0.1"},
                 timeout=12)
        j = json.loads(d)
        out = []
        for c in j["data"]["children"]:
            p = c["data"]
            if p.get("stickied"):
                continue
            out.append({"title": p["title"][:140], "score": p.get("ups", 0),
                        "comments": p.get("num_comments", 0),
                        "link": "https://reddit.com" + p.get("permalink", "")})
            if len(out) >= limit:
                break
        return {"ok": True, "items": out, "sub": sub}
    except Exception as e:
        return {"ok": False, "err": str(e)[:120], "items": []}

def get_reddit_buzz(cid=None, secret=None, sub="IndianStreetBets", limit=5):
    if not cid or not secret:
        return {"ok": False, "err": "reddit-not-configured", "items": []}
    return _cached(f"reddit:{sub}", 600, lambda: _fetch_reddit(cid, secret, sub, limit))

# =================================================================== EVENTS
def _load_curated_events():
    """User-maintained JSON: data/brief_events.json = [{date,what,imp,country?}].
    File na ho to khaali — auto expiry-day phir bhi add hoga."""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "brief_events.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def get_events(curated=None):
    now = _now_ist()
    today = now.date()
    evs = list(curated if curated is not None else _load_curated_events())
    # auto: is week ka Thursday = weekly F&O expiry
    thu = today + timedelta(days=(3 - today.weekday()) % 7)
    tstr = thu.isoformat()
    if not any(e.get("date") == tstr and e.get("flag") == "expiry" for e in evs):
        evs.append({"date": tstr, "what": "Weekly F&O Expiry (NIFTY)", "imp": "hi", "flag": "expiry"})
    horizon = (today + timedelta(days=30)).isoformat()
    tdy = today.isoformat()
    evs = [e for e in evs if e.get("date") and tdy <= e["date"] <= horizon]
    for e in evs:
        e["is_today"] = (e["date"] == tdy)
    evs.sort(key=lambda e: (e["date"], {"hi": 0, "md": 1, "lo": 2}.get(e.get("imp"), 3)))
    return {"ok": True, "items": evs}

# =================================================================== INDIA (lake, no Dhan)
_INDIA_SET = (("NIFTY", "NIFTY 50"), ("BANKNIFTY", "BANK NIFTY"))

def get_india():
    """NIFTY/BANKNIFTY prev-session close + day change + VIX from collector lake."""
    def _build():
        try:
            import option_curves as oc
        except Exception as e:
            return {"ok": False, "err": f"lake-reader-missing:{e}"[:120], "items": [], "vix": None}
        items, vix = [], None
        for u, label in _INDIA_SET:
            try:
                dates = oc.available_dates(u)
                if not dates:
                    continue
                last = oc.curves(u, dates[-1]).get("points") or []
                if not last:
                    continue
                close = last[-1].get("spot")
                if u == "NIFTY":
                    vix = last[-1].get("vix")
                prev = None
                if len(dates) >= 2:
                    p2 = oc.curves(u, dates[-2]).get("points") or []
                    if p2:
                        prev = p2[-1].get("spot")
                chg = round(close - prev, 2) if (close is not None and prev) else None
                pct = round(chg / prev * 100, 2) if (chg is not None and prev) else None
                items.append({"name": label, "value": round(close, 2) if close else None,
                              "chg": chg, "chg_pct": pct, "date": dates[-1]})
            except Exception:
                continue
        return {"ok": bool(items), "items": items, "vix": round(vix, 2) if vix else None}
    return _cached("india", 120, _build)

# =================================================================== FLOWS (FII/DII + PCR)
def get_flows():
    def _build():
        try:
            import fii_flow_view as ff
            s = ff.series()
        except Exception as e:
            return {"ok": False, "err": f"fii-lake-missing:{e}"[:120]}
        rows = s.get("rows") or []
        if not rows:
            return {"ok": False, "err": "fii-lake-empty"}
        cols = s.get("cols") or []

        def col(row, name):
            try:
                return row[cols.index(name)]
            except Exception:
                return None
        last = rows[-1]
        return {"ok": True, "date": last[0],
                "fii_cash": col(last, "fii_cash"),
                "dii_cash": col(last, "dii_cash"),
                "fii_fut": col(last, "fii_fut"),
                "pcr": col(last, "pcr"),
                "max_pain": col(last, "max_pain"),
                "spot": col(last, "spot")}
    return _cached("flows", 300, _build)

# =================================================================== GIFT (VPS/Dhan wire)
def get_gift(prev_nifty_close=None):
    """GIFT Nifty vs prev NIFTY close. Dhan/NSE-IX — VPS pe wire hoga.
    Abhi optional: creds/feed na ho to graceful skip."""
    return {"ok": False, "err": "gift-wire-on-vps", "value": None, "gap": None}

# =================================================================== BIAS (auto 1-line)
def _bias(india, flows, crypto, gift, events):
    bits, tone = [], "neutral"
    # gap read
    if gift.get("ok") and gift.get("gap") is not None:
        g = gift["gap"]
        arrow = "up" if g > 0 else ("flat" if abs(g) < 15 else "down")
        bits.append(f"GIFT Nifty {'+' if g>=0 else ''}{g} vs close")
        tone = "positive" if g > 15 else ("negative" if g < -15 else "neutral")
    # flows
    if flows.get("ok"):
        fc = flows.get("fii_cash")
        if fc is not None:
            side = "sold" if fc < 0 else "bought"
            bits.append(f"FII {side} ₹{abs(fc):,.0f} cr")
            if fc < -1500 and tone != "positive":
                tone = "negative"
    # vix
    v = india.get("vix")
    if v is not None:
        bits.append(f"India VIX {v}")
    # today's high-impact event
    tev = next((e for e in events.get("items", []) if e.get("is_today") and e.get("imp") == "hi"), None)
    if tev:
        bits.append(f"⚠ {tev['what']} today")
    label = {"positive": "\U0001f4c8 POSITIVE cues", "negative": "\U0001f4c9 CAUTIOUS open",
             "neutral": "⚖️ MIXED cues"}[tone]
    return {"tone": tone, "label": label, "text": " · ".join(bits) if bits else "Data aa raha hai…"}

# =================================================================== ASSEMBLE
def _reddit_creds():
    """nifty_config.json._morning_brief.reddit se ya env se creds."""
    cid = os.environ.get("REDDIT_CLIENT_ID")
    sec = os.environ.get("REDDIT_CLIENT_SECRET")
    if cid and sec:
        return cid, sec
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nifty_config.json")
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
        r = (c.get("_morning_brief") or {}).get("reddit") or {}
        return r.get("client_id"), r.get("client_secret")
    except Exception:
        return None, None

def build_brief():
    india = get_india()
    flows = get_flows()
    crypto = get_crypto()
    news = get_news()
    events = get_events()
    gift = get_gift()
    cid, sec = _reddit_creds()
    reddit = get_reddit_buzz(cid, sec)
    bias = _bias(india, flows, crypto, gift, events)
    now = _now_ist()
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M IST"),
        "weekday": now.strftime("%A"),
        "bias": bias,
        "india": india, "flows": flows, "gift": gift,
        "crypto": crypto, "news": news, "events": events, "reddit": reddit,
    }

if __name__ == "__main__":
    t0 = time.time()
    b = build_brief()
    print(f"=== MORNING BRIEF built in {round(time.time()-t0,1)}s ===\n")
    print("BIAS  :", b["bias"]["label"], "|", b["bias"]["text"])
    print("INDIA :", b["india"]["ok"], b["india"].get("err", ""), b["india"].get("items"))
    print("FLOWS :", b["flows"]["ok"], b["flows"].get("err", ""),
          {k: b["flows"].get(k) for k in ("fii_cash", "dii_cash", "pcr", "max_pain")} if b["flows"]["ok"] else "")
    print("CRYPTO:", b["crypto"]["ok"], b["crypto"].get("items"))
    print("NEWS  :", b["news"]["ok"], f"({b['news'].get('total_fetched')} fetched)")
    for n in b["news"]["items"][:5]:
        print(f"    [{n['src']:14}] {n['title'][:66]}")
    print("EVENTS:", [f"{e['date']}{'*' if e.get('is_today') else ''}:{e['what']}" for e in b["events"]["items"][:5]])
    print("REDDIT:", b["reddit"]["ok"], b["reddit"].get("err", ""))
    print("GIFT  :", b["gift"]["ok"], b["gift"].get("err", ""))
