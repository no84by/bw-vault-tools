"""bw-dedup entrypoint: bootstrap -> export -> plan -> approve -> apply -> journal."""
import argparse
import sys
from dataclasses import dataclass

from . import capabilities, checkpoint, keyprovider, plan as planmod


@dataclass
class DedupResult:
    applied_destructive: int = 0
    preserved: int = 0


def tty_approver(op) -> bool:
    """Inline terminal gate. Non-TTY -> refuse destructive ops."""
    if not sys.stdin.isatty():
        return False
    tgt = getattr(op, "item_id", None) or getattr(op, "keep_id", None)
    return input(f"  apply {type(op).__name__} on {tgt}? [a]pprove/[s]kip: ").strip().lower() == "a"


def _merged_notes(group_items):
    notes = []
    for e in group_items:
        n = (e.get("notes") or "").strip()
        if n and n not in notes:
            notes.append(n)
    return "\n\n".join(notes) if notes else None


def _merged_fields(group_items):
    """Union of custom fields across a merge group, dedup by (name, value, type)."""
    out, seen = [], set()
    for e in group_items:
        for f in (e.get("fields") or []):
            k = (f.get("name"), f.get("value"), f.get("type"))
            if k not in seen:
                seen.add(k)
                out.append(f)
    return out


def _apply_destructive(prof, op, by_id, run):
    if isinstance(op, (planmod.DeleteOp, planmod.ClearPersonalDupOp)):
        if op.item_id in run.completed_ids:
            return
        run.record(op.item_id, op.inverse())
        prof.delete(op.item_id)
    elif isinstance(op, planmod.MergeOp):
        keep = dict(by_id[op.keep_id])
        keep["login"] = dict(keep["login"])
        keep["login"]["uris"] = [{"uri": u, "match": None} for u in op.uris]
        group = [by_id[op.keep_id]] + [by_id[d] for d in op.drop_ids]
        merged = _merged_notes(group)
        if merged:
            keep["notes"] = merged
        keep_totp = keep["login"].get("totp")         # keep the kept item's 2FA seed active, but
        extra_totps = [{"name": "totp (merged dup)", "value": t, "type": 1}   # never drop a dup's
                       for t in dict.fromkeys((e.get("login") or {}).get("totp") for e in group)
                       if t and t != keep_totp]
        keep["fields"] = _merged_fields(group) + extra_totps   # keep every side's custom fields
        if op.keep_id not in run.completed_ids:
            run.record(op.keep_id, op.inverse())
            prof.edit(op.keep_id, keep)
        for d in op.drop_ids:
            if d in run.completed_ids:
                continue
            run.record(d, {"action": "restore", "item_id": d})
            prof.delete(d)


def run_dedup(prof, approver=tty_approver, run_dir="/dev/shm/bwvt-run", apply=True,
              key_provider=None) -> DedupResult:
    if apply and key_provider is None:
        raise ValueError("key_provider required when apply=True (reversibility)")
    vault = prof.export()
    items = vault.get("items", [])
    by_id = {it["id"]: it for it in items}
    org_reference = capabilities.org_reference_items(prof) if hasattr(prof, "list_items") else []
    p = planmod.build_dedup_plan(items, vault.get("folders", []), org_reference=org_reference)
    assert p.check_invariant(set(by_id)), "preservation invariant violated"

    res = DedupResult(preserved=len(p.preserved_ids))
    print(f"plan: {len(p.gated_ops)} dedup op(s) merge/remove, "
          f"{len(p.preserved_ids)} preserved ({len(p.flagged_guard_ids)} guarded).")
    if p.flagged_guard_ids:
        held = sorted(by_id[i]["name"] for i in p.flagged_guard_ids if i in by_id)
        print("  held (passkey/SSH, never modified): " + ", ".join(held))
    if not apply:
        print("[--plan] dry-run; no changes.")
        return res

    run = checkpoint.RunDir(run_dir, key_provider)
    run.write_baseline("vault", vault)
    skipped = []
    for op in p.gated_ops:
        if approver(op):
            _apply_destructive(prof, op, by_id, run)
            res.applied_destructive += 1
        else:
            skipped.append(op)
    if skipped:
        print(f"NOTE: {len(skipped)} destructive op(s) NOT applied (declined / non-interactive): "
              + ", ".join(_op_name(o, by_id) for o in skipped))
    return res


def _op_name(op, by_id):
    if isinstance(op, planmod.MergeOp):
        return by_id.get(op.keep_id, {}).get("name", op.keep_id)
    return op.payload.get("name", op.item_id)


def main() -> int:
    import getpass
    import os
    from . import bootstrap, bw_adapter
    bootstrap.ensure_cryptography()
    bootstrap.ensure_bw()
    ap = argparse.ArgumentParser(prog="bw-dedup")
    ap.add_argument("--vault", required=True, help="profile label (for prompts)")
    ap.add_argument("--appdata", required=True, help="BITWARDENCLI_APPDATA_DIR for the profile")
    ap.add_argument("--apply", action="store_true", help="apply (default: --plan dry-run)")
    ap.add_argument("--undo", metavar="RUN_DIR", help="reverse a prior run from its journal")
    args = ap.parse_args()

    session = os.environ.get("BW_SESSION") or _unlock(args.appdata)
    prof = bw_adapter.BwProfile(args.appdata, session)
    if args.undo:
        return _undo(prof, args.undo)

    if args.apply:
        kp = keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: "))
        rd = checkpoint.new_run_dir("dedup")
        res = run_dedup(prof, run_dir=rd, apply=True, key_provider=kp)
        print(f'done: {res.applied_destructive} merge/remove applied, {res.preserved} preserved.')
        print(f'reversible: bw-dedup --undo "{rd}"')
    else:
        res = run_dedup(prof, apply=False)
    return 0


def _undo(prof, run_dir):
    import getpass
    run = checkpoint.RunDir(run_dir, keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: ")))
    for inv in run.undo_plan():
        action = inv["action"]
        if action == "restore":
            prof.restore(inv["item_id"])
        elif action == "edit":
            prof.edit(inv["item_id"], inv["item"])
            for rid in inv.get("restore_ids", []):
                prof.restore(rid)
    print("undo complete.")
    return 0


def _unlock(appdata):
    import getpass
    import os
    import subprocess
    env = dict(os.environ, BITWARDENCLI_APPDATA_DIR=appdata)
    pw = getpass.getpass("bw master password: ")
    return subprocess.run(["bw", "unlock", "--raw"], input=pw + "\n", text=True,
                          capture_output=True, check=True, env=env).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
