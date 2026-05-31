from bw_vault_tools import identity
from tests import fixtures as fx


def test_normalize_uri_strips_scheme_and_path():
    assert identity.normalize_uri("https://Example.com/login/") == "example.com"
    assert identity.normalize_uri("http://example.com") == "example.com"


def test_normalize_uri_android_with_and_without_trailing_slash():
    assert identity.normalize_uri("androidapp://com.example.app") == "com.example.app"
    assert identity.normalize_uri("android://hash@com.example.app/") == "com.example.app"
    assert identity.normalize_uri("android://hash@com.example.app") == "com.example.app"


def test_normalize_uri_tolerates_null_and_empty():
    assert identity.normalize_uri(None) == ""
    assert identity.normalize_uri("") == ""


def test_fingerprint_same_creds_match():
    a = fx.login("a", uri="https://site.com", username="u", password="p")
    b = fx.login("b", uri="http://site.com/login", username="u", password="p")
    assert identity.fingerprint(a) == identity.fingerprint(b)


def test_fingerprint_differs_on_password_when_included():
    a, b = fx.login("a", password="p1"), fx.login("b", password="p2")
    assert identity.fingerprint(a) != identity.fingerprint(b)


def test_fingerprint_ignores_password_when_excluded():
    a, b = fx.login("a", password="p1"), fx.login("b", password="p2")
    assert identity.fingerprint(a, include_password=False) == identity.fingerprint(b, include_password=False)


def test_group_logins_buckets_by_fingerprint():
    items = [fx.login("a", username="u", password="p"),
             fx.login("b", uri="https://site.com/x", username="u", password="p"),
             fx.login("c", username="other", password="p")]
    assert sorted(len(g) for g in identity.group_logins(items).values()) == [1, 2]


def test_pick_best_prefers_newer_revision():
    a = fx.login("a", revision="2026-01-01T00:00:00.000Z")
    b = fx.login("b", revision="2026-05-01T00:00:00.000Z")
    assert identity.pick_best([a, b], reused=set())["id"] == "b"


def test_pick_best_prefers_unique_over_reused_password():
    a = fx.login("a", password="reused")
    b = fx.login("b", password="unique")
    assert identity.pick_best([a, b], reused={"reused"})["id"] == "b"


def test_merge_uris_unions_and_sorts():
    a = fx.login("a", uri="https://site.com")
    b = fx.login("b", uri="https://app.site.com")
    assert identity.merge_uris([a, b]) == ["https://app.site.com", "https://site.com"]


def test_merge_uris_skips_null_uris():
    a = fx.login("a", uri="https://site.com")
    a["login"]["uris"].append({"uri": None, "match": None})
    assert identity.merge_uris([a]) == ["https://site.com"]
