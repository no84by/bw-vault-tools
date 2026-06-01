from bw_vault_tools import content, identity, merge, snapshot
from tests import fixtures as fx


def test_create_on_b_suppressed_when_already_in_b_org():
    # A-only login that already exists in B's org must NOT be re-created on B's personal vault
    # (that would undo bw-dedup's cross-boundary clear). It's reported as suppressed instead.
    a = [fx.login("a1", uri="https://x.com", username="u", password="p")]
    org_b = {identity.fingerprint(a[0])}
    r = merge.three_way(a, [], snapshot.Snapshot(), org_b_fps=org_b)
    assert [o for o in r.ops if o.kind == "create"] == []
    assert [(o.kind, o.target) for o in r.suppressed] == [("create", "B")]


def test_create_on_a_suppressed_when_already_in_a_org():
    b = [fx.login("b1", uri="https://y.com", username="v", password="q")]
    org_a = {identity.fingerprint(b[0])}
    r = merge.three_way([], b, snapshot.Snapshot(), org_a_fps=org_a)
    assert [o for o in r.ops if o.kind == "create"] == []
    assert [(o.kind, o.target) for o in r.suppressed] == [("create", "A")]


def test_create_proceeds_when_not_in_target_org():
    a = [fx.login("a1", uri="https://x.com", username="u", password="p")]
    r = merge.three_way(a, [], snapshot.Snapshot(), org_b_fps={("other.com", "z", "w")})
    assert [(o.kind, o.target) for o in r.ops] == [("create", "B")]
    assert r.suppressed == []


def _snap(pairs):
    s = snapshot.Snapshot()
    for lid, a, b in pairs:
        s.record(lid, a["id"], b["id"], content.content_key(a), a)
    return s


def test_new_on_a_creates_on_b():
    a = [fx.login("a1", uri="https://new.com", username="u", password="p")]
    r = merge.three_way(a, [], snapshot.Snapshot())
    assert [(o.kind, o.target) for o in r.ops] == [("create", "B")]


def test_new_on_b_creates_on_a():
    b = [fx.login("b1", uri="https://new.com", username="u", password="p")]
    r = merge.three_way([], b, snapshot.Snapshot())
    assert [(o.kind, o.target) for o in r.ops] == [("create", "A")]


def test_identical_pair_no_op():
    a = fx.login("a1", uri="https://x.com", username="u", password="p")
    b = fx.login("b1", uri="https://x.com", username="u", password="p")
    r = merge.three_way([a], [b], _snap([("L1", a, b)]))
    assert r.ops == []


def test_one_sided_edit_pushes_safe():
    a0 = fx.login("a1", uri="https://x.com", username="u", password="OLD")
    b = fx.login("b1", uri="https://x.com", username="u", password="OLD")
    s = _snap([("L1", a0, b)])
    a1 = fx.login("a1", uri="https://x.com", username="u", password="NEW")
    r = merge.three_way([a1], [b], s)
    op = r.ops[0]
    assert op.kind == "edit" and op.target == "B" and op.destructive is False


def test_both_edited_is_conflict_gated():
    base = fx.login("a1", uri="https://x.com", username="u", password="BASE")
    b0 = fx.login("b1", uri="https://x.com", username="u", password="BASE")
    s = _snap([("L1", base, b0)])
    a1 = fx.login("a1", uri="https://x.com", username="u", password="A-NEW")
    b1 = fx.login("b1", uri="https://x.com", username="u", password="B-NEW")
    r = merge.three_way([a1], [b1], s)
    assert r.ops[0].kind == "conflict" and r.ops[0].destructive is True


def test_deleted_on_b_deletes_on_a_gated():
    a = fx.login("a1", uri="https://x.com", username="u", password="p")
    b = fx.login("b1", uri="https://x.com", username="u", password="p")
    s = _snap([("L1", a, b)])
    r = merge.three_way([a], [], s)
    assert r.ops[0].kind == "delete" and r.ops[0].target == "A" and r.ops[0].destructive is True


def test_deleted_one_side_edited_other_is_ambiguous():
    base = fx.login("a1", uri="https://x.com", username="u", password="BASE")
    b = fx.login("b1", uri="https://x.com", username="u", password="BASE")
    s = _snap([("L1", base, b)])
    a1 = fx.login("a1", uri="https://x.com", username="u", password="EDITED")
    r = merge.three_way([a1], [], s)
    assert r.ops[0].kind == "ambiguous" and r.ops[0].destructive is True


def test_passkey_guarded_never_destructive():
    a = fx.passkey_login("a1", uri="https://x.com", username="u", password="p")
    r = merge.three_way([a], [], snapshot.Snapshot())
    assert r.ops[0].guarded is True and r.ops[0].destructive is False
