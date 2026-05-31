from bw_vault_tools import cli_dedup, keyprovider
from tests import fixtures as fx


class FakeProfile:
    def __init__(self, items):
        self._items = items
        self.deleted = []
        self.edited = []

    def export(self):
        return fx.vault(list(self._items))

    def edit(self, item_id, item):
        self.edited.append((item_id, item))

    def delete(self, item_id, permanent=False):
        self.deleted.append(item_id)

    def restore(self, item_id):
        pass


def approve_all(op):
    return True


def kp():
    return keyprovider.PassphraseProvider("t")


def test_apply_dedups_and_preserves(tmp_path):
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p"),
             fx.no_uri_login("n"), fx.ssh_key("s"), fx.passkey_login("k", password="z")]
    prof = FakeProfile(items)
    res = cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), key_provider=kp())
    assert prof.deleted == ["b"] and res.applied_destructive == 1
    assert {"n", "s", "k"}.isdisjoint(prof.deleted)


def test_plan_mode_makes_no_changes(tmp_path):
    prof = FakeProfile([fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p")])
    cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), apply=False)
    assert prof.deleted == [] and prof.edited == []


def test_apply_requires_key_provider(tmp_path):
    prof = FakeProfile([fx.login("a")])
    try:
        cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), apply=True, key_provider=None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_dedup_is_idempotent(tmp_path):
    items = [fx.login("a", username="u", password="p"),
             fx.login("b", username="v", password="q", uri="https://other.com")]
    prof = FakeProfile(items)
    res = cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), key_provider=kp())
    assert res.applied_destructive == 0 and prof.deleted == []


def test_non_tty_approver_refuses(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli_dedup.tty_approver(object()) is False


def test_interrupted_run_skips_completed(tmp_path):
    run = cli_dedup.checkpoint.RunDir(str(tmp_path), kp())
    run.record("b", {"action": "restore", "item_id": "b"})
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p")]
    prof = FakeProfile(items)
    cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), key_provider=kp())
    assert "b" not in prof.deleted
