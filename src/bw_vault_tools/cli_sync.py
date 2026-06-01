"""bw-sync entrypoint: 3-way two-way sync between two profiles via a persisted snapshot."""
import sys
from dataclasses import dataclass

from . import checkpoint, keyprovider, merge as mergemod, snapshot as snapmod, tmpfs


@dataclass
class SyncResult:
    planned: int = 0
    applied: int = 0
    gated: int = 0
    guarded: int = 0


def tty_approver(op) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"  {op.kind} on {op.target} ({op.item.get('name')})? [a]pprove/[s]kip: ").strip().lower() == "a"


def run_sync(prof_a, prof_b, snapshot_path, apply=True, key_provider=None,
             approver=tty_approver, run_dir="/dev/shm/bwvt-sync") -> SyncResult:
    if apply and key_provider is None:
        raise ValueError("key_provider required when apply=True")
    a = prof_a.export().get("items", [])
    b = prof_b.export().get("items", [])
    snap = snapmod.load(snapshot_path, key_provider) if key_provider else snapmod.Snapshot()
    result = mergemod.three_way(a, b, snap)
    safe = [o for o in result.ops if not o.destructive and not o.guarded]
    gated = [o for o in result.ops if o.destructive]
    guarded = [o for o in result.ops if o.guarded]
    res = SyncResult(planned=len(result.ops), gated=len(gated), guarded=len(guarded))
    print(f"sync plan: {len(safe)} safe, {len(gated)} gated, {len(guarded)} guarded "
          f"({len(result.ops)} total).")
    if not apply:
        print("[--plan] dry-run; no changes.")
        return res
    run = checkpoint.RunDir(run_dir, key_provider)
    for op in safe:
        _apply(prof_a, prof_b, op, run, result.new_snapshot)
        res.applied += 1
    for op in gated:
        if approver(op):
            _apply(prof_a, prof_b, op, run, result.new_snapshot)
            res.applied += 1
    snapmod.save(result.new_snapshot, snapshot_path, key_provider)
    return res


def _apply(prof_a, prof_b, op, run, new_snap):
    prof = prof_a if op.target == "A" else prof_b
    if op.kind == "create":
        created = prof.create(op.item)
        run.record(created["id"], {"action": "delete", "item_id": created["id"]})
        # capture the server-assigned id into the snapshot so the next run pairs correctly
        if op.target == "B":
            e = new_snap.by_a(op.item["id"])
            if e:
                e.id_on_b = created["id"]
        else:
            e = new_snap.by_b(op.item["id"])
            if e:
                e.id_on_a = created["id"]
    elif op.kind in ("edit", "conflict"):
        run.record(op.item["id"], {"action": "edit", "item_id": op.item["id"], "item": op.item})
        prof.edit(op.item["id"], op.item)
    elif op.kind == "delete":
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
    ap.add_argument("--snapshot", required=True, help="path to the encrypted last-synced snapshot")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sa = os.environ.get("BW_SESSION_A") or _unlock(args.appdata_a, "A")
    sb = os.environ.get("BW_SESSION_B") or _unlock(args.appdata_b, "B")
    prof_a = bw_adapter.BwProfile(args.appdata_a, sa)
    prof_b = bw_adapter.BwProfile(args.appdata_b, sb)
    need_key = args.apply or os.path.exists(args.snapshot)
    kp = keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: ")) if need_key else None
    with tmpfs.tmpfs_dir() as rd:
        res = run_sync(prof_a, prof_b, args.snapshot, apply=args.apply, key_provider=kp, run_dir=rd)
    print(f"done: {res.applied} applied, {res.gated} gated, {res.guarded} guarded.")
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
