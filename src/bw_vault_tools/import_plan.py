"""Pure: decide which candidate logins to create (not already in the live vault)."""
from dataclasses import dataclass, field

from . import identity, models


@dataclass
class CreateOp:
    item: dict
    destructive: bool = field(default=False, init=False)   # bw create is additive

    def inverse(self, created_id):
        return {"action": "delete", "item_id": created_id}


@dataclass
class ImportPlan:
    creates: list = field(default_factory=list)
    skipped: int = 0
    guarded: int = 0


def build_import_plan(candidates: list, live_items: list) -> ImportPlan:
    """Create a candidate only if its fingerprint is absent from the live vault and not a
    duplicate of an earlier candidate. Never edits/deletes anything (additive-only)."""
    p = ImportPlan()
    seen = set()
    for it in live_items:
        if models.is_login(it) and models.has_uris(it):
            seen.add(identity.fingerprint(it))
    for c in candidates:
        # defensive guard: never auto-create a passkey/SSH item (browser CSVs carry neither)
        if models.has_passkey(c) or models.item_type(c) == 5:
            p.guarded += 1
            continue
        if not (models.is_login(c) and models.has_uris(c)):
            p.skipped += 1                       # no URI -> cannot fingerprint -> skip create
            continue
        fp = identity.fingerprint(c)
        if fp in seen:
            p.skipped += 1
            continue
        seen.add(fp)
        p.creates.append(CreateOp(item=c))
    return p
