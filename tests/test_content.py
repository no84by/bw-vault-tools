from bw_vault_tools import content
from tests import fixtures as fx


def test_same_content_same_key_ignoring_id_and_dates():
    a = fx.login("a", uri="https://x.com", username="u", password="p", revision="2026-01-01T00:00:00.000Z")
    b = fx.login("b", uri="https://x.com", username="u", password="p", revision="2026-09-09T00:00:00.000Z")
    assert content.content_key(a) == content.content_key(b)


def test_password_change_changes_key():
    a = fx.login("a", password="p1")
    b = fx.login("b", password="p2")
    assert content.content_key(a) != content.content_key(b)


def test_notes_and_uris_affect_key():
    a = fx.login("a", uri="https://x.com", notes="hi")
    b = fx.login("b", uri="https://x.com", notes="bye")
    assert content.content_key(a) != content.content_key(b)
