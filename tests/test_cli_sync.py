from bw_vault_tools import checkpoint, cli_sync, content, keyprovider, snapshot
from tests import fixtures as fx


def _kp():
    return keyprovider.PassphraseProvider("s")


def test_one_sided_edit_targets_the_other_vaults_own_id(tmp_path):
    # A edited since last sync; pushing to B must edit B's item by B's id (not A's id).
    a = fx.login("a1", uri="https://x.com", username="u", password="NEW")
    b = fx.login("b1", uri="https://x.com", username="u", password="OLD")
    snp = str(tmp_path / "S.enc")
    s = snapshot.Snapshot(); s.record("L1", "a1", "b1", content.content_key(b), a)
    snapshot.save(s, snp, _kp())
    A = FakeProfile([a]); B = FakeProfile([b])
    cli_sync.run_sync(A, B, snapshot_path=snp, apply=True, key_provider=_kp(),
                      approver=lambda op: True, run_dir=str(tmp_path / "run"))
    assert B.edited == ["b1"] and A.edited == []
    assert B._items["b1"]["login"]["password"] == "NEW"      # B now carries A's content


def test_divergent_conflict_resolves_then_converges(tmp_path):
    a = fx.login("a1", uri="https://x.com", username="u", password="A", revision="2026-02-01T00:00:00.000Z")
    b = fx.login("b1", uri="https://x.com", username="u", password="B", revision="2026-01-01T00:00:00.000Z")
    snp = str(tmp_path / "S.enc")
    A = FakeProfile([a]); B = FakeProfile([b])
    cli_sync.run_sync(A, B, snapshot_path=snp, apply=True, key_provider=_kp(),
                      approver=lambda op: True, run_dir=str(tmp_path / "r1"))
    assert B._items["b1"]["login"]["password"] == "A"        # newer (A) won, applied to B
    r2 = cli_sync.run_sync(A, B, snapshot_path=snp, apply=False, key_provider=_kp(),
                           run_dir=str(tmp_path / "r2"))
    assert r2.planned == 0                                    # resolved -> converges


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


def test_apply_writes_baseline_export_of_both_vaults_before_mutating(tmp_path):
    A = FakeProfile([fx.login("a1", uri="https://only-a.com", username="u", password="p")])
    B = FakeProfile([fx.login("b1", uri="https://only-b.com", username="v", password="q")])
    run = str(tmp_path / "run")
    cli_sync.run_sync(A, B, snapshot_path=str(tmp_path / "S.enc"), apply=True, key_provider=kp(),
                      approver=lambda op: True, run_dir=run)
    rd = checkpoint.RunDir(run, kp())
    assert rd.read_baseline("A")["items"][0]["id"] == "a1"   # pre-mutation snapshot of A
    assert rd.read_baseline("B")["items"][0]["id"] == "b1"   # pre-mutation snapshot of B


def test_plan_mode_no_writes(tmp_path):
    A = FakeProfile([fx.login("a1", uri="https://only-a.com")])
    B = FakeProfile([])
    cli_sync.run_sync(A, B, snapshot_path=str(tmp_path / "S.enc"), apply=False, key_provider=kp(),
                      run_dir=str(tmp_path / "run"))
    assert A.created == [] and B.created == []
