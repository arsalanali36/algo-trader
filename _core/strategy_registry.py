"""
strategy_registry.py — the SINGLE source of truth for strategy identity.

Every strategy has ONE permanent hierarchical ID (family.member, e.g. "00.01").
This module maps that ID <-> every alias it's known by (config_key, hub slug,
live file). ALL display surfaces (logs, RMS table, positions, reports, hub)
should render `label(x)` instead of a raw config-key / slug / mission-number, so
a strategy is identified the SAME way everywhere.

Design rules (see strategy_registry.json `_meta.rules`):
  - IDs are PERMANENT — never renumber / reuse / re-map.
  - New strategy in an existing family  -> next_member(family).
  - Brand-new family                    -> next_family() + ".01".
  - Rejected/failed/legacy keep their ID (status marks it).
  - Internal keys are NOT the ID — this is a display/organization layer; live
    plumbing (correlationId/order_store) stays on config_key until a deliberate,
    separately-approved rename migration.

Nothing here places orders or touches live state — pure lookup + optional
registry edits.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_JSON = os.path.join(os.path.dirname(_HERE), "strategy_registry.json")  # repo root
_CACHE = None


def load(force=False):
    """Load (and cache) the registry dict."""
    global _CACHE
    if _CACHE is None or force:
        try:
            with open(_JSON, encoding="utf-8") as f:
                _CACHE = json.load(f)
        except Exception:
            _CACHE = {"_meta": {}, "families": {}, "strategies": {}}
    return _CACHE


def families():
    return load().get("families", {})


def strategies():
    return load().get("strategies", {})


def get(sid):
    """Record for an exact ID, else None."""
    return strategies().get(str(sid))


def resolve(alias):
    """Return the canonical ID for anything a strategy is known by — its ID,
    config_key, hub slug, or any entry in its `aliases` list (case-insensitive).
    None if unknown.

    `aliases` exists so a strategy can be RENAMED without ever renaming its
    config_key: order_store rows, _risk.per_strategy keys and tsl_state keys are
    all keyed on the old string and must keep resolving forever. Retiring a name
    means moving it into aliases, never deleting it."""
    if alias is None:
        return None
    a = str(alias).strip()
    # Empty = untagged; NEVER a strategy. Guard karo warna `("" or "").lower()`
    # kisi bhi missing-config_key wale record se match kar jaata tha (`''` → 00.07
    # "Long Strangle" mislabel — 65 blank orders galat strategy pe chip rahe the).
    if not a:
        return None
    st = strategies()
    if a in st:
        return a
    al = a.lower()
    for sid, r in st.items():
        if (r.get("config_key") or "").lower() == al:
            return sid
        if (r.get("slug") or "").lower() == al:
            return sid
        for _alt in (r.get("aliases") or []):
            if str(_alt).lower() == al:
                return sid
    # Webhook/pine pollution: strategy field me "id | description" ghus jaata hai
    # (order_store.strategy pe validation nahi — TRAP #128). "id" part pe resolve
    # karo taaki "52_Week_Breakout | Price closing..." → 09.01 pahunche.
    if " | " in a:
        return resolve(a.split(" | ", 1)[0])
    return None


def hidden_identifiers():
    """Set of identifiers that must never be rendered AS a strategy (lowercased).

    See `_meta.hidden` in the JSON for why each one is on the list. This is NOT
    a delete and NOT a P&L exclusion — every order_store row stays put and still
    counts in the money. It only stops dead configs (`vrp_v1`), non-strategies
    (`global` — the webhooks shared-config block), superseded ones
    (`webhook_v1`, `default`) and pure garbage (`''`, `unknown`, `ema920`, a
    description someone wrote into the strategy field) from showing up in
    strategy lists, pickers, the RMS table and the log sidebar."""
    h = (load().get("_meta", {}) or {}).get("hidden", {}) or {}
    return {str(k).lower() for k in (h.get("identifiers") or {})}


def is_hidden(alias):
    """True if this identifier should be kept out of strategy lists/pickers."""
    return str(alias or "").lower() in hidden_identifiers()


def bucket_labels():
    """Non-strategy order_store.strategy values (`unknown`/`default`/`manual`/'')
    ke CLEAN display naam. Ye REAL P&L rakhte hain (P&L table inhe HIDE nahi kar
    sakta), par strategy nahi hain — inhe `label()` raw ki jagah in saaf naamo se
    dikhata hai. Single source: `_meta.bucket_labels` (Python + JS dono padhte)."""
    m = (load().get("_meta", {}) or {}).get("bucket_labels", {}) or {}
    return {str(k).lower(): v for k, v in m.items() if not str(k).startswith("_")}


def _beautify(raw):
    """Anjaana raw id → padhne-layak Title Case (aakhri fallback, taaki raw
    underscore/kebab kabhi screen pe na aaye). `some_new_strat` → "Some New
    Strat"; `CamelId` → "Camel Id". Pure transform — koi guess/stale naam nahi."""
    import re
    s = str(raw).split(" | ", 1)[0]                 # desc-pollution hatao
    s = re.sub(r"[_\-]+", " ", s)                    # snake/kebab → spaces
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)    # CamelCase → spaces
    s = re.sub(r"\s+", " ", s).strip()
    return s.title() if s else str(raw)


def label(alias, with_name=True):
    """Har display surface ke liye SINGLE gate. Registered → "NN.MM - Name".
    Non-strategy bucket → saaf naam (`manual`→"Manual"). Anjaana → beautified.
    Raw sirf tab jab registry load hi na hui ho (spasht "load nahi hua", TRAP
    #132) — warna kabhi raw ugly id leak nahi hota."""
    sid = resolve(alias)
    if sid:
        return sid if not with_name else f"{sid} - {get(sid).get('name', sid)}"
    if not strategies():          # registry load fail — honest raw, na ki jhoota naam
        return str(alias)
    key = ("" if alias is None else str(alias).strip()).lower()
    bl = bucket_labels()
    if key in bl:
        return bl[key]
    return _beautify(alias)


def family_of(sid):
    """(family_id, family_record) for a strategy ID, else (None, None)."""
    sid = resolve(sid) or str(sid)
    fam = sid.split(".")[0]
    return (fam, families().get(fam)) if fam in families() else (None, None)


def by_config_key(key):
    sid = resolve(key)
    return (sid, get(sid)) if sid else (None, None)


def _members(family):
    fam = str(family)
    return sorted(s for s in strategies() if s.split(".")[0] == fam)


def next_member(family):
    """Next free 'family.MM' ID (append-only — highest existing member + 1)."""
    fam = str(family).zfill(2)
    nums = [int(s.split(".")[1]) for s in _members(fam) if "." in s]
    return f"{fam}.{(max(nums) + 1 if nums else 1):02d}"


def next_family():
    """Next free family number (append-only)."""
    fams = [int(f) for f in families() if f.isdigit()]
    return f"{(max(fams) + 1 if fams else 0):02d}"


def tree():
    """Nested {family_id: {name, desc, members:[{id, ...record}]}} for display."""
    out = {}
    for fid, frec in sorted(families().items()):
        out[fid] = dict(frec)
        out[fid]["members"] = [dict(id=m, **strategies()[m]) for m in _members(fid)]
    return out


def add(family, name, config_key=None, slug=None, live_file=None, status="research",
        save=True):
    """Register a NEW strategy under `family` (creating the family row is a
    separate call). Returns the assigned permanent ID. Append-only."""
    reg = load()
    sid = next_member(family)
    reg["strategies"][sid] = {"name": name, "config_key": config_key, "slug": slug,
                              "live_file": live_file, "status": status}
    if save:
        with open(_JSON, "w", encoding="utf-8") as f:
            json.dump(reg, f, indent=2)
        load(force=True)
    return sid


def resolve_base(strategy_id, script_of, aliases=None):
    """config-id (e.g. "vrp_condor_v1") → uska trader BASE key (e.g. "vrp_condor").

    Ye logic pehle DO jagah alag-alag likhi thi — `trader_dashboard._base()` aur
    `health_check._base()` — aur TRAP #116 ka fix sirf pehli me laga. Doosri
    `split("_")[0]` hi karti rahi, to `vrp_condor_v1` uske liye "vrp" (straddle)
    resolve hota raha: 9:20 ka preflight condor ki jagah STRADDLE ka script
    compile-check karta tha (aur `_ops/signal_replay.py`, jo isi ko import karta
    hai, condor ka din-bhar ka verdict straddle ki signal-logic pe banata tha).
    `vrp_condor` ki sahi entry TRADER_SCRIPTS me maujood thi — bas kabhi reach
    nahi hoti thi. Isliye ab ek hi jagah: dono caller yahi bulate hain.

    Rule: ZYADA-SPECIFIC two-token base ("vrp_condor") ko one-token ("vrp") pe
    tabhi prefer karo jab wo ALAG script pe jaata ho. `rsi_v1` → "rsi" hi rehta
    hai (dono bases ka script ek hi 01_rsi_v1 hai → koi ambiguity nahi), aur
    `ARS_CHAIN_V1_PAPER` → "ARS_CHAIN" (two-token maujood, one-token "ARS" nahi).

    script_of — callable: base → uska script path (ya None). Caller apna map deta
                hai (dashboard ka STRATEGIES, health_check ka TRADER_SCRIPTS), to
                ye function un dono maps se azad rehta hai.
    aliases   — optional {first_token: base} map (dashboard ka STRATEGY_ALIASES).
    """
    parts = str(strategy_id).split('_')
    if len(parts) >= 2:
        two, one = f"{parts[0]}_{parts[1]}", parts[0]
        if script_of(two) is not None and script_of(two) != script_of(one):
            return two
    first = parts[0] if '_' in str(strategy_id) else str(strategy_id)
    return (aliases or {}).get(first, first)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        q = sys.argv[1]
        print(f"resolve({q!r}) -> {resolve(q)}  |  label -> {label(q)}")
    else:
        for fid, f in tree().items():
            print(f"{fid}  {f['name']} — {f.get('desc','')}")
            for m in f["members"]:
                ck = f" [{m['config_key']}]" if m.get("config_key") else ""
                print(f"    {m['id']}  {m['name']}  ({m['status']}){ck}")
