"""
atm_straddle_roller.py — ATM straddle AUTO-ROLLER (see _ADR/ADR-004 roller spec).

Ek deployed ATM short-straddle ko tab-hi ROLL karta hai jab spot ~1 strike shift
ho jaaye — par sirf JAB ZAROORI HO, har tick pe nahi. 6 rules ek priority-order me
gate karte hain (ADR-004 roller); koi ek fail → roll skip (reason ke saath log).

Design (auto_straddle.py ka mirror — Rule 6B, wahi patterns reuse):
  • PURE state + decision — `RollerState` (disk-persist) + `should_roll()` me KOI
    broker/order/Dhan import nahi → standalone-testable (neeche __main__ self-test).
  • Money-path (`execute_roll`) `execution_gateway.execute_signal/execute_exit` ke
    through hi jaata hai — koi raw order nahi (Rule 6/6B, ADR-001). Exit PEHLE, phir
    naya ATM enter (ADR: "Do NOT roll both legs simultaneously — exit first, then enter").
  • KOI NAYA POLLING LOOP nahi (ADR: "Do NOT add a new polling loop"). `on_candle_close()`
    ko MAUJOODA candle/monitor cycle har 5-min candle pe call kare (jaise auto_straddle_loop
    ~3s pe chalta hai). Spot + premiums MAUJOODA `shared_ltp_cache` se aate hain (ltp_poller
    se warm) — ZERO extra Dhan call (ADR: "LTP source: existing shared_ltp_cache").
  • Bad/incomplete data (spot ya premium missing) → FREEZE, roll NAHI (TRAP #1 shape —
    kabhi adhoore data pe order fire nahi).

Config: `nifty_config.json["atm_straddle_roller"]` (ADR ka "strategy_config.json" is
repo me nifty_config.json hai — CLAUDE.md: saari config wahin). `load_config()` dekho.
Keys (ADR): enabled, confirmation_candles(3), cooldown_minutes(30),
premium_benefit_pct(0.70), start_time("09:30"), end_time("15:00"), max_rolls_per_day(3),
plus lots + mode (default "paper" — going live needs explicit config + PRE-MORTEM re-run).

State file: data/atm_straddle_roller.json → {"<SYMBOL>": {...day-scoped state...}}
Day-scoped: agle din khaali (intraday straddle; rolls_today/deployed sab reset).
Restart-safe: file hi source-of-truth for "aaj kitne roll" + "abhi kya deployed hai".
"""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# _ops/ ka parent = project root (data/ + nifty_config.json wahin)
_ROOT = Path(__file__).resolve().parent.parent
_FILE = _ROOT / "data" / "atm_straddle_roller.json"
_CONFIG_FILE = _ROOT / "nifty_config.json"
_FILE.parent.mkdir(exist_ok=True)

# ── charge-estimate constants (LOGGING ke liye; asli economic gate Rule 4 hai) ──
BROKERAGE_PER_ORDER = 20.0      # Zerodha flat ₹20/executed order
ROLL_ORDER_COUNT = 4            # 2 exit legs (buy-back) + 2 entry legs (fresh sell)
STT_SELL_PCT = 0.15             # sell-side STT % (Budget-2026, matches charges.py)
EXCHANGE_CHARGE_RS = 8.0        # NSE txn + GST + SEBI + stamp, rough per-roll

_DEFAULT_CFG = {
    "enabled": False,
    "confirmation_candles": 3,       # Rule 2 — N consecutive 5-min candles
    "cooldown_minutes": 30,          # Rule 3 — min gap between rolls
    "premium_benefit_pct": 0.70,     # Rule 4 — skip if current >= 70% of new premium
    "start_time": "09:30",           # Rule 5 — no roll before this
    "end_time": "15:00",             # Rule 5 — no roll after this
    "max_rolls_per_day": 3,          # Rule 6 — hard daily cap
    "min_strike_distance": 50,       # Rule 1 — hard min ATM shift (NIFTY = 1 strike)
    "lots": 1,
    "mode": "paper",                 # default paper; live = explicit config change
    # initial-deploy: roller apna PEHLA straddle bhi khud kholta hai (self-contained
    # strategy), phir roll karta hai. Ek deploy/symbol/din (day-scoped guard).
    "symbols": ["NIFTY"],
    "entry_time": "09:20",           # initial straddle deploy window start (IST)
    "entry_window_min": 6,           # deploy allowed for this many min after entry_time
    # HEDGED (defined-risk): sell ATM CE+PE, BUY OTM wings this many strikes out.
    # 2026-07-31 — naked straddle needed ~₹10.6L (3-lot NIFTY, both legs full SPAN),
    # blew the paper cap; hedged basket is a fraction so it fits + is safer.
    "hedge": {"wing_strikes_nifty": 3, "wing_strikes_banknifty": 3},
}


# ─────────────────────────────────────────────────────────────────────────────
# time helpers (IST wall-clock; auto_straddle.py se same convention)
# ─────────────────────────────────────────────────────────────────────────────
def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _today_ist():
    return _ist_now().strftime("%Y-%m-%d")


def _parse_hm(s, default=(9, 30)):
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except Exception:
        return default


def _strike_of(tsym):
    """Best-effort strike int from a Dhan trad_sym (e.g. NIFTY-Jul2026-24050-CE).
    Wahi helper jo trader_dashboard._strike_of use karta hai."""
    try:
        return int(float(str(tsym).split("-")[-2]))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# config
# ─────────────────────────────────────────────────────────────────────────────
def load_config():
    """nifty_config['atm_straddle_roller'] defaults ke upar. mode default 'paper'."""
    cfg = dict(_DEFAULT_CFG)
    try:
        raw = json.loads(_CONFIG_FILE.read_text()) if _CONFIG_FILE.exists() else {}
        user = raw.get("atm_straddle_roller") or {}
        if isinstance(user, dict):
            cfg.update(user)
    except Exception:
        pass
    # mode ko sirf explicit "live" hi live banaye — kuch aur/missing = paper
    cfg["mode"] = "live" if str(cfg.get("mode", "paper")).lower() == "live" else "paper"
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# RollerState — disk-persisted, day-scoped, per-symbol slice of one JSON file
# ─────────────────────────────────────────────────────────────────────────────
class RollerState:
    """Ek symbol ke deployed straddle + roll-bookkeeping ka disk-persisted state.
    ADR-004 ke roller_state dict ko mirror karta hai + execution ke liye kuch aur
    fields (legs/lots/group_id/mode). Ek hi file me sab symbols key-wise (NIFTY/
    BANKNIFTY). Day badla → stale (reset).

    Fields (ADR):
      deployed_atm        — abhi deployed ATM strike (None = kuch deployed nahi)
      deployed_at         — deploy/roll ka IST timestamp string
      last_roll_ts        — aakhri roll ka epoch (Rule 3 cooldown math)
      rolls_today         — aaj kitne roll ho chuke (Rule 6)
      confirmation_count  — candidate_atm ke lagataar confirming candles (Rule 2)
      candidate_atm       — jo naya ATM confirm ho raha hai
      roll_log            — [{time, from, to, cost_est, premium_gain}]
    Execution ke liye extra:
      legs                — [{opt_type, side, sec_id, trad_sym, entry_price, qty}]
      lots, lot_size, group_id, mode, entry_credit
    """

    def __init__(self, symbol, path=None):
        self.symbol = str(symbol).upper()
        self._path = Path(path) if path else _FILE
        self.day = _today_ist()
        self.deployed_atm = None
        self.deployed_at = None
        self.last_roll_ts = None
        self.rolls_today = 0
        self.confirmation_count = 0
        self.candidate_atm = None
        self.roll_log = []
        self.legs = []
        self.lots = 1
        self.lot_size = 0
        self.group_id = ""
        self.mode = "paper"
        self.entry_credit = 0.0
        self.initial_deployed = False   # day-scoped: initial straddle deploy ho chuka?

    # ── serialization ──
    def to_dict(self):
        return {
            "day": self.day,
            "deployed_atm": self.deployed_atm,
            "deployed_at": self.deployed_at,
            "last_roll_ts": self.last_roll_ts,
            "rolls_today": self.rolls_today,
            "confirmation_count": self.confirmation_count,
            "candidate_atm": self.candidate_atm,
            "roll_log": self.roll_log,
            "legs": self.legs,
            "lots": self.lots,
            "lot_size": self.lot_size,
            "group_id": self.group_id,
            "mode": self.mode,
            "entry_credit": self.entry_credit,
            "initial_deployed": self.initial_deployed,
        }

    def _apply(self, d):
        self.day = d.get("day", self.day)
        self.deployed_atm = d.get("deployed_atm")
        self.deployed_at = d.get("deployed_at")
        self.last_roll_ts = d.get("last_roll_ts")
        self.rolls_today = int(d.get("rolls_today", 0) or 0)
        self.confirmation_count = int(d.get("confirmation_count", 0) or 0)
        self.candidate_atm = d.get("candidate_atm")
        self.roll_log = d.get("roll_log") or []
        self.legs = d.get("legs") or []
        self.lots = int(d.get("lots", 1) or 1)
        self.lot_size = int(d.get("lot_size", 0) or 0)
        self.group_id = d.get("group_id", "") or ""
        self.mode = str(d.get("mode", "paper") or "paper")
        self.entry_credit = float(d.get("entry_credit", 0.0) or 0.0)
        self.initial_deployed = bool(d.get("initial_deployed", False))

    def reset_day(self):
        """Fresh day — deployed straddle + all roll bookkeeping stale (intraday)."""
        self.day = _today_ist()
        self.deployed_atm = None
        self.deployed_at = None
        self.last_roll_ts = None
        self.rolls_today = 0
        self.confirmation_count = 0
        self.candidate_atm = None
        self.roll_log = []
        self.legs = []
        self.lots = 1
        self.lot_size = 0
        self.group_id = ""
        self.entry_credit = 0.0
        self.initial_deployed = False

    def mark_flat(self, reason="external"):
        """Deployed straddle ab open NAHI hai (SL/EOD/manual ne close kar diya) —
        roller ko flat maar do taaki wo band position ko roll (=re-open) na kare.
        initial_deployed TRUE rehta hai → aaj dobara deploy nahi (SL/EOD respect)."""
        self.deployed_atm = None
        self.legs = []
        self.candidate_atm = None
        self.confirmation_count = 0
        self.save()

    def load(self):
        """File se apna symbol-slice padho. Stored day != today → reset (EOD stale).
        Returns self."""
        try:
            data = json.loads(self._path.read_text())
            if isinstance(data, dict):
                slice_ = data.get(self.symbol)
                if isinstance(slice_, dict):
                    if slice_.get("day") == _today_ist():
                        self._apply(slice_)
                    else:
                        self.reset_day()   # stale day → fresh
        except Exception:
            pass   # missing/corrupt file → fresh state (defaults)
        return self

    def save(self):
        """Apna symbol-slice file me merge karke likho (baaki symbols preserve).
        Last-writer-wins plain read-modify-write — same as shared_ltp_cache (price/
        low-frequency state, perfect lock zaroori nahi)."""
        self.day = _today_ist()
        try:
            data = json.loads(self._path.read_text())
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data[self.symbol] = self.to_dict()
        try:
            self._path.write_text(json.dumps(data))
        except Exception:
            pass
        return self

    # ── mutations ──
    def mark_deployed(self, atm, legs, lots, lot_size, group_id="", entry_credit=0.0,
                      mode="paper", now_ts=None):
        """Jab pehli baar (ya kisi aur path se) straddle deploy ho, use roller me
        register karo taaki roller ab isse track+roll kar sake. (Roller khud PEHLA
        straddle nahi kholta — wo auto_straddle / manual deploy ka kaam hai; roller
        sirf deployed ko roll karta hai. Ye method wo bridge hai.)"""
        self.deployed_atm = int(atm)
        self.legs = legs or []
        self.lots = int(lots or 1)
        self.lot_size = int(lot_size or 0)
        self.group_id = group_id or ("STRADR_" + uuid.uuid4().hex[:8])
        self.entry_credit = float(entry_credit or 0.0)
        self.mode = str(mode or "paper")
        self.deployed_at = _ist_now().strftime("%Y-%m-%d %H:%M:%S")
        self.candidate_atm = None
        self.confirmation_count = 0
        self.initial_deployed = True
        self.save()
        return self

    def record_roll(self, from_atm, to_atm, new_legs, entry_credit, cost_est,
                    premium_gain, group_id, now_ts=None):
        """Ek successful roll ke baad state advance karo: naya deployed ATM/legs,
        cooldown clock, rolls_today++, confirmation reset, roll_log append."""
        ts = now_ts if now_ts is not None else time.time()
        self.deployed_atm = int(to_atm)
        self.legs = new_legs or []
        self.entry_credit = float(entry_credit or 0.0)
        self.group_id = group_id or self.group_id
        self.deployed_at = _ist_now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_roll_ts = ts
        self.rolls_today = int(self.rolls_today) + 1
        self.confirmation_count = 0
        self.candidate_atm = None
        self.roll_log.append({
            "time": _ist_now().strftime("%Y-%m-%d %H:%M:%S"),
            "from": int(from_atm) if from_atm is not None else None,
            "to": int(to_atm),
            "cost_est": round(float(cost_est), 2),
            "premium_gain": round(float(premium_gain), 2),
        })
        self.save()
        return self

    def observe_candle(self, current_atm):
        """Per-candle confirmation bookkeeping (Rule 2 ka counter YAHAN advance hota
        hai — should_roll pure rehta hai, sirf padhta hai). ADR: "N CONSECUTIVE
        5-min candles". Har candle pe:
          • current_atm == deployed_atm  → koi shift nahi, candidate clear (count=0)
          • current_atm == candidate_atm → +1 (lagataar confirming)
          • warna                        → naya candidate, count=1 (pehla candle)
        Mutates + saves. (ye candle-tick semantics hai, isliye on_candle_close me,
        pure should_roll me nahi.)"""
        if self.deployed_atm is None:
            self.candidate_atm = None
            self.confirmation_count = 0
        elif int(current_atm) == int(self.deployed_atm):
            self.candidate_atm = None
            self.confirmation_count = 0
        elif self.candidate_atm is not None and int(current_atm) == int(self.candidate_atm):
            self.confirmation_count += 1
        else:
            self.candidate_atm = int(current_atm)
            self.confirmation_count = 1
        self.save()
        return self.confirmation_count


# ─────────────────────────────────────────────────────────────────────────────
# should_roll — PURE decision, 6 rules in ADR priority order
# ─────────────────────────────────────────────────────────────────────────────
def should_roll(state, current_atm, now=None, now_ts=None,
                current_straddle_value=None, new_atm_premium=None, cfg=None):
    """Kya abhi roll karna chahiye? 6 rules PRIORITY ORDER (ADR-004) me — pehla jo
    fail kare, wahi (False, reason). Sab pass → (True, reason). PURE: state ko sirf
    padhta hai (mutate nahi), koi broker/IO nahi → standalone-testable.

    Params:
      state                   — RollerState (deployed_atm/candidate/count/rolls read)
      current_atm             — live spot ka current ATM strike (int)
      now                     — IST datetime (Rule 5 time-of-day gate); default ab
      now_ts                  — epoch float (Rule 3 cooldown math); default time.time()
      current_straddle_value  — deployed legs ka abhi ka combined premium (Rule 4)
      new_atm_premium         — naye ATM straddle ka abhi ka combined premium (Rule 4)
      cfg                     — load_config() dict; None → load
    Returns (bool, reason_string).
    """
    cfg = cfg or load_config()
    now = now or _ist_now()
    now_ts = now_ts if now_ts is not None else time.time()

    if state.deployed_atm is None:
        return False, "no deployed straddle to roll"
    try:
        current_atm = int(current_atm)
    except (TypeError, ValueError):
        return False, "current_atm invalid — freeze"

    min_distance = int(cfg.get("min_strike_distance", 50))
    conf_needed = int(cfg.get("confirmation_candles", 3))
    cooldown_min = float(cfg.get("cooldown_minutes", 30))
    benefit_pct = float(cfg.get("premium_benefit_pct", 0.70))
    max_rolls = int(cfg.get("max_rolls_per_day", 3))
    sh, sm = _parse_hm(cfg.get("start_time", "09:30"), (9, 30))
    eh, em = _parse_hm(cfg.get("end_time", "15:00"), (15, 0))

    # ── Rule 1 — Minimum Strike Distance (HARD gate, non-negotiable) ──
    dist = abs(current_atm - int(state.deployed_atm))
    if dist < min_distance:
        return False, f"rule1: strike distance {dist} < {min_distance} — no roll"

    # ── Rule 2 — Confirmation filter (anti-whipsaw): N consecutive candles ──
    if state.candidate_atm is None or int(state.candidate_atm) != current_atm:
        return False, (f"rule2: new ATM {current_atm} not yet the confirming candidate "
                       f"({state.candidate_atm}) — 0/{conf_needed}")
    if int(state.confirmation_count) < conf_needed:
        return False, (f"rule2: ATM {current_atm} confirmed "
                       f"{state.confirmation_count}/{conf_needed} candles")

    # ── Rule 3 — Cooldown: min minutes since last roll ──
    if state.last_roll_ts:
        elapsed_min = (now_ts - float(state.last_roll_ts)) / 60.0
        if elapsed_min < cooldown_min:
            return False, (f"rule3: cooldown — {elapsed_min:.1f} min since last roll "
                           f"< {cooldown_min:.0f} min")

    # ── Rule 4 — Premium benefit: skip if current still holds >= pct of new premium ──
    # Bad/missing premium data → FREEZE (never roll on incomplete data — TRAP #1 shape).
    try:
        cur_v = float(current_straddle_value) if current_straddle_value is not None else None
        new_v = float(new_atm_premium) if new_atm_premium is not None else None
    except (TypeError, ValueError):
        cur_v = new_v = None
    if cur_v is None or new_v is None or cur_v <= 0 or new_v <= 0:
        return False, "rule4: premium data missing/invalid — freeze (no roll on bad data)"
    if cur_v >= benefit_pct * new_v:
        return False, (f"rule4: current straddle {cur_v:.1f} >= {benefit_pct:.0%} of "
                       f"new ATM {new_v:.1f} ({benefit_pct * new_v:.1f}) — not worth charges")

    # ── Rule 5 — Time of day gate ──
    tod = (now.hour, now.minute)
    if tod < (sh, sm):
        return False, f"rule5: before roll window start {sh:02d}:{sm:02d}"
    if tod > (eh, em):
        return False, f"rule5: after roll window end {eh:02d}:{em:02d}"

    # ── Rule 6 — Max rolls per day (hard cap) ──
    if int(state.rolls_today) >= max_rolls:
        return False, f"rule6: max rolls/day {max_rolls} reached"

    return True, (f"all 6 rules pass — roll {state.deployed_atm}→{current_atm} "
                  f"(dist {dist}, roll #{state.rolls_today + 1}/{max_rolls})")


# ─────────────────────────────────────────────────────────────────────────────
# charge estimate (LOGGING only — Rule 4 %-benefit is the real economic gate)
# ─────────────────────────────────────────────────────────────────────────────
def estimate_roll_cost(new_atm_premium, qty, cfg=None):
    """Ek roll ka approx cost (₹): brokerage (4 orders) + sell-side STT (naya straddle)
    + exchange charges. Sirf log-comparison ke liye (gain vs cost). Real economic
    decision Rule 4 (%-benefit) karta hai — ye number roll_log me visibility ke liye."""
    cfg = cfg or {}
    n_orders = int(cfg.get("roll_order_count", ROLL_ORDER_COUNT))
    brokerage = BROKERAGE_PER_ORDER * n_orders
    stt = 0.0
    try:
        prem = float(new_atm_premium or 0)
        q = int(qty or 0)
        if prem > 0 and q > 0:
            stt = (STT_SELL_PCT / 100.0) * prem * q
    except (TypeError, ValueError):
        pass
    return round(brokerage + stt + EXCHANGE_CHARGE_RS, 2)


# ─────────────────────────────────────────────────────────────────────────────
# price helper — MAUJOODA shared_ltp_cache se (ZERO extra Dhan call, no new polling)
# ─────────────────────────────────────────────────────────────────────────────
def _default_price_fn(sec_id, max_age=20.0):
    """Ek option ka premium shared_ltp_cache se (ltp_poller se warm). None on miss —
    caller FREEZE kare (Rule 4 bad-data guard)."""
    try:
        import shared_ltp_cache as slc
        v = slc.get(str(sec_id), max_age=max_age)
        return float(v) if v else None
    except Exception:
        return None


def _combined_premium(legs, price_fn):
    """Legs ka combined premium (Σ price). Koi bhi leg ka price missing/<=0 → None
    (freeze — auto_straddle.net_credit ki tarah, adhoore structure pe kabhi act nahi)."""
    total = 0.0
    for lg in (legs or []):
        p = price_fn(lg.get("sec_id"))
        if not p or p <= 0:
            return None
        total += float(p)
    return round(total, 2) if legs else None


# ─────────────────────────────────────────────────────────────────────────────
# still-open reconcile — deployed straddle abhi bhi broker pe open hai? (SL/EOD guard)
# ─────────────────────────────────────────────────────────────────────────────
def verify_still_open(state, log=print):
    """True agar deployed straddle ke SELL legs abhi bhi genuinely open hain.
    order_store (broker_sync._my_open_qty) se confirm karta hai — agar SAARE SELL
    legs confident-flat (==0: closed round-trip recorded) → state.mark_flat() +
    False. Ye ZAROORI hai: pos_monitor ka SL/EOD-squareoff straddle ko band kar
    sakta hai jabki RollerState abhi bhi 'deployed' dikhata → uske bina roller us
    band position ko 'roll' (=naya straddle re-open) kar deta, SL/EOD ke turant baad.
    (auto_straddle.reconcile_open ka same self-heal, TRAP #62 class.)
    Uncertain (None) / koi >0 → open maano (conservative — false-flat pe re-open se
    false-hold behtar). No legs → trust the record (can't verify)."""
    if state.deployed_atm is None:
        return False
    legs = [lg for lg in (state.legs or []) if str(lg.get("side", "")).upper() == "SELL"]
    if not legs:
        return True
    try:
        import broker_sync
    except Exception:
        return True   # can't verify → trust record
    verdicts = []
    for lg in legs:
        try:
            verdicts.append(broker_sync._my_open_qty(
                str(state.group_id or ""), str(lg.get("sec_id") or ""), lg.get("trad_sym") or ""))
        except Exception:
            verdicts.append(None)
    if all(v == 0 for v in verdicts):
        log(f"[ROLLER] {state.symbol} deployed straddle confirmed FLAT in order_store "
            f"(SL/EOD/manual close) — marking flat, no re-open today")
        state.mark_flat("reconciled-flat")
        return False
    return True   # any >0 or uncertain → still open


# ─────────────────────────────────────────────────────────────────────────────
# hedged-straddle entry — shared by deploy_initial + execute_roll (Rule 6B, mirrors
# _fire_auto_straddle's hedge-first pattern). DEFINED-RISK: sell ATM CE+PE + BUY 2
# OTM wings; gate the WHOLE structure ONCE (RMS gating + basket-margin) then place
# BUY wings FIRST, then SELL ATM (gate=False — basket already vetted). Unwind-safe.
# ─────────────────────────────────────────────────────────────────────────────
def _hedge_wing_offset(cfg, symbol):
    """OTM wing distance in strikes for the defined-risk hedge BUY legs. Per-symbol
    override via cfg['hedge']['wing_strikes_{nifty|banknifty}'], else shared default 3."""
    h = (cfg or {}).get("hedge") or {}
    sym = str(symbol).upper()
    key = "wing_strikes_banknifty" if sym == "BANKNIFTY" else "wing_strikes_nifty"
    try:
        return max(1, int(h.get(key, h.get("wing_strikes", 3))))
    except Exception:
        return 3


def _enter_hedged_straddle(symbol, spot, lots, gid, sid, mode, cfg, log, price_fn):
    """Place a fresh HEDGED short straddle (used by deploy_initial + roll re-enter).
    Returns (ok, atm_or_reason, legs, credit, lot_size). legs ordered SELL-first
    ([SELL CE, SELL PE, BUY wing CE, BUY wing PE]) so exit/roll (which iterates
    state.legs) closes every leg. quote_fn intentionally NOT passed to the wing
    resolver: on a cold cache compute_hedge_target's premium-walk drifts to the far
    end (near-zero protection) — a deterministic min_strikes-floor wing is the safe,
    tight, cold-cache-proof choice. All orders via execution_gateway (Rule 6/6B)."""
    import dhan_master
    import execution_gateway as gw
    import strategy_safety as _ss
    import risk_gate as rg
    ce_sec, ce_tsym, lot = dhan_master.get_option_contract(symbol, spot, "CE", 0)
    pe_sec, pe_tsym, lot2 = dhan_master.get_option_contract(symbol, spot, "PE", 0)
    if not ce_sec or not pe_sec:
        return False, f"{symbol} ATM contract resolve fail", [], 0, 0
    lot_size = int(lot or lot2 or 0)
    if lot_size < 1:
        return False, f"{symbol} lot size resolve nahi hua", [], 0, 0
    atm = _strike_of(ce_tsym)
    q = int(lots) * lot_size
    wing_off = _hedge_wing_offset(cfg, symbol)

    # ── resolve BOTH OTM wings (deterministic min_strikes-floor). fail → NO naked ──
    hedges = []
    for ot in ("CE", "PE"):
        try:
            hsec, htsym, hlot = _ss.compute_hedge_target(
                sid, symbol, spot, ot, 0, quote_fn=None,
                min_strikes_override=wing_off, max_premium_override=None,
                max_search=1, log=log)
        except Exception as he:
            hsec = None
            log(f"[ROLLER] {symbol} {ot} wing resolve err: {he}")
        if not hsec:
            return False, f"{ot} hedge wing resolve fail — no naked straddle", [], 0, 0
        hedges.append({"opt_type": ot, "sec": str(hsec), "tsym": htsym, "lot": int(hlot or lot_size)})

    # ── gate the WHOLE structure ONCE (RMS gating + basket-margin capital) ──
    try:
        blocked, why, _hard = rg.gating_status(sid, mode=mode)
        if blocked:
            return False, f"RMS blocked — {why}", [], 0, 0
    except Exception:
        pass
    basket_rows = [
        {"sec_id": str(ce_sec), "entry": "SELL", "qty": q, "entry_price": price_fn(str(ce_sec)) or 0, "sym": ce_tsym, "segment": "NSE_FNO"},
        {"sec_id": str(pe_sec), "entry": "SELL", "qty": q, "entry_price": price_fn(str(pe_sec)) or 0, "sym": pe_tsym, "segment": "NSE_FNO"},
    ]
    for h in hedges:
        basket_rows.append({"sec_id": h["sec"], "entry": "BUY", "qty": q,
                            "entry_price": price_fn(h["sec"]) or 0, "sym": h["tsym"], "segment": "NSE_FNO"})
    try:
        basket = rg.position_margin(basket_rows)   # single margin gate (hedged basket)
        ok_cap, cap_why = rg.check_capital_needed(sid, basket, mode=mode)
        if not ok_cap:
            return False, f"basket margin ₹{basket:,.0f} fit nahi hua — {cap_why}", [], 0, 0
    except Exception as _ce:
        log(f"[ROLLER] {symbol} basket capital check err: {_ce}")

    # ── place: BUY wings FIRST (margin reduced), then SELL ATM. unwind-safe. ──
    placed = []

    def _unwind(reason):
        for p in reversed(placed):
            try:
                gw.execute_exit(sid, symbol, p["sec"], p["tsym"], q, entry_side=p["side"],
                                mode=mode, group_id=gid, reason=reason,
                                tag="STRADDLE_ROLLER", source="straddle_roller", log=log)
            except Exception as ue:
                log(f"[ROLLER] {symbol} unwind FAIL {p['tsym']}: {ue}")

    hlegs = []
    for h in hedges:
        hres = gw.execute_signal(sid, symbol, "BUY", int(lots), h["lot"], h["sec"], h["tsym"],
                                 seg="NSE_FNO", mode=mode, source="straddle_roller",
                                 tag="STRADDLE_ROLLER_HEDGE", group_id=gid, gate=False,
                                 extra_tags=["STRADDLE_ROLLER", "HEDGE"], log=log)
        if not hres.get("ok"):
            log(f"[ROLLER] {symbol} {h['opt_type']} hedge BUY fail "
                f"({hres.get('reason') or hres.get('status')}) — unwinding, abort")
            _unwind("ROLLER_ABORT")
            return False, f"hedge {h['opt_type']} BUY fail — abort (no naked)", [], 0, 0
        placed.append({"sec": h["sec"], "tsym": h["tsym"], "side": "BUY"})
        hlegs.append({"opt_type": h["opt_type"], "side": "BUY", "sec_id": h["sec"],
                      "trad_sym": h["tsym"], "entry_price": hres.get("price") or 0, "qty": q})

    slegs = []
    for (sec, tsym, ot) in ((str(ce_sec), ce_tsym, "CE"), (str(pe_sec), pe_tsym, "PE")):
        res = gw.execute_signal(sid, symbol, "SELL", int(lots), lot_size, sec, tsym,
                                seg="NSE_FNO", mode=mode, source="straddle_roller",
                                tag="STRADDLE_ROLLER", group_id=gid, gate=False,
                                extra_tags=["STRADDLE_ROLLER"], log=log)
        if not res.get("ok"):
            log(f"[ROLLER] {symbol} {ot} SELL fail "
                f"({res.get('reason') or res.get('status')}) — unwinding, abort (no naked)")
            _unwind("ROLLER_ABORT")
            return False, f"{ot} SELL fail — abort (no naked)", [], 0, 0
        placed.append({"sec": sec, "tsym": tsym, "side": "SELL"})
        slegs.append({"opt_type": ot, "side": "SELL", "sec_id": sec, "trad_sym": tsym,
                      "entry_price": res.get("price") or 0, "qty": q})

    legs = slegs + hlegs   # SELL legs first (exit/roll iterates all)
    credit = round(sum(l["entry_price"] for l in slegs), 2)
    return True, atm, legs, credit, lot_size


# ─────────────────────────────────────────────────────────────────────────────
# deploy_initial — roller apna PEHLA ATM straddle khud kholta hai (self-contained)
# ─────────────────────────────────────────────────────────────────────────────
def deploy_initial(state, symbol, spot, lots, cfg=None, mode="paper", log=print):
    """Sell ATM CE + PE (fresh short straddle) — roller ka initial deploy. Legs
    execution_gateway se (gate=True → poora RMS: capital/liquidity/max-premium +
    default per-instrument SL tags; pos_monitor phir SL/EOD sambhaalta, ADR note).
    Unwind-safe: doosra leg fail → pehla turant unwind (poori tarah flat, koi naked
    short nahi). NAKED ATM straddle (ADR-004 roller = plain straddle; hedge ADR me
    nahi — chaho to follow-up). PAPER default. Returns (ok, msg)."""
    cfg = cfg or load_config()
    mode = str(mode or "paper")
    sid = str(cfg.get("strategy_id") or "atm_straddle_roller")
    gid = "STRADR_" + uuid.uuid4().hex[:8]
    ok, info, legs, credit, lot_size = _enter_hedged_straddle(
        symbol, spot, int(lots or 1), gid, sid, mode, cfg, log, _default_price_fn)
    if not ok:
        # attempt done → no re-fire storm within the window (skip rest of window/day)
        state.initial_deployed = True
        state.save()
        return False, f"initial deploy aborted — {info}"
    atm = info
    state.mark_deployed(atm, legs, int(lots or 1), lot_size, group_id=gid,
                        entry_credit=credit, mode=mode)
    log(f"[ROLLER] {symbol} INITIAL hedged straddle @ credit {credit:.1f} "
        f"(ATM {atm}, {int(lots or 1)} lot, +2 wings) — now tracking for rolls")
    try:
        import notify
        notify.push(f"🔄 {symbol} Auto-Rolling hedged straddle deployed @ credit {credit:.0f} "
                    f"(ATM {atm})", level="info", key="roller_deploy_%s" % gid, source="chain")
    except Exception:
        pass
    return True, f"[{mode.upper()}] {symbol} roller hedged straddle @ {credit:.1f} (ATM {atm})"


# ─────────────────────────────────────────────────────────────────────────────
# execute_roll — exit current legs, THEN enter new ATM legs (via execution_gateway)
# ─────────────────────────────────────────────────────────────────────────────
def execute_roll(state, symbol, new_atm, new_ce, new_pe, lots, lot_size,
                 current_straddle_value=None, new_atm_premium=None,
                 mode="paper", cfg=None, log=print, price_fn=None):
    """Deployed straddle ko new ATM pe ROLL karo. ADR: exit PEHLE, phir enter.
      new_ce / new_pe = (sec_id, trad_sym) naye ATM CE/PE contracts (caller ne
      dhan_master se resolve kiye — roller khud resolve kar sakta hai par on_candle_close
      pehle hi resolve karta hai premium ke liye, isliye pass-through).
    Order path SIRF execution_gateway se (Rule 6/6B, ADR-001). Naked-exposure guard:
    agar naye 2 legs me se ek fail ho → doosri naya leg turant unwind (poori tarah flat
    rehna naked short se behtar). Success pe state.record_roll(). Returns result dict."""
    import execution_gateway as gw

    cfg = cfg or load_config()
    price_fn = price_fn or _default_price_fn
    mode = str(mode or "paper")
    sid = str(cfg.get("strategy_id") or "atm_straddle_roller")
    from_atm = state.deployed_atm
    q = int(lots) * int(lot_size or 1)
    ce_sec, ce_tsym = str(new_ce[0]), new_ce[1]
    pe_sec, pe_tsym = str(new_pe[0]), new_pe[1]
    # ONE session group_id for the WHOLE roller life — all rolls (exit old + enter
    # new) share the gid deploy_initial set. So the rolled-out strikes stay in the
    # SAME position while running, and the whole chain lands in Completed only ONCE
    # (when fully squared off) — every rolled strike + total tax together, not one
    # completed trade per roll (user 2026-07-30). Fallback gid only if state has none.
    new_gid = state.group_id or ("STRADR_" + uuid.uuid4().hex[:8])

    log(f"[ROLLER] {symbol} ROLL start {from_atm}→{new_atm} "
        f"(lots {lots}, qty {q}, mode {mode})")

    # ── 1. EXIT current deployed legs (buy back the short straddle) ──
    old_legs = list(state.legs or [])
    for leg in old_legs:
        try:
            res = gw.execute_exit(
                sid, symbol, leg.get("sec_id"), leg.get("trad_sym"),
                int(leg.get("qty", q)), entry_side=leg.get("side", "SELL"),
                mode=mode, group_id=state.group_id, reason="ROLLER_ROLL_EXIT",
                tag="STRADDLE_ROLLER", source="straddle_roller", log=log)
            log(f"[ROLLER] {symbol} exit {leg.get('trad_sym')} → "
                f"{res.get('status')} ({res.get('reason', '')})")
        except Exception as e:
            log(f"[ROLLER] {symbol} exit leg fail {leg.get('trad_sym')}: {e}")

    # ── 2. ENTER new HEDGED straddle at the new ATM (basket-gated, unwind-safe) ──
    ok, info, new_legs, entry_credit, _ls = _enter_hedged_straddle(
        symbol, int(new_atm), int(lots), new_gid, sid, mode, cfg, log, price_fn)
    if not ok:
        log(f"[ROLLER] {symbol} roll re-enter fail ({info}) — flat, no naked leg")
        state.legs = []          # exited old + nothing re-entered → genuinely flat
        state.deployed_atm = None
        state.save()
        return {"ok": False, "status": "aborted",
                "reason": f"roll re-enter fail — flat (no naked): {info}"}

    # ── 3. bookkeeping — cost estimate vs premium gain (ADR "log karo har roll pe") ──
    cost_est = estimate_roll_cost(new_atm_premium or entry_credit, q, cfg)
    prem_gain_pts = None
    if new_atm_premium is not None and current_straddle_value is not None:
        prem_gain_pts = float(new_atm_premium) - float(current_straddle_value)
    prem_gain_rs = round((prem_gain_pts or 0) * q, 2)
    log(f"[ROLLER] {symbol} ROLLED {from_atm}→{new_atm} @ credit {entry_credit:.1f} | "
        f"est cost ₹{cost_est:.0f} | est premium gain ₹{prem_gain_rs:.0f}"
        + (f" ({prem_gain_pts:+.1f}pt)" if prem_gain_pts is not None else ""))
    if prem_gain_pts is not None and prem_gain_rs < cost_est:
        log(f"[ROLLER] {symbol} ⚠️ note: est premium gain ₹{prem_gain_rs:.0f} < est cost "
            f"₹{cost_est:.0f} — Rule 4 usually blocks this; verify config")

    state.record_roll(from_atm, new_atm, new_legs, entry_credit, cost_est,
                      prem_gain_rs, new_gid)
    return {"ok": True, "status": "rolled", "from": from_atm, "to": int(new_atm),
            "group_id": new_gid, "entry_credit": entry_credit, "cost_est": cost_est,
            "premium_gain": prem_gain_rs, "legs": new_legs}


# ─────────────────────────────────────────────────────────────────────────────
# on_candle_close — MAUJOODA candle/monitor cycle har 5-min candle pe call kare
# ─────────────────────────────────────────────────────────────────────────────
def on_candle_close(symbol="NIFTY", now=None, cfg=None, log=print, price_fn=None):
    """Har 5-min candle-close pe call ho (NO apna loop — existing cycle me hook karo,
    jaise auto_straddle_loop). Steps:
      1. config enabled? trading day? — warna skip.
      2. state load (day-scoped) + spot (shared_ltp_cache, ZERO extra Dhan call).
      3. no deployed straddle:
           • entry window ke andar + aaj tak deploy nahi hua → deploy_initial();
           • warna nothing to do.
      4. deployed straddle: verify_still_open (SL/EOD reconcile — band ko roll=re-open
         mat karo) → phir current ATM + confirmation bookkeeping + should_roll + roll.
    Returns dict {rolled, reason, ...}. KOI naya polling nahi (Rule per ADR)."""
    symbol = str(symbol).upper()
    cfg = cfg or load_config()
    now = now or _ist_now()
    price_fn = price_fn or _default_price_fn
    mode = str(cfg.get("mode", "paper"))

    if not cfg.get("enabled"):
        return {"rolled": False, "reason": "roller disabled (config)"}

    # trading-day gate (weekend/holiday) — market band pe kuch nahi
    try:
        import market_calendar as mc
        if not mc.is_trading_day(now.date()):
            return {"rolled": False, "reason": "market band (weekend/holiday)"}
    except Exception:
        pass   # calendar unavailable → fail-open (baaki gates still apply)

    state = RollerState(symbol).load()

    # ── spot (cache-only, poller-warmed — ZERO extra Dhan call) ──
    try:
        import shared_ltp_cache as slc
        spot = slc.get_index(symbol, max_age=120.0)
    except Exception:
        spot = None
    if not spot or spot <= 0:
        log(f"[ROLLER] {symbol} no fresh spot (cache cold) — skip candle (freeze)")
        return {"rolled": False, "reason": "no spot — freeze"}

    # ── nothing deployed yet: initial deploy within the entry window (once/day) ──
    if state.deployed_atm is None:
        if state.initial_deployed:
            return {"rolled": False, "reason": "no straddle open (already deployed+closed today)"}
        eh, em = _parse_hm(cfg.get("entry_time", "09:20"), (9, 20))
        win = int(cfg.get("entry_window_min", 6))
        delta = (now.hour * 60 + now.minute) - (eh * 60 + em)
        if not (0 <= delta <= win):
            return {"rolled": False, "reason": f"before/after entry window {eh:02d}:{em:02d}+{win}m"}
        ok, msg = deploy_initial(state, symbol, spot, int(cfg.get("lots", 1)),
                                 cfg=cfg, mode=mode, log=log)
        return {"rolled": False, "deployed": ok, "reason": "initial deploy: " + msg}

    # ── deployed: first confirm it's still genuinely open (SL/EOD may have closed it) ──
    if not verify_still_open(state, log=log):
        return {"rolled": False, "reason": "deployed straddle closed externally (SL/EOD) — flat, no re-open"}

    # ── current ATM strike (dhan_master se — live spot, no hardcoded step) ──
    try:
        import dhan_master
        ce_sec, ce_tsym, lot = dhan_master.get_option_contract(symbol, spot, "CE", 0)
        pe_sec, pe_tsym, lot2 = dhan_master.get_option_contract(symbol, spot, "PE", 0)
    except Exception as e:
        log(f"[ROLLER] {symbol} ATM resolve error: {e} — skip candle")
        return {"rolled": False, "reason": f"atm resolve error: {e}"}
    if not ce_sec or not pe_sec:
        log(f"[ROLLER] {symbol} ATM contract resolve fail — skip candle")
        return {"rolled": False, "reason": "atm contract resolve fail"}
    current_atm = _strike_of(ce_tsym)
    if current_atm is None:
        log(f"[ROLLER] {symbol} ATM strike parse fail ({ce_tsym}) — skip candle")
        return {"rolled": False, "reason": "atm strike parse fail"}
    lot_size = int(lot or lot2 or state.lot_size or 0)

    # ── per-candle confirmation bookkeeping (Rule 2 counter advances here) ──
    conf = state.observe_candle(current_atm)

    # ── keep the relevant strikes warm via the EXISTING poller (no new polling) ──
    try:
        import ltp_poller
        watch = [(lg.get("sec_id"), "NSE_FNO") for lg in (state.legs or [])]
        watch += [(str(ce_sec), "NSE_FNO"), (str(pe_sec), "NSE_FNO")]
        ltp_poller.request_watch(watch)
    except Exception:
        pass

    # ── premiums for Rule 4 (cache-only) ──
    cur_val = _combined_premium(state.legs, price_fn)          # deployed straddle now
    new_ce_p = price_fn(str(ce_sec))
    new_pe_p = price_fn(str(pe_sec))
    new_val = (new_ce_p + new_pe_p) if (new_ce_p and new_pe_p) else None

    # ── decision + LOG (always log — roll ho ya na ho) ──
    ok, reason = should_roll(state, current_atm, now=now,
                             current_straddle_value=cur_val, new_atm_premium=new_val,
                             cfg=cfg)
    log(f"[ROLLER] {symbol} candle {now.strftime('%H:%M')} | spot {spot:.1f} "
        f"| deployed_atm {state.deployed_atm} current_atm {current_atm} "
        f"conf {conf}/{cfg.get('confirmation_candles', 3)} rolls {state.rolls_today} "
        f"| cur {cur_val} new {new_val} → {'ROLL' if ok else 'HOLD'}: {reason}")

    if not ok:
        return {"rolled": False, "reason": reason, "current_atm": current_atm,
                "deployed_atm": state.deployed_atm, "confirmation_count": conf}

    res = execute_roll(state, symbol, current_atm, (ce_sec, ce_tsym), (pe_sec, pe_tsym),
                       state.lots, lot_size, current_straddle_value=cur_val,
                       new_atm_premium=new_val, mode=cfg.get("mode", "paper"),
                       cfg=cfg, log=log, price_fn=price_fn)
    return {"rolled": bool(res.get("ok")), "reason": reason, **res}


# ─────────────────────────────────────────────────────────────────────────────
# self-test (PURE decision logic — no broker/IO). Run: python _ops/atm_straddle_roller.py
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        import _paths  # noqa: F401
    except Exception:
        pass

    from datetime import datetime as _dt

    CFG = dict(_DEFAULT_CFG)
    CFG.update({"enabled": True, "confirmation_candles": 3, "cooldown_minutes": 30,
                "premium_benefit_pct": 0.70, "start_time": "09:30", "end_time": "15:00",
                "max_rolls_per_day": 3, "min_strike_distance": 50})

    NOON = _dt(2026, 7, 29, 12, 0)   # inside window
    T0 = 1_000_000.0                 # arbitrary epoch base

    def fresh(**kw):
        s = RollerState("NIFTY", path=Path(os.devnull))  # never writes to real file
        s.deployed_atm = 24200
        s.candidate_atm = 24250
        s.confirmation_count = 3
        s.rolls_today = 0
        s.last_roll_ts = None
        s.legs = [{"opt_type": "CE", "side": "SELL", "sec_id": "1", "trad_sym": "NIFTY-x-24200-CE", "qty": 65},
                  {"opt_type": "PE", "side": "SELL", "sec_id": "2", "trad_sym": "NIFTY-x-24200-PE", "qty": 65}]
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    # all pass — spot moved to 24250 (dist 50), confirmed 3, cur 40 << 0.7*100=70
    ok, r = should_roll(fresh(), 24250, now=NOON, now_ts=T0,
                        current_straddle_value=40, new_atm_premium=100, cfg=CFG)
    assert ok, r
    print("R0 all-pass:", r)

    # Rule 1 — dist 30 < 50
    ok, r = should_roll(fresh(), 24230, now=NOON, now_ts=T0,
                        current_straddle_value=40, new_atm_premium=100, cfg=CFG)
    assert not ok and r.startswith("rule1"), r
    print("R1:", r)

    # Rule 2 — only 2/3 candles confirmed
    ok, r = should_roll(fresh(confirmation_count=2), 24250, now=NOON, now_ts=T0,
                        current_straddle_value=40, new_atm_premium=100, cfg=CFG)
    assert not ok and r.startswith("rule2"), r
    print("R2:", r)

    # Rule 2 — candidate is a different strike than current_atm
    ok, r = should_roll(fresh(candidate_atm=24300), 24250, now=NOON, now_ts=T0,
                        current_straddle_value=40, new_atm_premium=100, cfg=CFG)
    assert not ok and r.startswith("rule2"), r
    print("R2b:", r)

    # Rule 3 — last roll 10 min ago (< 30 cooldown). now_ts = T0, last = T0-600s
    ok, r = should_roll(fresh(last_roll_ts=T0 - 600), 24250, now=NOON, now_ts=T0,
                        current_straddle_value=40, new_atm_premium=100, cfg=CFG)
    assert not ok and r.startswith("rule3"), r
    print("R3:", r)

    # Rule 3 passes when > 30 min elapsed
    ok, r = should_roll(fresh(last_roll_ts=T0 - 60 * 40), 24250, now=NOON, now_ts=T0,
                        current_straddle_value=40, new_atm_premium=100, cfg=CFG)
    assert ok, r
    print("R3-pass:", r)

    # Rule 4 — current 80 >= 0.7*100=70 → not worth
    ok, r = should_roll(fresh(), 24250, now=NOON, now_ts=T0,
                        current_straddle_value=80, new_atm_premium=100, cfg=CFG)
    assert not ok and r.startswith("rule4"), r
    print("R4:", r)

    # Rule 4 — missing premium → freeze
    ok, r = should_roll(fresh(), 24250, now=NOON, now_ts=T0,
                        current_straddle_value=None, new_atm_premium=100, cfg=CFG)
    assert not ok and r.startswith("rule4"), r
    print("R4-freeze:", r)

    # Rule 5 — before 09:30
    ok, r = should_roll(fresh(), 24250, now=_dt(2026, 7, 29, 9, 20), now_ts=T0,
                        current_straddle_value=40, new_atm_premium=100, cfg=CFG)
    assert not ok and r.startswith("rule5"), r
    print("R5-early:", r)

    # Rule 5 — after 15:00
    ok, r = should_roll(fresh(), 24250, now=_dt(2026, 7, 29, 15, 10), now_ts=T0,
                        current_straddle_value=40, new_atm_premium=100, cfg=CFG)
    assert not ok and r.startswith("rule5"), r
    print("R5-late:", r)

    # Rule 6 — max rolls reached
    ok, r = should_roll(fresh(rolls_today=3), 24250, now=NOON, now_ts=T0,
                        current_straddle_value=40, new_atm_premium=100, cfg=CFG)
    assert not ok and r.startswith("rule6"), r
    print("R6:", r)

    # nothing deployed
    s = RollerState("NIFTY", path=Path(os.devnull))
    s.deployed_atm = None
    ok, r = should_roll(s, 24250, now=NOON, now_ts=T0, cfg=CFG)
    assert not ok and "no deployed" in r, r
    print("R-empty:", r)

    # observe_candle confirmation counting
    s = RollerState("NIFTY", path=Path(os.devnull))
    s.deployed_atm = 24200
    assert s.observe_candle(24200) == 0            # same ATM → no candidate
    assert s.observe_candle(24250) == 1            # new candidate, candle 1
    assert s.observe_candle(24250) == 2            # confirming, candle 2
    assert s.observe_candle(24250) == 3            # candle 3
    assert s.observe_candle(24300) == 1            # ATM jumped again → new candidate reset
    assert s.observe_candle(24200) == 0            # back to deployed → clear
    print("observe_candle: 0→1→2→3→(jump)1→(back)0 ok")

    # estimate_roll_cost sanity
    c = estimate_roll_cost(100, 65, CFG)   # 20*4=80 + 0.15%*100*65=9.75 + 8 = 97.75
    assert abs(c - 97.75) < 0.01, c
    print("estimate_roll_cost(100,65):", c)

    print("\n✅ atm_straddle_roller self-test — all should_roll rules + state + cost pass")
