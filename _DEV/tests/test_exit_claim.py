import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "_core"))
import exit_claim as ec

fails = []
def ok(name, cond):
    print(("PASS " if cond else "FAIL ") + name); (fails.append(name) if not cond else None)

# ── Scenario 1: today's real 12:59 race — same strategy/contract/side, two engines ──
# engine A (basket GROUP_SL) fires first on 57300-CE BUY
a = ec.claim("straddle_alert_hedged", 59086, "BUY", "live")
# engine B (RMS_PROFIT_TARGET) fires ~1s later on the SAME leg
b = ec.claim("straddle_alert_hedged", 59086, "BUY", "live")
ok("engine A wins the 57300-CE BUY claim", a is True)
ok("engine B is BLOCKED (no duplicate 3627)", b is False)

# ── Scenario 2: today's real 15:10 cascade double-close of 57600-CE BUY ──
c = ec.claim("straddle_alert_hedged", 59092, "BUY", "live")
d = ec.claim("straddle_alert_hedged", 59092, "BUY", "live")   # 3664 duplicate
ok("57600-CE BUY first close wins", c is True)
ok("57600-CE BUY duplicate (3664) BLOCKED", d is False)

# ── Scenario 3: the OTHER short leg (PE) is a different contract → NOT blocked ──
ok("57300-PE BUY (different sec_id) allowed", ec.claim("straddle_alert_hedged", 59087, "BUY", "live") is True)

# ── Scenario 4: two DIFFERENT strategies, same contract+side → both legit ──
ok("rsi_v1 closes 57600-PE SELL", ec.claim("rsi_v1", 59093, "SELL", "paper") is True)
ok("banknifty_v1 closes 57600-PE SELL (diff strategy, NOT blocked)",
   ec.claim("banknifty_v1", 59093, "SELL", "paper") is True)

# ── Scenario 5: release on failed placement → immediate retry allowed ──
ec.claim("xstrat", 111, "BUY", "live")
ec.release("xstrat", 111, "BUY", "live")
ok("after release, retry wins again", ec.claim("xstrat", 111, "BUY", "live") is True)

# ── Scenario 6: short TTL → a genuine later re-close is NOT blocked forever ──
ec.claim("ystrat", 222, "SELL", "live", ttl=0.3)
time.sleep(0.35)
ok("after TTL expiry, re-close allowed", ec.claim("ystrat", 222, "SELL", "live", ttl=0.3) is True)

# ── Scenario 7: fail-open when leg can't be keyed (never strand an exit) ──
ok("missing sec_id → fail-open (proceed)", ec.claim("s", None, "BUY", "live") is True)
ok("missing side → fail-open (proceed)", ec.claim("s", 333, None, "live") is True)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
