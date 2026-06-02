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


def test_duplicate_pairkey_pairs_by_content_and_converges():
    # two logins share (uri,username) but differ by password; both vaults hold both. First-run
    # pairing must match by CONTENT (not list order) or it cross-pairs and emits phantom edits forever.
    a1 = fx.login("a1", uri="https://x.com", username="u", password="P1")
    a2 = fx.login("a2", uri="https://x.com", username="u", password="P2")
    b2 = fx.login("b2", uri="https://x.com", username="u", password="P2")  # reversed order vs A
    b1 = fx.login("b1", uri="https://x.com", username="u", password="P1")
    r1 = merge.three_way([a1, a2], [b2, b1], snapshot.Snapshot())          # first run: establish pairing
    assert {(e.id_on_a, e.id_on_b) for e in r1.new_snapshot.entries.values()} == {("a1", "b1"), ("a2", "b2")}
    r2 = merge.three_way([a1, a2], [b2, b1], r1.new_snapshot)              # second run must converge
    assert r2.ops == []


def test_divergent_first_pairing_is_gated_conflict_not_silent_overwrite():
    # same uri+username, DIFFERENT password, NO prior snapshot -> a real divergence: gate it as a
    # conflict (newest-wins proposed), never silently baseline one side and auto-push over the other.
    a = fx.login("a1", uri="https://x.com", username="u", password="A-pw", revision="2026-02-01T00:00:00.000Z")
    b = fx.login("b1", uri="https://x.com", username="u", password="B-pw", revision="2026-01-01T00:00:00.000Z")
    r = merge.three_way([a], [b], snapshot.Snapshot())
    assert [(o.kind, o.target, o.destructive) for o in r.ops] == [("conflict", "B", True)]


def test_divergence_persists_as_conflict_across_runs():
    # an unresolved divergence must stay a gated conflict every run (base=None), never silently
    # flip to a one-sided auto-edit once the snapshot exists.
    a = fx.login("a1", uri="https://x.com", username="u", password="A", revision="2026-02-01T00:00:00.000Z")
    b = fx.login("b1", uri="https://x.com", username="u", password="B", revision="2026-01-01T00:00:00.000Z")
    r1 = merge.three_way([a], [b], snapshot.Snapshot())
    assert [o.kind for o in r1.ops] == ["conflict"]
    r2 = merge.three_way([a], [b], r1.new_snapshot)
    assert [o.kind for o in r2.ops] == ["conflict"]      # NOT "edit"
    assert r1.new_snapshot.entries[r1.ops[0].link_id].content is None


def test_passkey_guarded_never_destructive():
    a = fx.passkey_login("a1", uri="https://x.com", username="u", password="p")
    r = merge.three_way([a], [], snapshot.Snapshot())
    assert r.ops[0].guarded is True and r.ops[0].destructive is False


def test_passkey_create_is_held_cannot_round_trip():
    # bw export emits empty fido2Credentials, so creating a passkey on the other side is broken
    a = fx.passkey_login("a1", uri="https://x.com", username="u", password="p")
    r = merge.three_way([a], [], snapshot.Snapshot())
    assert r.ops[0].kind == "create" and r.ops[0].guarded is True


def test_ssh_key_is_mirrored_not_held():
    # SSH keys (type 5) export faithfully -> they MUST sync to the backup vault, not be held
    a = fx.ssh_key("s1")
    r = merge.three_way([a], [], snapshot.Snapshot())
    assert [(o.kind, o.target, o.guarded) for o in r.ops] == [("create", "B", False)]


def test_held_passkey_create_not_recorded_in_snapshot():
    # a held create never applies; recording a pairing would make next run see a phantom B-delete
    a = fx.passkey_login("a1", uri="https://x.com", username="u", password="p")
    r = merge.three_way([a], [], snapshot.Snapshot())
    assert len(r.new_snapshot.entries) == 0          # nothing paired -> next run re-proposes the create
