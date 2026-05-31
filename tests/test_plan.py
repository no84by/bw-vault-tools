from bw_vault_tools import plan
from tests import fixtures as fx


def test_op_destructive_flags():
    assert plan.DeleteOp("x", {"id": "x"}).destructive is True
    assert plan.MergeOp("a", ["b"], [], {}).destructive is True
    assert plan.AssignFolderOp("a", "f", "F").destructive is False
    assert plan.FlagReusedOp("a", "!").destructive is False


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


def test_reused_password_flagged_even_when_one_side_is_preserved():
    items = [fx.passkey_login("k", uri="https://x.com", username="u", password="shared"),
             fx.login("a", uri="https://y.com", username="v", password="shared")]
    p = _plan(items)
    flags = [o for o in p.ops if isinstance(o, plan.FlagReusedOp)]
    assert any(o.item_id == "a" for o in flags)


def test_preservation_invariant_holds():
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p"),
             fx.no_uri_login("n"), fx.ssh_key("s"), fx.passkey_login("k", password="z")]
    p = _plan(items)
    assert p.check_invariant({"a", "b", "n", "s", "k"}) is True


def test_invariant_catches_a_silent_drop():
    p = plan.Plan()
    assert p.check_invariant({"ghost"}) is False
