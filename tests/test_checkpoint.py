import os

from bw_vault_tools import checkpoint, keyprovider


def kp():
    return keyprovider.PassphraseProvider("pw")


def test_baseline_encrypted_and_roundtrips(tmp_path):
    run = checkpoint.RunDir(str(tmp_path), kp())
    run.write_baseline("vault", {"items": [{"id": "a"}]})
    raw = open(os.path.join(run.dir, "00-pre", "vault.json.enc"), "rb").read()
    assert b'"items"' not in raw
    assert run.read_baseline("vault")["items"][0]["id"] == "a"


def test_journal_records_completed_and_undo_is_reverse(tmp_path):
    run = checkpoint.RunDir(str(tmp_path), kp())
    run.record(item_id="b", inverse={"action": "restore", "item_id": "b"})
    run.record(item_id="a", inverse={"action": "edit", "item_id": "a", "item": {"id": "a"}})
    assert run.completed_ids == {"a", "b"}
    assert [e["action"] for e in run.undo_plan()] == ["edit", "restore"]


def test_completed_ids_survive_reopen(tmp_path):
    r1 = checkpoint.RunDir(str(tmp_path), kp())
    r1.record("x", {"action": "noop"})
    r2 = checkpoint.RunDir(str(tmp_path), kp())
    assert "x" in r2.completed_ids


def test_new_run_dir_is_persistent_not_tmpfs(monkeypatch, tmp_path):
    # the run dir (journal + baseline) must survive process exit so --undo works; not /dev/shm
    monkeypatch.setattr(checkpoint, "data_home", lambda: str(tmp_path))
    d = checkpoint.new_run_dir("dedup")
    assert d.startswith(str(tmp_path)) and "bw-vault-tools" in d and "/dev/shm" not in d
    assert checkpoint.os.path.isdir(d)
