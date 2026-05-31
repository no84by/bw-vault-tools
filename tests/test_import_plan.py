from bw_vault_tools import import_plan


def _login(id, uri="https://x.com", user="u", pw="p"):
    return {"id": id, "type": 1, "name": uri, "notes": None,
            "login": {"uris": [{"uri": uri, "match": None}], "username": user,
                      "password": pw, "totp": None, "fido2Credentials": []}}


def test_new_candidate_becomes_create():
    live = [_login("L1", user="alice", pw="p1")]
    cand = [_login("C1", uri="https://new.com", user="bob", pw="p2")]
    plan = import_plan.build_import_plan(cand, live)
    assert [o.item["id"] for o in plan.creates] == ["C1"]
    assert plan.skipped == 0


def test_candidate_already_in_vault_is_skipped():
    live = [_login("L1", uri="https://x.com", user="alice", pw="p1")]
    cand = [_login("C1", uri="http://x.com/login", user="alice", pw="p1")]  # same fingerprint
    plan = import_plan.build_import_plan(cand, live)
    assert plan.creates == [] and plan.skipped == 1


def test_same_site_user_different_password_is_created_not_overwrite():
    live = [_login("L1", uri="https://x.com", user="alice", pw="OLD")]
    cand = [_login("C1", uri="https://x.com", user="alice", pw="NEW")]
    plan = import_plan.build_import_plan(cand, live)
    assert [o.item["id"] for o in plan.creates] == ["C1"]   # never overwrites L1


def test_duplicate_candidates_collapse():
    cand = [_login("C1", user="u", pw="p"), _login("C2", user="u", pw="p")]  # identical fp
    plan = import_plan.build_import_plan(cand, [])
    assert len(plan.creates) == 1
