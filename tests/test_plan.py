from bw_vault_tools import plan
from tests import fixtures as fx


def test_op_destructive_flags():
    assert plan.DeleteOp("x", {"id": "x"}).destructive is True
    assert plan.MergeOp("a", ["b"], [], {}).destructive is True
    assert plan.ClearPersonalDupOp("x", {"id": "x"}).destructive is True


def test_delete_inverse_is_restore():
    assert plan.DeleteOp("x", {"id": "x"}).inverse() == {"action": "restore", "item_id": "x"}


def test_merge_inverse_restores_keep_and_drops():
    op = plan.MergeOp(keep_id="a", drop_ids=["b"], uris=["u"], keep_before={"id": "a", "name": "old"})
    inv = op.inverse()
    assert inv["action"] == "edit" and inv["item"] == {"id": "a", "name": "old"}
    assert inv["restore_ids"] == ["b"]


def _plan(items, folders=None):
    return plan.build_dedup_plan(items, folders or [])


def test_exact_duplicates_produce_delete_op():
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p")]
    p = _plan(items)
    dels = [o for o in p.ops if isinstance(o, plan.DeleteOp)]
    assert len(dels) == 1 and dels[0].item_id == "b"


def test_date_or_uri_variants_produce_merge_op():
    # same fingerprint (site.com/u/p) but different dates + an extra URI on b -> merge
    a = fx.login("a", uri="https://site.com", username="u", password="p", revision="2026-01-01T00:00:00.000Z")
    b = fx.login("b", uri="https://site.com", username="u", password="p", revision="2026-02-01T00:00:00.000Z")
    b["login"]["uris"].append({"uri": "https://m.site.com", "match": None})
    p = _plan([a, b])
    merges = [o for o in p.ops if isinstance(o, plan.MergeOp)]
    assert len(merges) == 1
    assert set(merges[0].uris) == {"https://site.com", "https://m.site.com"}


def test_same_timestamps_but_differing_uris_merge_not_delete():
    # identical password AND identical creation/revision dates (fools a timestamp-only exact-dup
    # test) but b carries a URI a lacks -> must MERGE (union), never pure-delete b and lose it.
    a = fx.login("a", uri="https://site.com", username="u", password="p")
    b = fx.login("b", uri="https://site.com", username="u", password="p")
    b["login"]["uris"].append({"uri": "https://extra.site.com", "match": None})
    p = _plan([a, b])
    assert [o for o in p.ops if isinstance(o, plan.DeleteOp)] == []
    merges = [o for o in p.ops if isinstance(o, plan.MergeOp)]
    assert len(merges) == 1
    assert "https://extra.site.com" in set(merges[0].uris)


def test_same_timestamps_but_differing_notes_merge_not_delete():
    a = fx.login("a", username="u", password="p", notes="recovery code 123")
    b = fx.login("b", username="u", password="p", notes=None)
    p = _plan([a, b])               # a has a note b lacks -> merge preserves it, never pure-delete
    assert [o for o in p.ops if isinstance(o, plan.DeleteOp)] == []
    assert len([o for o in p.ops if isinstance(o, plan.MergeOp)]) == 1


def test_passkey_login_preserved_and_never_destructive():
    items = [fx.passkey_login("a", username="u", password="p"),
             fx.login("b", username="u", password="p")]
    p = _plan(items)
    targets = set()
    for o in p.gated_ops:
        targets |= {getattr(o, "item_id", None), getattr(o, "keep_id", None)}
        targets |= set(getattr(o, "drop_ids", []))
    assert "a" not in targets
    assert "a" in p.preserved_ids and "a" in p.flagged_guard_ids
    assert len(p.gated_ops) == 0


def test_ssh_key_and_no_uri_and_unknown_preserved():
    items = [fx.ssh_key("s"), fx.no_uri_login("n"), fx.unknown_type("w"), fx.login("a")]
    p = _plan(items)
    assert {"s", "n", "w"} <= p.preserved_ids
    assert "s" in p.flagged_guard_ids


def test_plan_is_pure_dedup_no_folder_or_reuse_annotations():
    # A folder named like the username + a reused password would, in the old tool, trigger a
    # folder-assign and a reuse-note. bw-dedup is now pure dedup: only merge/remove ops, ever.
    items = [fx.login("a", uri="https://x.com", username="silviu", password="shared"),
             fx.login("b", uri="https://y.com", username="silviu", password="shared")]
    folders = [{"id": "f1", "name": "silviu"}]
    p = plan.build_dedup_plan(items, folders)
    kinds = {type(o).__name__ for o in p.ops}
    assert kinds <= {"DeleteOp", "MergeOp", "ClearPersonalDupOp"}
    assert not hasattr(plan, "FlagReusedOp") and not hasattr(plan, "AssignFolderOp")


def test_preservation_invariant_holds():
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p"),
             fx.no_uri_login("n"), fx.ssh_key("s"), fx.passkey_login("k", password="z")]
    p = _plan(items)
    assert p.check_invariant({"a", "b", "n", "s", "k"}) is True


def test_invariant_catches_a_silent_drop():
    p = plan.Plan()
    assert p.check_invariant({"ghost"}) is False


def test_clear_personal_dup_when_present_in_org():
    items = [fx.login("p", uri="https://x.com", username="u", password="pw")]
    org = [fx.login("o", uri="https://x.com", username="u", password="pw")]
    p = plan.build_dedup_plan(items, [], org_reference=org)
    clears = [o for o in p.ops if isinstance(o, plan.ClearPersonalDupOp)]
    assert [c.item_id for c in clears] == ["p"]
    assert "p" in p.removed_ids()
    assert "o" not in {getattr(o, "item_id", None) for o in p.ops}


def test_personal_not_in_org_is_untouched():
    items = [fx.login("p", uri="https://x.com", username="u", password="pw")]
    org = [fx.login("o", uri="https://other.com", username="v", password="zz")]
    p = plan.build_dedup_plan(items, [], org_reference=org)
    assert [o for o in p.ops if isinstance(o, plan.ClearPersonalDupOp)] == []


def test_org_reference_none_is_unchanged():
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p")]
    p = plan.build_dedup_plan(items, [])
    assert [o for o in p.ops if isinstance(o, plan.ClearPersonalDupOp)] == []


def test_clear_op_inverse_is_restore():
    op = plan.ClearPersonalDupOp(item_id="p", payload={"id": "p"})
    assert op.destructive is True
    assert op.inverse() == {"action": "restore", "item_id": "p"}
