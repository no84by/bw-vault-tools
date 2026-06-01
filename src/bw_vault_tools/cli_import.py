"""bw-import entrypoint: ingest browser CSVs -> create only the logins not already in the vault."""
import sys
from dataclasses import dataclass

from . import checkpoint, import_plan as ipmod


@dataclass
class ImportResult:
    to_create: int = 0
    created: int = 0
    skipped: int = 0
    guarded: int = 0


def tty_approver(plan) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"  create {len(plan.creates)} new login(s)? [a]pprove/[s]kip: ").strip().lower() == "a"


def run_import(prof, candidates, run_dir="/dev/shm/bwvt-import", apply=True,
               key_provider=None, approver=tty_approver) -> ImportResult:
    if apply and key_provider is None:
        raise ValueError("key_provider required when apply=True (reversibility)")
    live = prof.export().get("items", [])
    plan = ipmod.build_import_plan(candidates, live)
    res = ImportResult(to_create=len(plan.creates), skipped=plan.skipped, guarded=plan.guarded)
    print(f"import plan: {len(plan.creates)} new, {plan.skipped} already present"
          f"{f', {plan.guarded} guarded' if plan.guarded else ''}.")
    if not apply:
        print("[--plan] dry-run; nothing created.")
        return res
    if not plan.creates or not approver(plan):
        return res
    run = checkpoint.RunDir(run_dir, key_provider)
    for op in plan.creates:
        created = prof.create(op.item)               # returns item with server-assigned id
        run.record(created["id"], op.inverse(created["id"]))
        res.created += 1
    return res


def undo(prof, run_dir, key_provider) -> int:
    run = checkpoint.RunDir(run_dir, key_provider)
    n = 0
    for inv in run.undo_plan():
        if inv.get("action") == "delete":
            prof.delete(inv["item_id"])
            n += 1
    print(f"undo: deleted {n} created item(s).")
    return n


def main() -> int:
    import argparse
    import getpass
    import os
    import time
    from . import bootstrap, bw_adapter, checkpoint, keyprovider, sources

    bootstrap.ensure_cryptography()
    bootstrap.ensure_bw()
    ap = argparse.ArgumentParser(prog="bw-import")
    ap.add_argument("--vault", required=True, help="profile label (for prompts)")
    ap.add_argument("--appdata", required=True, help="BITWARDENCLI_APPDATA_DIR for the profile")
    ap.add_argument("--apply", action="store_true", help="create new items (default: --plan)")
    ap.add_argument("--undo", metavar="RUN_DIR", help="reverse a prior import")
    args = ap.parse_args()

    session = os.environ.get("BW_SESSION") or _unlock(args.appdata)
    prof = bw_adapter.BwProfile(args.appdata, session)

    if args.undo:
        return undo(prof, args.undo,
                    keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: ")))

    candidates = _gather_candidates(sources, time)
    if args.apply:
        kp = keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: "))
        rd = checkpoint.new_run_dir("import")
        res = run_import(prof, candidates, run_dir=rd, apply=True, key_provider=kp)
        print(f"done: {res.created} created, {res.skipped} already present, {res.to_create} were new.")
        print(f'reversible: bw-import --undo "{rd}"')
    else:
        res = run_import(prof, candidates, apply=False)
        print(f"done: {res.created} created, {res.skipped} already present, {res.to_create} were new.")
    return 0


def _gather_candidates(sources, time) -> list:
    import os
    installed = sources.detect_browsers()
    found = sources.scan_for_exports([sources.downloads_dir(), os.getcwd()])
    paths = [(p, k) for p, k in found if k != "bitwarden_json"]   # import only browser CSVs
    if sys.stdin.isatty():
        present = {k for _, k in paths}
        for b in sorted(installed):
            kind = sources.browser_csv_kind(b)
            if not kind or kind in present:
                continue
            hit = sources.collect_source(b, {kind}, watch=sources.real_watch, ask=input, now=time.time)
            if hit:
                paths.append((hit, sources.classify_export(hit) or kind))
                present.add(kind)
    items = []
    for p, k in paths:
        items.extend(sources.csv_to_items(p, k))
    return items


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
