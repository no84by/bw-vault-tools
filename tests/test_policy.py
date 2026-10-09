from bw_vault_tools import policy


class MergeOp: pass
class DeleteOp:
    payload = {"name": "exact-dup.com"}
class ClearPersonalDupOp:
    payload = {"name": "org-dup.com"}


class SyncOp:
    def __init__(self, kind, target, item):
        self.kind, self.target, self.item = kind, target, item


def test_loss_free_dedup_ops_auto_approve():
    held = []
    approve = policy.make_approver(held)
    assert approve(MergeOp()) is True
    assert approve(DeleteOp()) is True
    assert approve(ClearPersonalDupOp()) is True
    assert held == []


def test_destructive_sync_ops_are_held_never_guessed():
    held = []
    approve = policy.make_approver(held)
    assert approve(SyncOp("conflict", "A", {"name": "bank.example"})) is False
    assert approve(SyncOp("delete", "B", {"name": "old.example"})) is False
    assert len(held) == 2
    assert "bank.example" in held[0][0] and "conflict" in held[0][0]
