"""The weekly cascade: import -> dedup A -> dedup B -> two-way sync, run unattended and
conservatively. Loss-free work applies automatically; anything destructive is held and reported.
"""
from . import backup, checkpoint, cli_dedup, cli_import, cli_sync, sources

from . import gateway, policy


def run(prof_a, prof_b, snapshot_path, key_provider, conflict_policy="a-wins",
        scan_dirs=None, notify=None, require_backup=True, backup_store=None,
        dedup=cli_dedup.run_dedup, sync=cli_sync.run_sync, imp=cli_import.run_import,
        scan=None):
    """Execute the cascade. Returns {summary, held, digest}. Tool fns are injectable for tests."""
    held = []
    approver = policy.make_approver(held)
    stages = []
    applied = 0

    # 0) SAFEGUARD: before anything mutates, secure a validated, encrypted, up-to-date backup of
    #    BOTH vaults on durable store. Because this runs *before* import/dedup/sync, the snapshot
    #    captures the true pre-cascade state — so a wrong run can be inspected/rolled back from a
    #    snapshot that postdates every prior applied sync. It is complementary to the per-run
    #    `--undo` baseline (that covers reversible per-op rollback *inside* a run); this covers a
    #    durable, validated pre-state on persistent storage. Raises BackupUnavailable if no valid
    #    backup can be made, and the caller MUST then refuse to run (nothing is mutated).
    if require_backup:
        info = backup.enforce_store_backup(prof_a.export(), prof_b.export(), key_provider,
                                           backup_store=backup_store)
        stages.append(("backup", f"{info.dir} validated on store"))

    # 1) import (additive-only): ingest browser exports already on disk. Creating new logins can't
    #    lose data, so it auto-applies; nothing is staged autonomously, so an empty scan is a no-op.
    cands = []
    for path, kind in (scan or sources.scan_for_exports)(scan_dirs or [sources.downloads_dir()]):
        if kind != "bitwarden_json":
            cands += sources.csv_to_items(path, kind)
    if cands:
        ri = imp(prof_a, cands, run_dir=checkpoint.new_run_dir("cascade-import"),
                 apply=True, key_provider=key_provider, approver=lambda plan: True)
        applied += ri.created
        stages.append(("import->A", f"{ri.created} new login(s)"))

    # 2) dedup each vault. Every dedup op is loss-free (union merge / exact-dup / org-clear-kept),
    #    so the conservative approver lets them through.
    for label, prof in (("A", prof_a), ("B", prof_b)):
        rd = dedup(prof, approver=approver, run_dir=checkpoint.new_run_dir(f"cascade-dedup{label}"),
                   apply=True, key_provider=key_provider)
        applied += rd.applied_destructive
        stages.append((f"dedup {label}", f"{rd.applied_destructive} merged/removed"))

    # 3) two-way sync. Safe ops (creates, one-sided edits, lossless field-union merges) auto-apply;
    #    password clashes / cross-vault deletes / ambiguous are HELD by the approver.
    rs = sync(prof_a, prof_b, snapshot_path, apply=True, key_provider=key_provider,
              approver=approver, run_dir=checkpoint.new_run_dir("cascade-sync"),
              conflict_policy=conflict_policy)
    applied += rs.applied
    stages.append(("sync A<->B", f"{rs.applied} applied, {rs.gated} gated"))

    summary = {"applied": applied, "stages": stages}
    digest = gateway.summarize(summary, held)
    if notify:
        notify(digest)
    return {"summary": summary, "held": held, "digest": digest}

class VaultUnavailable(RuntimeError):
    """A vault profile is not usable for this run (stale/locked session, unreachable server).

    Raised by the preflight so an unusable vault dies HERE, with a cause a human can act on,
    instead of surfacing ~40 lines deeper as a JSONDecodeError: a locked `bw` prints its prompt
    to stderr, writes a bare newline to stdout and still exits 0, so the adapter feeds "\\n"
    straight into json.loads() ("Expecting value: line 2 column 1").
    """


# Re-seal the matching session cred — the one systemd LoadCredentialEncrypted sources
# BW_SESSION_{A,B} from (a `bw-run --reseal` seals a *different* cred, bw-claude-session.cred).
# Only the cred filename is named here (an estate convention); the full credstore path lives in
# the RUNBOOK, so the shipped package carries no host-specific path in its error text.
_CRED_HINT = {"A": "bw-cascade-session-a.cred", "B": "bw-cascade-session-b.cred"}


def _brief(text: str, limit: int = 200) -> str:
    """Collapse a `bw` message to one short line for an error string (never carries the session:
    it is passed by env, so it is not echoed in bw's output)."""
    return " ".join((text or "").split())[:limit] or "(no output)"


def _prepare(appdata, session_env, label="?"):
    """Use a pre-unlocked session (from the credstore — bw sessions persist until lock/key-rotation,
    so no master password or 2FA is involved), PREFLIGHT that it is genuinely unlocked, then refresh
    the local cache (`bw sync`) so an edit can't fail on a stale cipher. Returns the session.

    Fails fast and loudly: every failure mode below used to reach the caller as an opaque parse
    error one or two stages into the cascade.
    """
    import json
    import os
    import subprocess

    session = os.environ.get(session_env)
    if not session:
        raise VaultUnavailable(
            f"vault {label}: ${session_env} is empty/unset — systemd did not populate it; "
            f"check LoadCredentialEncrypted and {_CRED_HINT.get(label, 'the sealed cred')}")

    env = dict(os.environ, BITWARDENCLI_APPDATA_DIR=appdata, BW_SESSION=session)

    # Preflight. `bw status` is the one cheap call that distinguishes "session is stale/locked" from
    # every other failure -- and it is the ONLY one that does: a locked `bw` exits 0 from both
    # `export` and `sync`, so neither check=True nor a returncode test can catch it. Note bw reports
    # the lock in the JSON *body*, not the exit status, so parse the body.
    p = subprocess.run(["bw", "status"], env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise VaultUnavailable(
            f"vault {label}: `bw status` failed (rc={p.returncode}) — {_brief(p.stderr or p.stdout)}")
    try:
        status = json.loads(p.stdout).get("status")
    except json.JSONDecodeError:
        raise VaultUnavailable(
            f"vault {label}: `bw status` returned non-JSON — {_brief(p.stdout or p.stderr)}") from None
    if status != "unlocked":
        raise VaultUnavailable(
            f"vault {label} {status} — the sealed session is stale; re-seal it into "
            f"{_CRED_HINT.get(label, 'the sealed cred')} (RUNBOOK § Re-sealing a stale session)")

    # `bw sync` refreshes the local cache. It ran unchecked before; that did NOT hide the lock (a
    # locked sync still prints "Syncing complete." and exits 0 — the preflight above is what catches
    # that), but it did hide genuine failures such as an unreachable server. Propagate them.
    s = subprocess.run(["bw", "sync"], env=env, capture_output=True, text=True)
    if s.returncode != 0:
        raise VaultUnavailable(
            f"vault {label}: `bw sync` failed (rc={s.returncode}) — {_brief(s.stderr or s.stdout)}")
    return session


def main() -> int:
    import argparse
    import os
    import sys
    from . import bw_adapter, keyprovider
    ap = argparse.ArgumentParser(prog="bw-vault-cascade")
    ap.add_argument("--appdata-a", required=True)
    ap.add_argument("--appdata-b", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--conflict", default="a-wins", choices=["newest", "a-wins", "b-wins"],
                    help="divergence policy; a-wins keeps the self-hosted primary canonical (default)")
    ap.add_argument("--preflight-only", action="store_true",
                    help="check both vault sessions are usable, then exit — mutates nothing")
    ap.add_argument("--no-backup", dest="require_backup", action="store_false", default=True,
                    help="DEBUG/override: skip the validated-backup guard before syncing (NOT "
                         "recommended — the safeguard exists to prevent data loss)")
    ap.add_argument("--backup-dir", dest="backup_dir",
                    help="override the durable backup store (default: "
                         "data_home()/bw-vault-tools/backups)")
    args = ap.parse_args()
    # Sessions + snapshot passphrase come from the environment, populated by systemd
    # LoadCredentialEncrypted from the TPM2 credstore — never prompted, never on disk in plaintext.
    # Both vaults are preflighted BEFORE any stage runs, so an unusable vault costs nothing.
    try:
        sa = _prepare(args.appdata_a, "BW_SESSION_A", "A")
        sb = _prepare(args.appdata_b, "BW_SESSION_B", "B")
    except VaultUnavailable as e:
        print(f"bw-vault-cascade: PREFLIGHT FAILED — {e}", file=sys.stderr)
        return 2   # distinct from a mid-run failure: nothing was touched
    if args.preflight_only:
        print("bw-vault-cascade: preflight OK — both vaults unlocked and synced (nothing applied).")
        return 0
    pa = bw_adapter.BwProfile(args.appdata_a, sa)
    pb = bw_adapter.BwProfile(args.appdata_b, sb)
    kp = keyprovider.PassphraseProvider(os.environ["BWVT_SNAPSHOT_PASSPHRASE"])
    try:
        out = run(pa, pb, args.snapshot, kp, conflict_policy=args.conflict, notify=print,
                  require_backup=args.require_backup, backup_store=args.backup_dir)
    except backup.BackupUnavailable as e:
        # Safeguard tripped: nothing was mutated. Secure a backup and re-run.
        print(f"bw-vault-cascade: REFUSING TO RUN — {e}", file=sys.stderr)
        return 2
    return 0   # held items are expected (held for review), not a failure


if __name__ == "__main__":
    raise SystemExit(main())
