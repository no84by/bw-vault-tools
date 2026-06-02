"""bw-sync entrypoint: 3-way two-way sync between two profiles via a persisted snapshot."""
import sys
from dataclasses import dataclass

from . import (capabilities, checkpoint, content, identity, keyprovider, merge as mergemod, models,
               snapshot as snapmod)


@dataclass
class SyncResult:
    planned: int = 0
    applied: int = 0
    gated: int = 0
    guarded: int = 0
    suppressed: int = 0


def _org_fps(prof):
    """Fingerprints of a profile's org login items, so a create can be suppressed when the
    item already lives in that side's org (mirrors bw-dedup's org-awareness)."""
    if not hasattr(prof, "list_items"):
        return frozenset()
    return {identity.fingerprint(o) for o in capabilities.org_reference_items(prof)
            if models.is_login(o) and models.has_uris(o)}


def tty_approver(op) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"  {op.kind} on {op.target} ({op.item.get('name')})? [a]pprove/[s]kip: ").strip().lower() == "a"


def run_sync(prof_a, prof_b, snapshot_path, apply=True, key_provider=None,
             approver=tty_approver, run_dir="/dev/shm/bwvt-sync", conflict_policy="newest") -> SyncResult:
    if apply and key_provider is None:
        raise ValueError("key_provider required when apply=True")
    exp_a = prof_a.export()
    exp_b = prof_b.export()
    a = exp_a.get("items", [])
    b = exp_b.get("items", [])
    snap = snapmod.load(snapshot_path, key_provider) if key_provider else snapmod.Snapshot()
    result = mergemod.three_way(a, b, snap, org_a_fps=_org_fps(prof_a), org_b_fps=_org_fps(prof_b),
                                conflict_policy=conflict_policy)
    safe = [o for o in result.ops if not o.destructive and not o.guarded]
    gated = [o for o in result.ops if o.destructive]
    guarded = [o for o in result.ops if o.guarded]
    res = SyncResult(planned=len(result.ops), gated=len(gated), guarded=len(guarded),
                     suppressed=len(result.suppressed))
    print(f"sync plan: {len(safe)} safe, {len(gated)} gated, {len(guarded)} guarded "
          f"({len(result.ops)} total); {len(result.suppressed)} suppressed (already in target org).")
    if guarded:
        print("  held (passkey, can't round-trip via export): "
              + ", ".join(sorted(o.item.get("name", "?") for o in guarded)))
    if not apply:
        print("[--plan] dry-run; no changes.")
        return res
    run = checkpoint.RunDir(run_dir, key_provider)
    run.write_baseline("A", exp_a)                    # encrypted pre-mutation snapshot of both
    run.write_baseline("B", exp_b)                    # vaults — automatic, no manual export needed
    a_by = {i["id"]: i for i in a}
    b_by = {i["id"]: i for i in b}
    for op in safe:
        _apply(prof_a, prof_b, op, run, result.new_snapshot, a_by, b_by)
        res.applied += 1
    skipped = []
    for op in gated:
        if approver(op):
            _apply(prof_a, prof_b, op, run, result.new_snapshot, a_by, b_by)
            res.applied += 1
        else:
            skipped.append(op)
    if skipped:
        print(f"NOTE: {len(skipped)} destructive op(s) NOT applied (declined / non-interactive): "
              + ", ".join(o.item.get("name", "?") for o in skipped))
    snapmod.save(result.new_snapshot, snapshot_path, key_provider)
    return res


def _edit_to(run, prof, tid, cur, new_item):
    """Write new_item's content onto the target's own item (id), keeping its structural fields;
    record the pre-edit state for undo."""
    run.record(tid, {"action": "edit", "item_id": tid, "item": cur})
    prof.edit(tid, {**new_item, "id": tid, "folderId": cur.get("folderId"),
                    "organizationId": cur.get("organizationId"),
                    "collectionIds": cur.get("collectionIds"),
                    "revisionDate": cur.get("revisionDate")})


def _apply(prof_a, prof_b, op, run, new_snap, a_by, b_by):
    if op.kind == "create":
        prof = prof_a if op.target == "A" else prof_b
        created = prof.create(op.item)
        run.record(created["id"], {"action": "delete", "item_id": created["id"]})
        # capture the server-assigned id into the snapshot so the next run pairs correctly
        e = new_snap.by_a(op.item["id"]) if op.target == "B" else new_snap.by_b(op.item["id"])
        if e:
            if op.target == "B":
                e.id_on_b = created["id"]
            else:
                e.id_on_a = created["id"]
    elif op.kind == "edit":
        # one-sided change: write the source content to the OTHER vault's own item (by its id),
        # keeping that vault's structural fields so a foreign folderId/revision can't break it.
        e = new_snap.entries.get(op.link_id)
        if not e:
            return
        prof, tid, cur = ((prof_a, e.id_on_a, a_by.get(e.id_on_a)) if op.target == "A"
                          else (prof_b, e.id_on_b, b_by.get(e.id_on_b)))
        if tid and cur is not None:
            _edit_to(run, prof, tid, cur, op.item)
    elif op.kind == "merge":
        # divergence resolved by union: write the merged item to BOTH vaults so neither loses data.
        e = new_snap.entries.get(op.link_id)
        if not e:
            return
        for prof, tid, by in ((prof_a, e.id_on_a, a_by), (prof_b, e.id_on_b, b_by)):
            cur = by.get(tid)
            if tid and cur is not None:
                _edit_to(run, prof, tid, cur, op.item)
        e.content = content.content_key(op.item)       # both sides now == merged -> converges
    elif op.kind == "delete":
        prof = prof_a if op.target == "A" else prof_b
        run.record(op.item["id"], {"action": "restore", "item_id": op.item["id"]})
        prof.delete(op.item["id"])
    # ambiguous -> default keep: no write (flagged only)


def undo(prof_a, prof_b, run_dir, key_provider) -> int:
    run = checkpoint.RunDir(run_dir, key_provider)
    n = 0
    for inv in run.undo_plan():
        for prof in (prof_a, prof_b):
            try:
                if inv["action"] == "delete":
                    prof.delete(inv["item_id"])
                elif inv["action"] == "restore":
                    prof.restore(inv["item_id"])
                elif inv["action"] == "edit":
                    prof.edit(inv["item_id"], inv["item"])
                n += 1
                break
            except Exception:
                continue
    return n


def main() -> int:
    import argparse
    import getpass
    import os
    from . import bootstrap, bw_adapter
    bootstrap.ensure_cryptography()
    bootstrap.ensure_bw()
    ap = argparse.ArgumentParser(prog="bw-sync")
    ap.add_argument("--appdata-a", required=True)
    ap.add_argument("--appdata-b", required=True)
    ap.add_argument("--snapshot", help="path to the encrypted last-synced snapshot (plan/apply)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="RUN_DIR", help="reverse a prior sync from its journal")
    ap.add_argument("--conflict", choices=["newest", "a-wins", "b-wins"], default="newest",
                    help="how to resolve a divergence (default: newest revisionDate)")
    args = ap.parse_args()
    sa = os.environ.get("BW_SESSION_A") or _unlock(args.appdata_a, "A")
    sb = os.environ.get("BW_SESSION_B") or _unlock(args.appdata_b, "B")
    prof_a = bw_adapter.BwProfile(args.appdata_a, sa)
    prof_b = bw_adapter.BwProfile(args.appdata_b, sb)
    if args.undo:
        n = undo(prof_a, prof_b, args.undo,
                 keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: ")))
        print(f"undo: reversed {n} op(s).")
        return 0
    if not args.snapshot:
        ap.error("--snapshot is required for plan/apply")
    need_key = args.apply or os.path.exists(args.snapshot)
    kp = keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: ")) if need_key else None
    if args.apply:
        rd = checkpoint.new_run_dir("sync")
        res = run_sync(prof_a, prof_b, args.snapshot, apply=True, key_provider=kp, run_dir=rd,
                       conflict_policy=args.conflict)
        print(f"done: {res.applied} applied, {res.gated} gated, {res.guarded} guarded.")
        print(f'reversible: bw-sync --undo "{rd}"')
    else:
        res = run_sync(prof_a, prof_b, args.snapshot, apply=False, key_provider=kp,
                       conflict_policy=args.conflict)
    return 0


def _unlock(appdata, label):
    import getpass
    import os
    import subprocess
    env = dict(os.environ, BITWARDENCLI_APPDATA_DIR=appdata)
    pw = getpass.getpass(f"bw master password ({label}): ")
    return subprocess.run(["bw", "unlock", "--raw"], input=pw + "\n", text=True,
                          capture_output=True, check=True, env=env).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
