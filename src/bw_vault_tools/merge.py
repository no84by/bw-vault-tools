"""Pure 3-way merge: classify A-now / B-now / S-last into typed sync ops (spec §4)."""
import uuid
from dataclasses import dataclass, field

from . import content, identity, models, snapshot as snapmod


@dataclass
class SyncOp:
    kind: str            # create | edit | delete | conflict | ambiguous
    target: str          # "A" or "B" (where the change applies)
    item: dict
    link_id: str = ""
    destructive: bool = False
    guarded: bool = False
    note: str = ""


@dataclass
class MergeResult:
    ops: list = field(default_factory=list)
    new_snapshot: object = None


def _guard(item):
    return models.has_passkey(item) or models.item_type(item) == 5


def _pair_key(item):
    # cross-vault identity for first-run pairing: uri+username+type (password-agnostic)
    return identity.fingerprint(item, include_password=False)


def _op(kind, target, item, link_id="", destructive=False, guarded=False, note=""):
    if guarded:                       # a guarded item is never auto-destructively applied
        destructive = False
    return SyncOp(kind=kind, target=target, item=item, link_id=link_id,
                  destructive=destructive, guarded=guarded, note=note)


def three_way(a_items, b_items, snap):
    ops = []
    new = snapmod.Snapshot()
    a_by = {i["id"]: i for i in a_items}
    b_by = {i["id"]: i for i in b_items}
    seen_a, seen_b = set(), set()

    # 1) walk existing snapshot pairings
    for e in list(snap.entries.values()):
        a = a_by.get(e.id_on_a)
        b = b_by.get(e.id_on_b)
        if a:
            seen_a.add(a["id"])
        if b:
            seen_b.add(b["id"])
        base = tuple(e.content)
        if a and b:
            ca, cb = content.content_key(a), content.content_key(b)
            a_ed, b_ed = ca != base, cb != base
            if not a_ed and not b_ed:
                new.record(e.link_id, a["id"], b["id"], base, e.canonical)
            elif a_ed and not b_ed:
                ops.append(_op("edit", "B", a, e.link_id, guarded=_guard(a)))
                new.record(e.link_id, a["id"], b["id"], ca, a)
            elif b_ed and not a_ed:
                ops.append(_op("edit", "A", b, e.link_id, guarded=_guard(b)))
                new.record(e.link_id, a["id"], b["id"], cb, b)
            else:
                winner = a if a.get("revisionDate", "") >= b.get("revisionDate", "") else b
                tgt = "B" if winner is a else "A"
                ops.append(_op("conflict", tgt, winner, e.link_id, destructive=True, guarded=_guard(winner)))
                new.record(e.link_id, a["id"], b["id"], content.content_key(winner), winner)
        elif a and not b:
            if content.content_key(a) == base:
                ops.append(_op("delete", "A", a, e.link_id, destructive=True, guarded=_guard(a)))
            else:
                ops.append(_op("ambiguous", "A", a, e.link_id, destructive=True, guarded=_guard(a)))
                new.record(e.link_id, a["id"], "", content.content_key(a), a)
        elif b and not a:
            if content.content_key(b) == base:
                ops.append(_op("delete", "B", b, e.link_id, destructive=True, guarded=_guard(b)))
            else:
                ops.append(_op("ambiguous", "B", b, e.link_id, destructive=True, guarded=_guard(b)))
                new.record(e.link_id, "", b["id"], content.content_key(b), b)
        # both gone -> synced delete, drop from snapshot

    # 2) unmatched items: first-run pairing by content, else create on the other side
    a_left = [i for i in a_items if i["id"] not in seen_a]
    b_left = [i for i in b_items if i["id"] not in seen_b]
    b_index = {}
    for i in b_left:
        b_index.setdefault(_pair_key(i), []).append(i)
    for a in a_left:
        match = b_index.get(_pair_key(a))
        if match:                               # same logical item both sides, never synced -> pair
            b = match.pop(0)
            new.record(str(uuid.uuid4()), a["id"], b["id"], content.content_key(a), a)
        else:
            ops.append(_op("create", "B", a, guarded=_guard(a)))
            new.record(str(uuid.uuid4()), a["id"], "", content.content_key(a), a)
    for leftover in (i for lst in b_index.values() for i in lst):
        ops.append(_op("create", "A", leftover, guarded=_guard(leftover)))
        new.record(str(uuid.uuid4()), "", leftover["id"], content.content_key(leftover), leftover)

    return MergeResult(ops=ops, new_snapshot=new)
