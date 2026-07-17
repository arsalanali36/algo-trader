#!/usr/bin/env python3
"""
_TOOLS/architecture_audit.py — Mechanical architecture audit (CLAUDE.md Rule 6B enforcer).

Pure static analysis (AST + regex, zero LLM calls). Scans the repo (or only
staged files) and reports FAIL/WARN per Rule 6B:

  1. RAW-ORDER      .place_order()/.cancel_order() called outside smart_order.py/brokers/
  2. INLINE-RISK    capital/concentration/drawdown comparison inline in a strategy file
                    (must go through risk_gate.py / strategy_safety.gate_entry())
  3. DUP-INDICATOR  EMA/RSI/ATR/VWAP/... function defined outside _CHARTING/
  4. STATE-PERSIST  module-level {}/[] named peak/pending/lock/state with no json persistence (WARN)
  5. BACKTEST-RISK  backtest/simulation file simulates capital/risk itself without importing
                    risk_gate / strategy_safety / smart_order / execution_gateway
  6. SINGLETON      live trader daemon (--id + while-True loop) missing acquire_singleton()
                    guard — a 2nd copy (scheduler/restart race) places duplicate orders
  7. RAW-HTTP-ORDER requests.post()/put()/delete() straight at a broker /orders endpoint
                    (check 1 only sees SDK-shaped .place_order() — a raw POST walked past it)
  8. CORE-IMPORTS-UI  _core/ importing trader_dashboard — money path depending on the UI
  9. RAW-STRAT-LABEL raw strategy config-key rendered on a user-visible surface instead
                    of regLabel()/regId() (JS) or strategy_registry.label() (Python)

  Check 9 added 2026-07-17. The registry was always right — the names were IN it —
  yet 25 places still printed raw config-keys (ARS_CHAIN_V1, arschain_MAIN,
  vrp_condor_v1) straight at the user. Fixing them one by one fixes nothing
  permanently; site #26 gets written next week. NOTE this check also scans
  static/js/ + templates/ — checks 1-8 are .py-only, which is exactly why every
  one of those leaks lived in a blind spot. An enforcer that can't see the
  display layer can't enforce anything about the display layer.
  Escape hatch: `raw-id-ok: <reason>` on the line (or its comment block) for
  strings that are IDENTITY, not display — storage keys, fingerprints, dedup keys.

  Checks 7-8 added 2026-07-16 after an audit found both blind spots being actively
  exploited: /api/close-position raw-POSTs orders to Dhan while the algo trades on
  Kite (invisible to check 1), and webhook_executor imports its ONLY kill-floor
  check out of trader_dashboard inside a try/except that fails open. The enforcer
  reported "0 FAIL" throughout. A check that can't see the money path isn't a check.

Usage:
  python _TOOLS/architecture_audit.py                   # full repo scan
  python _TOOLS/architecture_audit.py --staged-only     # only `git diff --cached` files (pre-commit hook)
  python _TOOLS/architecture_audit.py --report          # also write _TOOLS/ARCH_AUDIT_REPORT.md
  python _TOOLS/architecture_audit.py --write-baseline  # accept current FAILs as the known-debt baseline

BASELINE RATCHET (progressive adoption, like real linters):
  _TOOLS/audit_baseline.json holds known pre-existing FAIL counts per (file, check)
  — the debt that Tasks 3+ are scheduled to pay down. A FAIL is only BLOCKING when
  its (file, check) count EXCEEDS the baseline (i.e., someone added a NEW violation).
  Baselined FAILs still print (as BASE) so the scoreboard stays visible.
  After fixing debt, re-run --write-baseline to ratchet the allowance DOWN.

Exit code: 1 if any non-baselined FAIL (WARN/BASE alone don't block). The pre-commit
hook depends on this.
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_FILE = os.path.join(REPO_ROOT, "_TOOLS", "audit_baseline.json")

# ---------------------------------------------------------------- scan scope

SCAN_DIRS = ["", "_core", "_data", "_ops", "_TOOLS", "_CHARTING",
             "strategies", "strategies/backtest", "strategies/live", "brokers",
             # scratch/nifty_trend is NOT scratch (2026-07-16): every shipped
             # strategy's validated Sharpe/net comes out of it, _core/payoff.py
             # imports bs_option from it, and 03_orbst_trader's _supertrend_dir is
             # a hand-copy of its supertrend(). Excluding it meant the Rule 6B
             # enforcer was blind to the biggest, most load-bearing folder in the
             # repo — purely because of what it's named.
             "scratch/nifty_trend"]

# One-off/scratch scripts — not part of the live architecture, never audited.
EXCLUDE_PATTERNS = [
    # scratch/ stays excluded EXCEPT nifty_trend (see SCAN_DIRS). Matched against
    # the repo-relative path in staged_files(), so the pre-commit hook honours it.
    r"^scratch[/\\](?!nifty_trend[/\\]).*\.py$",
    r"^patch_.*\.py$", r"^delete_.*\.py$", r"^fix_.*\.py$",
    r"^check.*\.py$", r"^verify\.py$", r"^clean\.py$", r"^find_block\.py$",
    r"^extract_base64\.py$", r"^test_extract\.py$",
    r"^recover_default\.py$", r"^vps_delete_script\.py$", r"^_test_.*\.py$",
]

# Check 1 allowlist: the ONLY files allowed to call broker .place_order/.cancel_order
RAW_ORDER_ALLOW = {"smart_order.py"}
RAW_ORDER_ALLOW_DIRS = {"brokers"}  # broker implementations wrap the SDK itself

# Check 2 scope: strategy files only (signal layer — must not contain risk logic)
STRATEGY_DIRS = {"_TRADERS", "strategies"}
STRATEGY_FILES = {"webhook_executor.py"}
RISK_HOME = {"risk_gate.py", "strategy_safety.py"}

# Check 3: indicator-looking function names; only _CHARTING may define them
INDICATOR_FN_RE = re.compile(
    r"^_?(compute_|calc_|get_)?(ema|sma|rsi|atr|vwap|macd|adx|stoch|supertrend|"
    r"bbands?|bollinger\w*|pivots?\w*)(_\w+)?$", re.IGNORECASE)
INDICATOR_HOME_DIR = "_CHARTING"

# Check 4: persist-worthy module-level state names
STATE_NAME_RE = re.compile(r"(peak|pending|lock|state)", re.IGNORECASE)

# Check 5: backtest files + what counts as "doing risk simulation yourself"
BACKTEST_FILE_RE = re.compile(r"(backtest|simulat)", re.IGNORECASE)
RISK_IMPORTS = {"risk_gate", "strategy_safety", "smart_order", "execution_gateway"}
RISK_SIM_RE = re.compile(r"\b(capital|concentration|drawdown)\b", re.IGNORECASE)

# Check 7: broker ORDER endpoints hit over raw HTTP. Check 1 only sees SDK-shaped
# `.place_order()` calls, so a plain requests.post(<orders url>) walked straight
# past it — that blind spot is exactly how /api/close-position kept a hardcoded
# 'dhan' order POST while the algo moved to Kite (2026-07-16 audit).
# Only WRITE verbs — GET /v2/orders is a status poll, not an order.
BROKER_ORDER_URL_RE = re.compile(
    r"https?://(api\.dhan\.co|api\.kite\.trade)[\w./-]*/orders?\b", re.IGNORECASE)
HTTP_WRITE_VERBS = {"post", "put", "delete", "patch"}

# Check 8: _core/ is the money path; trader_dashboard.py is the Flask UI monolith.
# _core importing the UI inverts the layering, and every real instance so far sits
# in a `try: ... except: pass` — so the day the import breaks, a risk check silently
# stops running instead of failing loudly (webhook_executor's kill-floor check).
CORE_DIR = "_core"
UI_MODULE = "trader_dashboard"

# ---------------------------------------------------------------- helpers

class Finding:
    def __init__(self, level, check, rel_path, line, msg):
        self.level, self.check, self.rel_path, self.line, self.msg = level, check, rel_path, line, msg

    def __str__(self):
        return f"[{self.level}] {self.check:14s} {self.rel_path}:{self.line} — {self.msg}"


def is_excluded(fname):
    return any(re.match(p, fname) for p in EXCLUDE_PATTERNS)


def iter_repo_files():
    for d in SCAN_DIRS:
        full = os.path.join(REPO_ROOT, d) if d else REPO_ROOT
        if not os.path.isdir(full):
            continue
        for fname in sorted(os.listdir(full)):
            if not fname.endswith(".py") or is_excluded(fname):
                continue
            yield os.path.join(full, fname)
    # static/js + templates — RAW-STRAT-LABEL ke liye. Check 9 se pehle audit
    # sirf .py dekhta tha, jabki har user-visible leak wahin (JS/HTML) tha.
    for f in iter_display_files():
        yield f


def staged_files():
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=REPO_ROOT, text=True)
    except Exception as e:
        print(f"[WARN] git diff --cached failed ({e}) — falling back to full scan")
        return list(iter_repo_files())
    files = []
    for rel in out.splitlines():
        # Check BOTH the full repo-relative path (so path-anchored patterns like
        # ^scratch.*\.py$ actually match staged files — they got only the
        # basename before, silently defeating the scratch/ exclusion) AND the
        # basename (so name patterns like ^patch_.*\.py$ still match anywhere).
        _disp = (rel.endswith(".js") or rel.endswith(".html")) and                any(rel.replace("\\", "/").startswith(d + "/") for d in DISPLAY_SCAN_DIRS) and                os.path.basename(rel) not in DISPLAY_EXCLUDE
        if not (rel.endswith(".py") or _disp):
            continue
        if is_excluded(rel) or is_excluded(os.path.basename(rel)):
            continue
        full = os.path.join(REPO_ROOT, rel.replace("/", os.sep))
        if os.path.isfile(full):
            files.append(full)
    return files


def rel(path):
    return os.path.relpath(path, REPO_ROOT)


def parent_dir(path):
    """Immediate dir of the file relative to repo root ('' if repo root)."""
    r = rel(path)
    parts = r.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else ""


def parse(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:  # -sig: strip BOM (some repo files have it)
        src = f.read()
    try:
        return src, ast.parse(src)
    except SyntaxError as e:
        return src, e  # surfaced as its own FAIL


# ---------------------------------------------------------------- checks

def check_raw_orders(path, tree, findings):
    fname = os.path.basename(path)
    if fname in RAW_ORDER_ALLOW or parent_dir(path) in RAW_ORDER_ALLOW_DIRS:
        return
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("place_order", "cancel_order")):
            findings.append(Finding(
                "FAIL", "RAW-ORDER", rel(path), node.lineno,
                f"direct .{node.func.attr}() call — use smart_order.execute() "
                f"(or execution_gateway.execute_signal() once built)"))


def check_inline_risk(path, src, findings):
    fname = os.path.basename(path)
    if fname in RISK_HOME:
        return
    if parent_dir(path) not in STRATEGY_DIRS and fname not in STRATEGY_FILES:
        return
    for i, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        if not re.search(r"\b(capital|concentration|drawdown)\b", code, re.IGNORECASE):
            continue
        # calling the gateway is exactly what we WANT — only inline math is a smell
        if re.search(r"(risk_gate|strategy_safety|gate_entry)", code):
            continue
        if re.search(r"(if|while|assert)\b.*[<>]|[<>]=?\s*\w*(capital|concentration|drawdown)", code, re.IGNORECASE):
            findings.append(Finding(
                "FAIL", "INLINE-RISK", rel(path), i,
                "inline capital/concentration/drawdown comparison in a strategy file — "
                "route through risk_gate.py / strategy_safety.gate_entry()"))


def check_dup_indicators(path, tree, findings):
    if parent_dir(path) == INDICATOR_HOME_DIR:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and INDICATOR_FN_RE.match(node.name):
            findings.append(Finding(
                "FAIL", "DUP-INDICATOR", rel(path), node.lineno,
                f"indicator-like function '{node.name}' defined outside {INDICATOR_HOME_DIR}/ — "
                f"import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, "
                f"or add it THERE if it doesn't exist yet"))


def check_state_persistence(path, src, tree, findings):
    has_json_write = bool(re.search(r"json\.dump|_save_\w+|\.write\(", src))
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, (ast.Dict, ast.List)):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and STATE_NAME_RE.search(tgt.id) and not has_json_write:
                findings.append(Finding(
                    "WARN", "STATE-PERSIST", rel(path), node.lineno,
                    f"module-level '{tgt.id}' looks persist-worthy but file has no json.dump/_save_* — "
                    f"restart will wipe it (see _pending_group_close/_kf_state pattern in trader_dashboard.py)"))


def check_singleton_guard(path, src, findings):
    """Every live trader DAEMON (its own --id CLI + a while-True order loop) must call
    acquire_singleton() at startup. Without it, two copies of the same strategy
    (scheduler/restart race — dashboard get_pid() has a TOCTOU gap) each keep their
    OWN in-memory position (pos=None) and BOTH enter on one signal → duplicate live
    orders + inflated trade count. EOD 2026-07-13: orbst_v1/dvert_v1 hit exactly this."""
    r = rel(path).replace("\\", "/")
    if not (r.startswith("strategies/live/") or r.startswith("_TRADERS/")):
        return
    if "--id" not in src or "while True" not in src:   # only entrypoint daemons
        return
    if "acquire_singleton" in src:
        return
    findings.append(Finding(
        "FAIL", "SINGLETON", rel(path), 1,
        "live trader daemon has no acquire_singleton() guard — a 2nd copy "
        "(scheduler/restart race) will place DUPLICATE orders. Add at startup: "
        "`from singleton_guard import acquire_singleton` + "
        "`if not acquire_singleton(strategy_id): return`"))


def check_backtest_risk_bypass(path, src, tree, findings):
    fname = os.path.basename(path)
    if not BACKTEST_FILE_RE.search(fname):
        return
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    if imports & RISK_IMPORTS:
        return
    hits = [i for i, line in enumerate(src.splitlines(), 1)
            if RISK_SIM_RE.search(line.split("#", 1)[0])]
    if hits:
        findings.append(Finding(
            "FAIL", "BACKTEST-RISK", rel(path), hits[0],
            f"backtest/simulation file mentions capital/concentration/drawdown on "
            f"{len(hits)} line(s) but imports none of {sorted(RISK_IMPORTS)} — "
            f"backtest must share live risk rules (execute_signal(mode='backtest')) "
            f"or be explicitly flagged to the user"))


def _order_url_names(tree):
    """Module-level names bound to a broker ORDER url.

    Zaroori hai kyunki asli code `ORDERS_URL = "https://api.dhan.co/v2/orders"`
    upar rakhta hai aur neeche `requests.post(ORDERS_URL, ...)` karta hai — sirf
    literal-arg dekhne wala check use miss kar jaata.
    """
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) \
                and BROKER_ORDER_URL_RE.search(node.value.value):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _hits_order_url(arg, url_names):
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return bool(BROKER_ORDER_URL_RE.search(arg.value))
    if isinstance(arg, ast.Name):
        return arg.id in url_names
    if isinstance(arg, ast.JoinedStr):      # f"{BASE}/v2/orders"
        return any(_hits_order_url(v, url_names) for v in arg.values)
    if isinstance(arg, ast.FormattedValue):
        return _hits_order_url(arg.value, url_names)
    return False


def check_raw_http_orders(path, tree, findings):
    """Check 7 — RAW-HTTP-ORDER: broker order endpoint hit directly over HTTP."""
    fname = os.path.basename(path)
    if fname in RAW_ORDER_ALLOW or parent_dir(path) in RAW_ORDER_ALLOW_DIRS:
        return
    url_names = _order_url_names(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr.lower() not in HTTP_WRITE_VERBS:
            continue
        if not any(_hits_order_url(a, url_names) for a in node.args):
            continue
        findings.append(Finding(
            "FAIL", "RAW-HTTP-ORDER", rel(path), node.lineno,
            f"raw HTTP .{node.func.attr}() to a broker ORDER endpoint — bypasses "
            f"smart_order/execution_gateway, so RMS gating, rate-limiting, "
            f"async fill-confirm and order_store recording ALL skip. Route it "
            f"through execution_gateway.execute_signal()/execute_exit()"))


# ---------------------------------------------------- check 9: RAW-STRAT-LABEL
#
# 2026-07-17. Registry hamesha sahi tha — naam usme likhe the. Phir bhi ek audit
# me 25 jagah mili jahan raw config-key (ARS_CHAIN_V1, arschain_MAIN,
# vrp_condor_v1) seedha user ki screen pe chhap raha tha: Stats table, Payoff
# modal ka title, bell ki notifications, CSV export, dropdowns, EOD report.
#
# Ye sab ek-ek karke theek karne se kuch permanent nahi hota — 26vi jagah kal
# ban jaayegi. Isliye rule mechanical hai: user ko dikhne wali har jagah
# regLabel()/regId() (JS) ya strategy_registry.label()/strat_label()/_sid_lbl()
# (Python) se guzre. Raw id sirf plumbing me — order_store rows, API query
# params, config keys, aur `title=` hover (wahan wo JAAN-BOOJH ke hai).
#
# Ye check regex hai, AST nahi (JS/HTML parse nahi kar sakte) — isliye
# jaan-boojh ke conservative: sirf saaf-saaf render sites, aur neeche har wo
# shape skip hai jo genuinely raw honi chahiye. Shak ho to chhod deta hai —
# ek jhoota FAIL developer ko `--no-verify` sikha dega, aur phir har check bekaar.

# JS/HTML: template-literal me strategy-ish variable interpolate ho raha hai.
_JS_INTERP = re.compile(r"\$\{[^{}]*\}")
_JS_STRATVAR = re.compile(r"\b(?:strategy|strat|stratName|stratId|sid|_sid)\b", re.I)
_JS_LABELLED = re.compile(r"\b(?:regLabel|regId|regFull|_ordTag|_ordTags|_logName|_src)\s*\(")
# Wo shapes jinme raw id HONA hi chahiye — inhe chhod do.
_JS_SKIP_LINE = re.compile(
    r"""title\s*=\s*["']\$\{           # title="${...}"  -> hover pe raw = by design
      | \?\s*strategy=                  # URL query param
      | ['"]strategy['"]\s*:            # JSON/dict key
      | \.set\(\s*['"]strategy['"]      # URLSearchParams
      | \bdata-                         # data-* attribute (machine-read)
      | \.value\s*=                     # <option>.value — filter isi pe match karta hai
      | onclick=                        # handler arg = raw key (callback ko chahiye)
    """, re.I | re.X)
# Poora `${...}` ek function-call hai → strategy var us call ka ARGUMENT hai,
# rendered value nahi (`${_payoffBtn(items, stratName)}` = button banata hai,
# `${encodeURIComponent(strategy)}` = URL). Labelling callee ki zimmedari hai.
# Exception = ye pass-through wrappers: escape/stringify karte hain, label NAHI —
# `${esc(t.strategy)}` utna hi raw chhapta hai jitna `${t.strategy}`.
_JS_CALL = re.compile(r"^\$\{\s*([A-Za-z_$][\w$.]*)\s*\(")
_JS_PASSTHRU = {"esc", "_esc", "String", "encodeURI"}
# Escape hatch: kuch strings user ko dikhti hi nahi — wo IDENTITY hoti hain
# (localStorage key, change-detect fingerprint, dedup key). Unhe label karna
# sirf bekaar nahi, GALAT hai: do strategies ka label ek jaisa ho to fingerprint
# collide karega aur storage key naam badalte hi purani settings kho jaayengi.
# Aisi jagah `raw-id-ok: <wajah>` likho — chupchaap skip nahi, likhi hui wajah.
_RAW_OK = re.compile(r"raw-id-ok")

# Python: `notify.error(f"{sid}: ...", source=sid)` — raw id msg ke ANDAR.
# `source=` alag field hai aur listing() usay label karta hai; msg ek jama hua
# string hai, usme se naam nikaal ke label karna namumkin hai.
_PY_NOTIFY_RAWID = re.compile(
    r"""notify\.(?:error|warn|info|push)\s*\(\s*f["']\{\s*(\w+)\s*\}\s*:""", re.X)

# Python: user-facing sentence jisme raw `{strategy}` gundh diya gaya ho.
# 2026-07-17 ko ye khud pakda nahi gaya tha — check ke pehle version ne sirf
# notify dekha, aur EOD report me `risk_gate` ki teen RMS block-reason strings
# ("...hit for 'rsi_v1_PAPER'") bach nikli. Wahi blind-spot sabak, chhote paimane pe.
# `return`/`append` wali reason lines — sirf tab jab quote ke andar ho ('{strategy}'),
# yaani jumle me chipka hua naam, na ki dict value ya query param.
_PY_REASON_RAWID = re.compile(
    r"""(?:return|append|reason\s*=|msg\s*=)[^\n]*f["'][^"'\n]*['"]\{\s*(strategy|sid|strategy_id)\s*\}['"]""")
# Wo files jinme ye check bekaar hai: strategy_registry khud labeller hai; audit
# me ye patterns bataur regex-source likhe hain.
_PY_LABEL_ALLOW = {"strategy_registry.py", "architecture_audit.py"}

DISPLAY_SCAN_DIRS = ["static/js", "templates"]
# registry.js khud labeller hai; notify.js server ka `source_label` render karti hai.
DISPLAY_EXCLUDE = {"registry.js"}


def iter_display_files():
    for d in DISPLAY_SCAN_DIRS:
        full = os.path.join(REPO_ROOT, d.replace("/", os.sep))
        if not os.path.isdir(full):
            continue
        for fname in sorted(os.listdir(full)):
            if fname in DISPLAY_EXCLUDE:
                continue
            if fname.endswith(".js") or fname.endswith(".html"):
                yield os.path.join(full, fname)


def check_raw_strategy_label(path, src, findings):
    """Raw strategy id kisi user-visible surface pe ja raha hai?"""
    is_py = path.endswith(".py")
    lines = src.splitlines()
    for i, line in enumerate(lines, 1):
        # `raw-id-ok` usi line pe, ya upar ke comment-block me (3 line tak —
        # wajah aksar ek line me nahi samati).
        if _RAW_OK.search(line) or _RAW_OK.search("\n".join(lines[max(0, i - 4):i - 1])):
            continue
        st = line.strip()
        if st.startswith("//") or st.startswith("#") or st.startswith("*"):
            continue
        if is_py:
            if os.path.basename(path) in _PY_LABEL_ALLOW:
                continue
            m = _PY_NOTIFY_RAWID.search(line)
            if m and "source=" in line + src[src.find(line):src.find(line) + 300]:
                findings.append(Finding(
                    "FAIL", "RAW-STRAT-LABEL", rel(path), i,
                    f"notify msg me raw id '{{{m.group(1)}}}:' — prefix hatao, "
                    f"source={m.group(1)} already carry karta hai (listing() usay label karta hai)"))
            m2 = _PY_REASON_RAWID.search(line)
            if m2:
                findings.append(Finding(
                    "FAIL", "RAW-STRAT-LABEL", rel(path), i,
                    f"user-facing reason me raw '{{{m2.group(1)}}}' — label karke daalo "
                    f"(risk_gate._sname() / strat_label() / _sid_lbl())"))
            continue
        if _JS_SKIP_LINE.search(line):
            continue
        for interp in _JS_INTERP.findall(line):
            if not _JS_STRATVAR.search(interp):
                continue
            if _JS_LABELLED.search(interp):
                continue
            _c = _JS_CALL.match(interp)
            if _c and _c.group(1) not in _JS_PASSTHRU:
                continue
            findings.append(Finding(
                "FAIL", "RAW-STRAT-LABEL", rel(path), i,
                f"raw strategy id render ho raha hai: {interp.strip()} — regLabel() se guzaro "
                f"(raw chahiye to title= me daalo)"))


def check_core_imports_ui(path, tree, findings):
    """Check 8 — CORE-IMPORTS-UI: _core/ must not depend on the Flask UI module."""
    if parent_dir(path) != CORE_DIR:
        return
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module.split(".")[0]]
        if UI_MODULE in mods:
            findings.append(Finding(
                "FAIL", "CORE-IMPORTS-UI", rel(path), node.lineno,
                f"{CORE_DIR}/ imports '{UI_MODULE}' — layering inversion: the money "
                f"path must not depend on the UI. These imports live inside "
                f"try/except and FAIL OPEN, so a risk check silently stops running. "
                f"Move the shared function into {CORE_DIR}/ and import it from there"))


# ---------------------------------------------------------------- main

def audit(files):
    findings = []
    for path in files:
        # JS/HTML: AST nahi hai, sirf display-layer check (RAW-STRAT-LABEL).
        if not path.endswith(".py"):
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                check_raw_strategy_label(path, f.read(), findings)
            continue
        src, tree = parse(path)
        if isinstance(tree, SyntaxError):
            findings.append(Finding("FAIL", "SYNTAX", rel(path), tree.lineno or 0,
                                    f"file does not parse: {tree.msg}"))
            continue
        check_raw_strategy_label(path, src, findings)
        check_raw_orders(path, tree, findings)
        check_inline_risk(path, src, findings)
        check_dup_indicators(path, tree, findings)
        check_state_persistence(path, src, tree, findings)
        check_singleton_guard(path, src, findings)
        check_backtest_risk_bypass(path, src, tree, findings)
        check_raw_http_orders(path, tree, findings)
        check_core_imports_ui(path, tree, findings)
    return findings


def write_report(findings, files_scanned):
    out = os.path.join(REPO_ROOT, "_TOOLS", "ARCH_AUDIT_REPORT.md")
    fails = [f for f in findings if f.level == "FAIL"]
    warns = [f for f in findings if f.level == "WARN"]
    based = [f for f in findings if f.level == "BASE"]
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# Architecture Audit Report\n\n")
        f.write(f"- **Run:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"- **Files scanned:** {files_scanned}\n")
        f.write(f"- **FAIL:** {len(fails)} | **WARN:** {len(warns)} | **Baselined debt:** {len(based)}\n\n")
        for title, items in (("FAILs", fails), ("WARNs", warns), ("Baselined (pre-existing debt — Tasks 3+ scope)", based)):
            f.write(f"## {title}\n\n")
            if not items:
                f.write("(none)\n\n")
                continue
            f.write("| Check | File:Line | Detail |\n|---|---|---|\n")
            for fd in items:
                f.write(f"| {fd.check} | `{fd.rel_path}:{fd.line}` | {fd.msg} |\n")
            f.write("\n")
    return out


def _group_key(fd):
    """Baseline key: file + check (line numbers shift, counts don't lie)."""
    return f"{fd.rel_path.replace(os.sep, '/')}|{fd.check}"


def load_baseline():
    if not os.path.isfile(BASELINE_FILE):
        return {}
    with open(BASELINE_FILE, encoding="utf-8") as f:
        return json.load(f).get("known_fail_counts", {})


def apply_baseline(findings, baseline):
    """Downgrade FAILs covered by the baseline ratchet to level BASE (non-blocking).
    If a (file, check) group's count EXCEEDS its baseline allowance, the whole
    group stays FAIL — someone added a NEW violation on top of the known debt."""
    groups = {}
    for fd in findings:
        if fd.level == "FAIL":
            groups.setdefault(_group_key(fd), []).append(fd)
    for key, group in groups.items():
        allowed = baseline.get(key, 0)
        if allowed and len(group) <= allowed:
            for fd in group:
                fd.level = "BASE"
                fd.msg += f"  [baselined pre-existing debt: {len(group)}/{allowed} allowed]"
        elif allowed and len(group) > allowed:
            for fd in group:
                fd.msg += f"  [EXCEEDS baseline: {len(group)} found, only {allowed} allowed — NEW violation added]"


def write_baseline(findings):
    counts = {}
    for fd in findings:
        if fd.level in ("FAIL", "BASE"):
            counts[_group_key(fd)] = counts.get(_group_key(fd), 0) + 1
    with open(BASELINE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "_comment": "Known pre-existing FAIL counts (debt) — audit only blocks when a count EXCEEDS "
                        "its allowance here. After paying debt down (Tasks 3+), re-run --write-baseline "
                        "to ratchet allowances DOWN. Never hand-edit upward.",
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "known_fail_counts": counts,
        }, f, indent=2)
    return counts


def main():
    ap = argparse.ArgumentParser(description="Mechanical architecture audit (Rule 6B)")
    ap.add_argument("--staged-only", action="store_true",
                    help="only audit files in git diff --cached (pre-commit hook mode)")
    ap.add_argument("--report", action="store_true",
                    help="also write _TOOLS/ARCH_AUDIT_REPORT.md")
    ap.add_argument("--write-baseline", action="store_true",
                    help="accept current full-repo FAILs as the known-debt baseline (ratchet)")
    args = ap.parse_args()

    files = staged_files() if args.staged_only else list(iter_repo_files())
    if not files:
        print("[PASS] no Python files to audit (nothing staged).")
        return 0

    findings = audit(files)

    if args.write_baseline:
        counts = write_baseline(findings)
        print(f"Baseline written: {BASELINE_FILE} — {sum(counts.values())} known FAIL(s) "
              f"across {len(counts)} (file, check) group(s)")
        return 0

    apply_baseline(findings, load_baseline())
    fails = [f for f in findings if f.level == "FAIL"]
    warns = [f for f in findings if f.level == "WARN"]
    based = [f for f in findings if f.level == "BASE"]

    mode = "staged-only" if args.staged_only else "full-repo"
    print(f"Architecture audit ({mode}) — {len(files)} file(s) scanned")
    print("-" * 72)
    for fd in findings:
        print(fd)
    print("-" * 72)
    print(f"RESULT: {len(fails)} FAIL, {len(warns)} WARN, {len(based)} baselined (pre-existing debt)")

    if args.report:
        print(f"Report written: {write_report(findings, len(files))}")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
