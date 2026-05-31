"""Typed plan operations and the dedup planner. Pure (no IO)."""
from collections import defaultdict
from dataclasses import dataclass, field

from . import identity, models

_REUSE_NOTE = "[bw-dedup] [!] This password is reused across multiple sites."


@dataclass
class AssignFolderOp:
    item_id: str
    folder_id: str
    folder_name: str
    destructive: bool = field(default=False, init=False)

    def inverse(self) -> dict:
        return {"action": "edit", "item_id": self.item_id, "clear_folder": True}


@dataclass
class FlagReusedOp:
    item_id: str
    note: str
    destructive: bool = field(default=False, init=False)

    def inverse(self) -> dict:
        return {"action": "noop"}            # appended note; safe to leave on undo


@dataclass
class DeleteOp:
    """Soft-delete an exact-duplicate loser. payload kept for record only."""
    item_id: str
    payload: dict
    destructive: bool = field(default=True, init=False)

    def inverse(self) -> dict:
        return {"action": "restore", "item_id": self.item_id}


@dataclass
class MergeOp:
    """Keep keep_id with unioned uris (+merged notes); soft-delete each drop_id."""
    keep_id: str
    drop_ids: list
    uris: list
    keep_before: dict                         # pre-edit snapshot of keep, for undo
    destructive: bool = field(default=True, init=False)

    def inverse(self) -> dict:
        return {"action": "edit", "item_id": self.keep_id, "item": self.keep_before,
                "restore_ids": list(self.drop_ids)}


def reused_passwords(items: list[dict]) -> set[str]:
    """Passwords used on >1 distinct domain. Computed over the FULL item set so a
    preserved/passkey item sharing a password still triggers a flag on its twin."""
    pw_domains: dict[str, set[str]] = defaultdict(set)
    for e in items:
        if not models.is_login(e):
            continue
        login = e["login"]
        pw = login.get("password")
        if not (pw and isinstance(pw, str) and pw.strip()):
            continue
        for u in (login.get("uris") or []):
            pw_domains[pw.strip()].add(identity.normalize_uri(u.get("uri")))
    return {pw for pw, doms in pw_domains.items() if len(doms) > 1}


def _folder_op(item: dict, folder_ids: dict) -> "AssignFolderOp | None":
    """Pure: assign an existing folder whose name is a substring of the username, only if
    folderId is empty. Ported from v2.0 assign_folder_id."""
    if item.get("folderId"):
        return None
    uname = (item.get("login") or {}).get("username") or ""
    for name, fid in folder_ids.items():
        if name in uname:
            return AssignFolderOp(item_id=item["id"], folder_id=fid, folder_name=name)
    return None


@dataclass
class Plan:
    ops: list = field(default_factory=list)
    preserved_ids: set = field(default_factory=set)
    kept_ids: set = field(default_factory=set)            # group winners we kept/edited
    flagged_passkey_ids: set = field(default_factory=set)
    flagged_guard_ids: set = field(default_factory=set)

    @property
    def safe_ops(self):
        return [o for o in self.ops if not o.destructive]

    @property
    def gated_ops(self):
        return [o for o in self.ops if o.destructive]

    def removed_ids(self) -> set:
        out = set()
        for o in self.ops:
            if isinstance(o, DeleteOp):
                out.add(o.item_id)
            elif isinstance(o, MergeOp):
                out.update(o.drop_ids)
        return out

    def check_invariant(self, input_ids: set) -> bool:
        """Cornerstone 4: nothing silently lost.
        (a) we only removed inputs; (b) every input is accounted for as
        removed, preserved, or a kept group-winner."""
        removed = self.removed_ids()
        accounted = self.preserved_ids | self.kept_ids | removed
        return removed <= input_ids and input_ids <= accounted


def build_dedup_plan(items: list[dict], folders: list[dict]) -> Plan:
    p = Plan()
    folder_ids = {f["name"]: f["id"] for f in folders}
    reused = reused_passwords(items)                       # full set (see reused_passwords)

    dedup_input = []
    for it in items:
        if models.is_login(it) and models.has_passkey(it):
            p.preserved_ids.add(it["id"])
            p.flagged_passkey_ids.add(it["id"])
            p.flagged_guard_ids.add(it["id"])
        elif models.item_type(it) == 5:
            p.preserved_ids.add(it["id"])
            p.flagged_guard_ids.add(it["id"])
        elif models.is_login(it) and models.has_uris(it):
            dedup_input.append(it)
        else:                                              # non-login / no-URI login / unknown
            p.preserved_ids.add(it["id"])

    for group in identity.group_logins(dedup_input).values():
        if len(group) == 1:
            kept = group[0]
        else:
            all_same = all(                                # exact-dup test (mirrors v2.0): keep first
                e["login"].get("password") == group[0]["login"].get("password")
                and e.get("revisionDate") == group[0].get("revisionDate")
                and e.get("creationDate") == group[0].get("creationDate")
                for e in group)
            if all_same:
                kept = group[0]
                for e in group[1:]:
                    p.ops.append(DeleteOp(item_id=e["id"], payload=e))
            else:                                          # variants -> merge uris+notes into best
                kept = identity.pick_best(group, reused)
                losers = [e for e in group if e["id"] != kept["id"]]
                p.ops.append(MergeOp(keep_id=kept["id"], drop_ids=[e["id"] for e in losers],
                                     uris=identity.merge_uris(group), keep_before=kept))
        p.kept_ids.add(kept["id"])
        fo = _folder_op(kept, folder_ids)
        if fo:
            p.ops.append(fo)
        if (kept["login"].get("password") or "") in reused:
            p.ops.append(FlagReusedOp(item_id=kept["id"], note=_REUSE_NOTE))
    return p
