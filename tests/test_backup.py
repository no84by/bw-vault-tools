"""Tests for the pre-mutation backup gate: a validated, encrypted, up-to-date backup of both
vaults must be on durable store before the cascade mutates anything; if it cannot be, the run
refuses (BackupUnavailable)."""
import json
import os

from bw_vault_tools import backup, keyprovider

A = {"items": [{"id": "a1", "login": {"password": "s3cret-PW", "totp": "HPBMGVW"}},
               {"id": "a2", "login": {"password": "two", "totp": None}}], "folders": []}
B = {"items": [{"id": "b1", "login": {"password": "bbb", "totp": None}}], "folders": []}


def _kp(pw="s"):
    return keyprovider.PassphraseProvider(pw)


def _store(tmp_path):
    return str(tmp_path / "store")


def _read_backup(store: str, d: str, which: str, kp):
    blob = open(os.path.join(store, d, f"vault-{which}.json.enc"), "rb").read()
    return json.loads(kp.decrypt(blob))


# ---- happy path -----------------------------------------------------------------------------

def test_first_run_creates_a_validated_backup_on_store(tmp_path):
    store = _store(tmp_path)
    info = backup.enforce_store_backup(A, B, _kp(), backup_store=store)
    idx = json.loads(open(os.path.join(store, "latest.json")).read())
    assert idx["dir"] == info.dir
    assert idx["vault_a_items"] == 2 and idx["vault_b_items"] == 1
    # the two encrypted blobs exist and decrypt back to the originals (restored, not corrupt)
    kp = _kp()
    assert _read_backup(store, info.dir, "A", kp) == A
    assert _read_backup(store, info.dir, "B", kp) == B


def test_second_run_reuses_a_valid_backup_without_recreating(tmp_path):
    store = _store(tmp_path)
    first = backup.enforce_store_backup(A, B, _kp(), backup_store=store, last_sync_ts_arg=None)
    before = frozenset(os.listdir(store))
    second = backup.enforce_store_backup(A, B, _kp(), backup_store=store, last_sync_ts_arg=None)
    assert second.dir == first.dir                # same copy reused
    assert frozenset(os.listdir(store)) == before


# ---- "up to date" semantics -----------------------------------------------------------------

def test_backup_older_than_last_sync_is_stale_and_remade(tmp_path):
    store = _store(tmp_path)
    first = backup.enforce_store_backup(A, B, _kp(), backup_store=store, last_sync_ts_arg=None)
    # pretend a sync ran AFTER `first` -> `first` no longer reflects current state -> remade.
    stale = backup.enforce_store_backup(A, B, _kp(), backup_store=store, last_sync_ts_arg=first.ts + 10)
    assert stale.dir != first.dir


def test_first_run_last_sync_none_does_not_raise(tmp_path):
    # No prior sync: last_sync is None, so the `ts >= last_sync` comparison must be skipped cleanly.
    info = backup.enforce_store_backup(A, B, _kp(), backup_store=_store(tmp_path), last_sync_ts_arg=None)
    assert info is not None


def test_absolute_staleness_cap_forces_refresh(tmp_path):
    store = _store(tmp_path)
    backup.enforce_store_backup(A, B, _kp(), backup_store=store, last_sync_ts_arg=None)
    # staleness_hours ~ 0 => any real backup is older than the cap -> a fresh one is made.
    fresh = backup.enforce_store_backup(A, B, _kp(), backup_store=store, last_sync_ts_arg=None,
                                        staleness_hours=1e-9)
    assert fresh is not None


# ---- restorability / refusal ----------------------------------------------------------------

def test_wrong_key_cannot_validate_existing_backup(tmp_path):
    store = _store(tmp_path)
    kp = _kp("right")
    first = backup.enforce_store_backup(A, B, kp, backup_store=store, last_sync_ts_arg=None)
    # Corrupt the indexed backup's blobs in place (as if the key rotated or a file went corrupt):
    evil = _kp("other")
    with open(os.path.join(store, first.dir, "vault-A.json.enc"), "wb") as f:
        f.write(evil.encrypt(json.dumps(A).encode("utf-8")))
    with open(os.path.join(store, first.dir, "vault-B.json.enc"), "wb") as f:
        f.write(evil.encrypt(json.dumps(B).encode("utf-8")))
    # enforce must NOT trust the undecryptable copy: it makes a fresh one, validated with `kp`.
    fresh = backup.enforce_store_backup(A, B, kp, backup_store=store, last_sync_ts_arg=None)
    assert fresh.dir != first.dir                          # the corrupted copy was not reused
    assert _read_backup(store, fresh.dir, "A", kp) == A
    assert _read_backup(store, fresh.dir, "B", kp) == B


def test_tampered_backup_blob_is_not_trusted(tmp_path):
    store = _store(tmp_path)
    kp = _kp()
    first = backup.enforce_store_backup(A, B, kp, backup_store=store, last_sync_ts_arg=None)
    # Flip a byte in the stored ciphertext: the on-disk sha256 no longer matches the stored index
    # fingerprint -> the copy is not "valid" and a fresh, validated one is made.
    p = os.path.join(store, first.dir, "vault-A.json.enc")
    b = bytearray(open(p, "rb").read())
    b[0] ^= 0xFF
    open(p, "wb").write(bytes(b))
    fresh = backup.enforce_store_backup(A, B, kp, backup_store=store, last_sync_ts_arg=None)
    assert fresh.dir != first.dir
    assert _read_backup(store, fresh.dir, "A", kp) == A
    assert _read_backup(store, fresh.dir, "B", kp) == B


def test_cannot_write_a_backup_raises_backup_unavailable(tmp_path):
    store = _store(tmp_path)
    os.mkdir(store, 0o755)
    os.chmod(store, 0o500)                  # read-only store: writes fail -> cannot secure a backup
    try:
        raised = False
        try:
            backup.enforce_store_backup(A, B, _kp(), backup_store=store, last_sync_ts_arg=None)
        except backup.BackupUnavailable:
            raised = True
        assert raised, "expected BackupUnavailable when no backup can be secured"
    finally:
        os.chmod(store, 0o755)


# ---- safety properties ----------------------------------------------------------------------

def test_index_is_secret_free(tmp_path):
    # The plaintext index must NOT contain a vault's password/totp (else sha256(fingerprint) would be
    # an offline password oracle). Fingerprints are over the *encrypted* blob bytes only.
    store = _store(tmp_path)
    backup.enforce_store_backup(A, B, _kp(), backup_store=store, last_sync_ts_arg=None)
    idx = open(os.path.join(store, "latest.json")).read()
    assert "s3cret-PW" not in idx
    assert "HPBMGVW" not in idx


def test_prune_keeps_latest_and_drops_oldest(tmp_path):
    store = _store(tmp_path)
    # Fabricate 5 backups with fixed-width dates all earlier than "today"; retention=2 -> only the
    # 2 newest survive, and the current backup (info.dir) is never pruned.
    fabricated = []
    for day in ("20260101", "20260102", "20260103", "20260104", "20260105"):
        name = f"backup-{day}T000000Z"
        os.makedirs(os.path.join(store, name), exist_ok=True)
        fabricated.append(name)
    info = backup.enforce_store_backup(A, B, _kp(), backup_store=store, last_sync_ts_arg=None,
                                       retention=2)
    remaining = sorted(d for d in os.listdir(store) if d.startswith("backup-"))
    assert info.dir in remaining                                  # latest never pruned
    assert sorted(remaining) == sorted([info.dir, fabricated[-1]])  # fresh + newest fabricated survive
    assert fabricated[0] not in remaining                          # oldest dropped
