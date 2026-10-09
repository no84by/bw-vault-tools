"""Pre-mutation backup gate: before the cascade mutates anything, guarantee a validated, encrypted
snapshot of BOTH vaults is on durable store.

This is a DURABLE, validated snapshot safety net for the unattended cascade — *complementary to*,
not a replacement for, the per-run `RunDir` baseline + op journal that `--undo` uses for reversible
per-op rollback inside a single run. `RunDir` covers in-run reversibility; this covers "a known-good
pre-cascade state exists on persistent storage, proven decryptable, retained, and pointed to by a
`latest.json` index" — so a run that goes wrong can be inspected and the vault rolled back from a
snapshot that postdates every prior applied sync.

What it is NOT: a Bitwarden account-disaster image. A full account restore needs server-side
`sqlite` / `bw restore`; a JSON export is not a same-account image (a `bw import` lands into a NEW
vault). See the README disclaimer. This module does not claim disaster recovery.

Exact guarantee, before ANY mutation in the cascade:
  - an ENCRYPTED export of both vaults exists on local disk,
  - each export is decrypt-round-trip VALIDATED (proves it is not corrupt and the key provider is
    correct),
  - it is the state as-of the moment *before* this run (the gate runs before import),
  - it is retained (retention), with a `latest.json` pointer to the newest good copy.
If a valid, up-to-date backup CANNOT be produced/validated, `enforce_store_backup` raises
`BackupUnavailable` and the caller MUST refuse to mutate either vault.

Security note: the on-disk index and any manifest are secret-free. Fingerprints are `sha256` of the
*encrypted* blob bytes (`sha256(file)`), an integrity/tamper marker that cannot be abused to
brute-force a password (unlike `sha256` of `content.content_key`, which folds in the raw
password/totp). Nothing sensitive is written in the clear.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass

from . import checkpoint


class BackupUnavailable(RuntimeError):
    """A validated, up-to-date backup could NOT be secured before the run. The caller MUST refuse
    to mutate either vault — this is the safeguard, not a soft warning."""


@dataclass
class BackupInfo:
    dir: str
    ts: float
    vault_a_items: int
    vault_b_items: int


def _now() -> float:
    return time.time()


def store_dir(backup_store: str | None = None) -> str:
    """The durable backup store. Explicit `backup_store` wins; else the `BW_VAULT_BACKUP_DIR` env;
    else `data_home()/bw-vault-tools/backups/`."""
    d = backup_store or os.environ.get("BW_VAULT_BACKUP_DIR")
    if d:
        return os.path.abspath(os.path.expanduser(d))
    return os.path.join(checkpoint.data_home(), "bw-vault-tools", "backups")


def _fingerprint(blob: bytes) -> str:
    """sha256 of the ENCRYPTED bytes — integrity/tamper fingerprint only. Secret-free: it is over
    ciphertext, so it cannot be used to check a guessed password."""
    return hashlib.sha256(blob).hexdigest()


def _run_dir_ts(name: str) -> float | None:
    """Parse the trailing UTC `%Y%m%dT%H%M%SZ` from a checkpoint-style run/backup dir name."""
    m = re.search(r"(\d{8}T\d{6}Z)$", name)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), "%Y%m%dT%H%M%SZ"))
    except ValueError:
        return None


def last_sync_ts() -> float | None:
    """UTC epoch of the most recent applied sync run (`run/sync-*` / `run/cascade-sync-*`). `None`
    if there have been none — so the FIRST run does not crash on the `ts >= last_sync` comparison."""
    runs = os.path.join(checkpoint.data_home(), "bw-vault-tools", "runs")
    try:
        entries = os.listdir(runs)
    except FileNotFoundError:
        return None
    best: float | None = None
    for e in entries:
        if not (e.startswith("sync-") or e.startswith("cascade-sync-")):
            continue
        if not os.path.isdir(os.path.join(runs, e)):
            continue
        t = _run_dir_ts(e)
        if t is not None and (best is None or t > best):
            best = t
    return best


def _manifest_path(store: str) -> str:
    return os.path.join(store, "latest.json")


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _write_bytes_atomic(path: str, data: bytes) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_text_atomic(path: str, text: str) -> None:
    _write_bytes_atomic(path, text.encode("utf-8"))


def _read_index(store: str) -> dict | None:
    try:
        return json.loads(_read_bytes(_manifest_path(store)).decode("utf-8"))
    except (OSError, ValueError):
        return None


def _safe_rmdir(d: str) -> None:
    shutil.rmtree(d, ignore_errors=True)


def _validate_dir(store: str, idx: dict, kp, last_sync: float | None, now: float,
                  staleness_hours: float) -> bool:
    """True iff the indexed backup is RESTORABLE (both vaults decrypt to dicts) AND the stored
    fingerprints match the on-disk ciphertext (no post-index tamper/corruption) AND UP-TO-DATE (not
    older than the last applied sync; within the absolute staleness cap)."""
    d = idx.get("dir")
    if not d or not os.path.isdir(os.path.join(store, d)):
        return False
    try:
        a = json.loads(kp.decrypt(_read_bytes(os.path.join(store, d, "vault-A.json.enc"))))
        b = json.loads(kp.decrypt(_read_bytes(os.path.join(store, d, "vault-B.json.enc"))))
    except Exception:
        return False                       # not restorable (wrong key / corrupt) -> invalid
    if a is None or b is None:
        return False
    # fingerprint check: the stored sha256 is over the *encrypted* bytes, so a mismatch means the
    # stored copy was altered/corrupted after being indexed (tamper + corruption beyond AES-GCM auth).
    if idx.get("vault_a_fp") != _fingerprint(_read_bytes(os.path.join(store, d, "vault-A.json.enc"))) \
       or idx.get("vault_b_fp") != _fingerprint(_read_bytes(os.path.join(store, d, "vault-B.json.enc"))):
        return False
    ts = float(idx.get("ts", 0))
    if last_sync is not None and ts < last_sync:
        return False                       # predates last applied sync -> stale
    if now - ts > staleness_hours * 3600:
        return False                       # absolute staleness cap
    return True


def _prune(store: str, retention: int, keep: str) -> None:
    """Remove oldest `backup-*` dirs beyond `retention`, but NEVER the dir `latest.json` points at."""
    if retention <= 0:
        return
    cands = [e for e in os.listdir(store)
             if e.startswith("backup-") and os.path.isdir(os.path.join(store, e))]
    cands.sort(reverse=True)                 # newest first (dir names sort chronologically)
    for old in cands[retention:]:
        if old == keep:
            continue                         # never delete the current backup
        _safe_rmdir(os.path.join(store, old))


def enforce_store_backup(exp_a: dict, exp_b: dict, kp, *,
                         backup_store: str | None = None,
                         last_sync_ts_arg: float | None = None,
                         staleness_hours: float = 72.0,
                         retention: int = 10) -> BackupInfo:
    """Ensure a validated, encrypted, up-to-date backup of BOTH vaults is on durable store, and
    return its `BackupInfo`. RAISES `BackupUnavailable` if it cannot be produced/validated — the
    caller must then refuse to mutate either vault.

    `exp_a`/`exp_b` are the *current* vault exports (`{"items": [...], "folders": [...]}`, the same
    shape `BwProfile.export` returns). `kp` encrypts/decrypts them.
    """
    store = store_dir(backup_store)
    os.makedirs(store, exist_ok=True)
    last_sync = last_sync_ts_arg if last_sync_ts_arg is not None else last_sync_ts()
    now = _now()

    # 1) reuse a current, valid backup if the latest.json index points at one.
    idx = _read_index(store)
    if idx and _validate_dir(store, idx, kp, last_sync, now, staleness_hours):
        return BackupInfo(dir=idx["dir"], ts=float(idx["ts"]),
                          vault_a_items=int(idx["vault_a_items"]),
                          vault_b_items=int(idx["vault_b_items"]))

    # 2) none valid -> make a fresh backup, validate it, THEN publish it to the index. Dir name is
    #    fixed-width and microsecond-precise so it sorts chronologically AND never collides with a
    #    backup made in the same wall-second as a prior call.
    ts_s = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(int(now))) + f"-{int((now - int(now)) * 1_000_000):06d}"
    bdir = os.path.join(store, f"backup-{ts_s}")
    try:
        os.makedirs(bdir, exist_ok=True)
        a_enc = kp.encrypt(json.dumps(exp_a).encode("utf-8"))
        b_enc = kp.encrypt(json.dumps(exp_b).encode("utf-8"))
        _write_bytes_atomic(os.path.join(bdir, "vault-A.json.enc"), a_enc)
        _write_bytes_atomic(os.path.join(bdir, "vault-B.json.enc"), b_enc)
        # RESTORABILITY proof: wrong kp or a corrupt write fails here.
        a_restored = json.loads(kp.decrypt(_read_bytes(os.path.join(bdir, "vault-A.json.enc"))))
        b_restored = json.loads(kp.decrypt(_read_bytes(os.path.join(bdir, "vault-B.json.enc"))))
    except Exception as e:
        _safe_rmdir(bdir)
        raise BackupUnavailable(f"could not validate a backup of both vaults ({e!r}); "
                               "refusing to sync")
    if a_restored != exp_a or b_restored != exp_b:
        _safe_rmdir(bdir)
        raise BackupUnavailable("backup round-trip mismatch; refusing to sync")

    index = {
        "dir": f"backup-{ts_s}",
        "ts": now,
        "vault_a_items": len(exp_a.get("items", [])),
        "vault_b_items": len(exp_b.get("items", [])),
        "vault_a_fp": _fingerprint(a_enc),     # sha256 of ciphertext — non-secret
        "vault_b_fp": _fingerprint(b_enc),
    }
    _write_text_atomic(_manifest_path(store), json.dumps(index, indent=2))
    _prune(store, retention, keep=index["dir"])
    return BackupInfo(dir=index["dir"], ts=now,
                      vault_a_items=len(exp_a.get("items", [])),
                      vault_b_items=len(exp_b.get("items", [])))
