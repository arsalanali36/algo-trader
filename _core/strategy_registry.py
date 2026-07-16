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
    config_key, or hub slug (case-insensitive on keys/slugs). None if unknown."""
    if alias is None:
        return None
    a = str(alias)
    st = strategies()
    if a in st:
        return a
    al = a.lower()
    for sid, r in st.items():
        if (r.get("config_key") or "").lower() == al:
            return sid
        if (r.get("slug") or "").lower() == al:
            return sid
    return None


def label(alias, with_name=True):
    """"NN.MM - Name" for display. Falls back to the raw alias if unknown (so a
    brand-new strategy not yet registered still shows *something*)."""
    sid = resolve(alias)
    if not sid:
        return str(alias)
    if not with_name:
        return sid
    return f"{sid} - {get(sid).get('name', sid)}"


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
