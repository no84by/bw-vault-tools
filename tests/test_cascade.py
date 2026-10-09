from bw_vault_tools import cascade, gateway


class _R:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_gateway_summarize_falls_back_when_gateway_unreachable():
    summary = {"applied": 3, "stages": [("dedup A", "1 merged/removed")]}
    held = [("conflict->A: acme.example", "held for review")]
    out = gateway.summarize(summary, held, reasoner=lambda **k: None)   # gateway "down"
    assert "acme.example" in out and "held" in out.lower()


def test_gateway_summarize_no_held_skips_the_model():
    boom = lambda **k: (_ for _ in ()).throw(AssertionError("must not call gateway when nothing held"))
    out = gateway.summarize({"applied": 2, "stages": []}, [], reasoner=boom)
    assert "2 applied" in out


def test_cascade_runs_all_stages_applies_loss_free_and_holds_destructive(monkeypatch, tmp_path):
    monkeypatch.setattr(cascade.checkpoint, "new_run_dir", lambda tool: str(tmp_path))
    monkeypatch.setattr(cascade.gateway, "summarize", lambda summary, held: "DIGEST")
    seq = []

    class MergeOp: pass
    class SyncConflict:
        kind, target, item = "conflict", "A", {"name": "bank.example"}

    def fake_dedup(prof, approver, run_dir, apply, key_provider):
        seq.append("dedup")
        approver(MergeOp())                       # loss-free -> applied
        return _R(applied_destructive=1, preserved=0)

    def fake_sync(prof_a, prof_b, snapshot_path, apply, key_provider, approver, run_dir, conflict_policy):
        seq.append(f"sync:{conflict_policy}")
        approver(SyncConflict())                  # destructive -> held
        return _R(applied=5, gated=1, guarded=0, suppressed=0)

    out = cascade.run(object(), object(), "snap.enc", object(),
                      scan=lambda dirs: [], dedup=fake_dedup, sync=fake_sync,
                      imp=lambda *a, **k: _R(created=0), require_backup=False)

    assert seq == ["dedup", "dedup", "sync:a-wins"]          # default conflict policy = a-wins
    assert len(out["held"]) == 1 and "bank.example" in out["held"][0][0]
    assert out["summary"]["applied"] == 1 + 1 + 5            # two dedups + sync's safe applies


# ---- session preflight (the fix for the 6-week silent failure) ----------------------
# A locked `bw` exits 0 from BOTH `export` and `sync` and writes a bare newline to stdout, so the
# only thing that catches a stale session is parsing `bw status`. These pin that.

import subprocess

import pytest


class _P:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _fake_bw(monkeypatch, status_out, sync=_P(0, "Syncing complete.")):
    """Stub subprocess.run for the two calls _prepare makes: `bw status`, then `bw sync`."""
    calls = []

    def fake_run(cmd, env=None, capture_output=None, text=None, **kw):
        calls.append(cmd)
        return status_out if cmd[1] == "status" else sync

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_prepare_rejects_a_locked_session_with_an_actionable_message(monkeypatch):
    monkeypatch.setenv("BW_SESSION_A", "stale-token")
    _fake_bw(monkeypatch, _P(0, '{"status":"locked","serverUrl":"https://vault.example"}'))
    with pytest.raises(cascade.VaultUnavailable) as e:
        cascade._prepare("/dev/shm/A", "BW_SESSION_A", "A")
    msg = str(e.value)
    assert "vault A locked" in msg                     # says WHICH vault and WHAT is wrong
    assert "bw-cascade-session-a.cred" in msg          # says exactly what to re-seal
    assert "stale-token" not in msg                    # never echoes the session


def test_prepare_never_reaches_sync_when_locked(monkeypatch):
    monkeypatch.setenv("BW_SESSION_A", "stale-token")
    calls = _fake_bw(monkeypatch, _P(0, '{"status":"locked"}'))
    with pytest.raises(cascade.VaultUnavailable):
        cascade._prepare("/dev/shm/A", "BW_SESSION_A", "A")
    assert calls == [["bw", "status"]]                 # fail-fast: no sync, no vault traffic


def test_prepare_accepts_an_unlocked_session_and_syncs(monkeypatch):
    monkeypatch.setenv("BW_SESSION_B", "good-token")
    calls = _fake_bw(monkeypatch, _P(0, '{"status":"unlocked"}'))
    assert cascade._prepare("/dev/shm/B", "BW_SESSION_B", "B") == "good-token"
    assert calls == [["bw", "status"], ["bw", "sync"]]


def test_prepare_propagates_a_failing_sync(monkeypatch):
    monkeypatch.setenv("BW_SESSION_A", "good-token")
    _fake_bw(monkeypatch, _P(0, '{"status":"unlocked"}'),
             sync=_P(1, "", "Failed to sync: connect ECONNREFUSED"))
    with pytest.raises(cascade.VaultUnavailable, match="ECONNREFUSED"):
        cascade._prepare("/dev/shm/A", "BW_SESSION_A", "A")


def test_prepare_rejects_non_json_status(monkeypatch):
    """The old failure shape: bw prints prose, not JSON. Must not become a JSONDecodeError."""
    monkeypatch.setenv("BW_SESSION_A", "good-token")
    _fake_bw(monkeypatch, _P(0, "You are not logged in.\n"))
    with pytest.raises(cascade.VaultUnavailable, match="non-JSON"):
        cascade._prepare("/dev/shm/A", "BW_SESSION_A", "A")


def test_prepare_rejects_a_missing_session_env(monkeypatch):
    monkeypatch.delenv("BW_SESSION_A", raising=False)
    with pytest.raises(cascade.VaultUnavailable, match=r"\$BW_SESSION_A"):
        cascade._prepare("/dev/shm/A", "BW_SESSION_A", "A")


# ---- pre-mutation backup gate (enforced in run(), before any mutation) ---------------------

def test_backup_gate_runs_before_any_stage_and_refuses_on_failure(monkeypatch):
    """The gate must run BEFORE import/dedup/sync, and if it refuses, NOTHING mutates."""
    ran = []

    class _I:
        def export(self):
            ran.append("export")
            return {"items": [], "folders": []}

    def fake_enforce(*_a, **_k):
        raise cascade.backup.BackupUnavailable("store unwritable")

    def boom(*_a, **_k):
        raise AssertionError("no mutating stage may run once the guard refuses")

    monkeypatch.setattr(cascade.backup, "enforce_store_backup", fake_enforce)
    with pytest.raises(cascade.backup.BackupUnavailable):
        cascade.run(_I(), _I(), "snap.enc", object(), require_backup=True,
                    scan=lambda dirs: [], dedup=boom, sync=boom, imp=boom)
    assert ran == ["export", "export"]            # only the gate's reads happened


def test_backup_gate_is_the_first_stage(monkeypatch, tmp_path):
    """With the guard passing, the cascade records `backup` as stage 0 and then runs import→dedup×2→sync."""
    order = []

    class _I:
        def export(self):
            order.append("export")
            return {"items": [], "folders": []}

    def fake_enforce(*_a, **_k):
        order.append("backup")
        return _R(dir="backup-20260101T000000Z-000000", ts=1.0, vault_a_items=0, vault_b_items=0)

    seq = []

    def rec(name):
        def f(*_a, **_k):
            seq.append(name)
            return _R(created=0, applied_destructive=0, applied=5, gated=0, guarded=0, suppressed=0)
        return f

    monkeypatch.setattr(cascade.backup, "enforce_store_backup", fake_enforce)
    monkeypatch.setattr(cascade.checkpoint, "new_run_dir", lambda tool: str(tmp_path))
    monkeypatch.setattr(cascade.gateway, "summarize", lambda summary, held: "DIGEST")
    out = cascade.run(_I(), _I(), "snap.enc", object(), require_backup=True,
                      scan=lambda dirs: [], dedup=rec("dedup"), sync=rec("sync"), imp=rec("import"))
    assert order == ["export", "export", "backup"]   # gate reads then backs up
    assert seq == ["dedup", "dedup", "sync"]          # import is skipped (scan yields no exports)
    assert out["summary"]["stages"][0] == ("backup", "backup-20260101T000000Z-000000 validated on store")

