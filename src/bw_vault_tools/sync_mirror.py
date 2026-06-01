"""Build a capacity-adaptive org→target-org mirror plan from a chosen pathway. Pure.

Additive-only: items not already present in the target (by fingerprint) are created into the
mapped collection; collections are created if missing. Nothing in the source is ever modified;
existing target items are never edited or deleted. Items are unlimited on every tier, so no
login is dropped — only collection structure flattens per the pathway/cap.
"""
from dataclasses import dataclass, field

from . import identity, models


@dataclass
class MirrorPlan:
    collections_to_create: list = field(default_factory=list)   # collection names
    item_creates: list = field(default_factory=list)            # [(item, collection_name)]
    skipped: int = 0                                            # already present in target
    guarded: int = 0                                           # passkey/SSH excluded


def build_mirror_plan(source_items_by_org, pathway, target_items, target_collections) -> MirrorPlan:
    """`source_items_by_org`: {org_id: [items]}. `pathway.mapping`: {org_id: target_collection_name}.
    `target_items`: existing items in the target org. `target_collections`: existing collection names.
    Creates only logins not already in the target (additive), into the mapped collection."""
    p = MirrorPlan()
    target_fps = {identity.fingerprint(i) for i in target_items
                  if models.is_login(i) and models.has_uris(i)}
    have_collections = set(target_collections)
    needed_collections = set()
    for org_id, items in source_items_by_org.items():
        coll = pathway.mapping.get(org_id)
        if not coll:
            continue                                  # org not selected by this pathway
        if coll not in have_collections and coll not in needed_collections:
            needed_collections.add(coll)
        for it in items:
            if models.has_passkey(it) or models.item_type(it) == 5:
                p.guarded += 1
                continue
            if not (models.is_login(it) and models.has_uris(it)):
                p.skipped += 1
                continue
            fp = identity.fingerprint(it)
            if fp in target_fps:
                p.skipped += 1
                continue
            target_fps.add(fp)                        # avoid duplicate creates within the run
            p.item_creates.append((it, coll))
    p.collections_to_create = sorted(needed_collections)
    return p
