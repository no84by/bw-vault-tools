from bw_vault_tools import cli_sync, keyprovider
from tests import fixtures as fx


class FakeProfile:
    def __init__(self, items):
        self._items = {i["id"]: i for i in items}
        self.created, self.edited, self.deleted = [], [], []

    def export(self):
        return {"items": list(self._items.values()), "folders": []}

    def create(self, item):
        new = dict(item, id="srv-" + item["id"])
        self._items[new["id"]] = new                 # reflect in subsequent exports
        self.created.append(new)
        return new

    def edit(self, item_id, item):
        self.edited.append(item_id)
        self._items[item_id] = item

    def delete(self, item_id, permanent=False):
        self.deleted.append(item_id)
        self._items.pop(item_id, None)

    def restore(self, item_id):
        pass


def kp():
    return keyprovider.PassphraseProvider("s")


def test_first_run_creates_missing_both_ways_then_idempotent(tmp_path):
    A = FakeProfile([fx.login("a1", uri="https://only-a.com", username="u", password="p")])
    B = FakeProfile([fx.login("b1", uri="https://only-b.com", username="v", password="q")])
    snp = str(tmp_path / "S.enc")
    cli_sync.run_sync(A, B, snapshot_path=snp, apply=True, key_provider=kp(),
                      approver=lambda op: True, run_dir=str(tmp_path / "run"))
    assert len(B.created) == 1 and len(A.created) == 1
    res2 = cli_sync.run_sync(A, B, snapshot_path=snp, apply=True, key_provider=kp(),
                             approver=lambda op: True, run_dir=str(tmp_path / "run2"))
    assert res2.applied == 0                          # second run is a no-op


def test_plan_mode_no_writes(tmp_path):
    A = FakeProfile([fx.login("a1", uri="https://only-a.com")])
    B = FakeProfile([])
    cli_sync.run_sync(A, B, snapshot_path=str(tmp_path / "S.enc"), apply=False, key_provider=kp(),
                      run_dir=str(tmp_path / "run"))
    assert A.created == [] and B.created == []
