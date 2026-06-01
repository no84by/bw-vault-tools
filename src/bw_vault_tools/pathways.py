"""Report the org→target capacity gap and offer programmable pathways (strategy choices).

A pathway maps each source org to one or more target collection names. The choice is resolved
either from a config key (`--org-strategy`) or an interactive chooser — same decision, scripted
or human. Pure: no IO.
"""
from dataclasses import dataclass, field


@dataclass
class Pathway:
    key: str                     # per-org-collection | full-mirror | personal-only
    label: str
    mapping: dict = field(default_factory=dict)   # source-org-id -> target-collection-name


def viable_pathways(source_orgs, max_collections) -> list:
    """`source_orgs`: [{id, name, collections:[...]}]. `max_collections`: int|None (None=unlimited).
    Returns the pathways that actually fit the target capacity."""
    out = [Pathway("per-org-collection", "One collection per org",
                   {o["id"]: o["name"] for o in source_orgs})]
    total_collections = sum(len(o.get("collections", [])) for o in source_orgs)
    n_orgs = len(source_orgs)
    fits_per_org = max_collections is None or max_collections >= n_orgs
    if not fits_per_org:
        out = []                 # per-org doesn't even fit
    if max_collections is None or max_collections >= total_collections:
        out.append(Pathway("full-mirror", "Full structural mirror (every collection 1:1)",
                           {o["id"]: o["name"] for o in source_orgs}))
    out.append(Pathway("personal-only", "Skip org sync (personal items only)", {}))
    return out


def report(source_orgs, max_collections, source_label="source", target_label="target") -> str:
    """Human-readable summary of the capacity gap."""
    n = len(source_orgs)
    cap = "unlimited" if max_collections is None else str(max_collections)
    lines = [f"Source ({source_label}) orgs: {n}"]
    for o in source_orgs:
        lines.append(f"  - {o['name']} ({len(o.get('collections', []))} collections, "
                     f"{o.get('item_count', '?')} items)")
    lines.append(f"Target ({target_label}) collection capacity: {cap}")
    if max_collections is not None and n > max_collections:
        lines.append(f"Gap: {n} orgs vs {max_collections}-collection cap -> consolidation needed")
    elif max_collections is not None and n == max_collections:
        lines.append(f"Fits: {n} orgs -> one collection each (exact)")
    else:
        lines.append("Fits: full mirror possible")
    return "\n".join(lines)


def resolve(pathways, choice_key=None, chooser=None):
    """Resolve to one pathway: by config key (wins), else an interactive chooser(pathways)->Pathway.
    Returns None if nothing resolves."""
    if choice_key:
        return next((p for p in pathways if p.key == choice_key), None)
    if chooser:
        return chooser(pathways)
    return None
