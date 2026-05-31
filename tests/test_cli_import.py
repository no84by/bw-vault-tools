from bw_vault_tools import cli_import, keyprovider
from tests.test_import_plan import _login


class FakeProfile:
    def __init__(self, live):
        self._live = live
        self.created = []
        self.deleted = []

    def export(self):
        return {"items": list(self._live)}

    def create(self, item):
        new = dict(item, id="srv-" + item["id"])
        self.created.append(new)
        return new

    def delete(self, item_id, permanent=False):
        self.deleted.append(item_id)


def kp():
    return keyprovider.PassphraseProvider("t")


def test_plan_mode_creates_nothing(tmp_path):
    prof = FakeProfile([])
    res = cli_import.run_import(prof, candidates=[_login("C1", uri="https://new.com")],
                               run_dir=str(tmp_path), apply=False)
    assert prof.created == [] and res.to_create == 1


def test_apply_creates_only_new_and_journals(tmp_path):
    live = [_login("L1", uri="https://x.com", user="alice", pw="p1")]
    cands = [_login("C1", uri="https://x.com", user="alice", pw="p1"),     # already present -> skip
             _login("C2", uri="https://new.com", user="bob", pw="p2")]     # new -> create
    prof = FakeProfile(live)
    res = cli_import.run_import(prof, candidates=cands, run_dir=str(tmp_path),
                               apply=True, key_provider=kp(), approver=lambda p: True)
    assert [c["id"] for c in prof.created] == ["srv-C2"]
    assert res.created == 1 and res.skipped == 1
    import bw_vault_tools.checkpoint as cp
    run = cp.RunDir(str(tmp_path), kp())
    assert {"srv-C2"} == {e["item_id"] for e in run.read_journal()}


def test_apply_requires_key_provider(tmp_path):
    try:
        cli_import.run_import(FakeProfile([]), candidates=[_login("C1")],
                             run_dir=str(tmp_path), apply=True, key_provider=None)
        assert False
    except ValueError:
        pass


def test_undo_deletes_created(tmp_path):
    prof = FakeProfile([])
    cli_import.run_import(prof, candidates=[_login("C1", uri="https://new.com")],
                         run_dir=str(tmp_path), apply=True, key_provider=kp(), approver=lambda p: True)
    cli_import.undo(prof, str(tmp_path), kp())
    assert prof.deleted == ["srv-C1"]
