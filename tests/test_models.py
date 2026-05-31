from bw_vault_tools import models
from tests import fixtures as fx


def test_item_type():
    assert models.item_type(fx.login("a")) == 1
    assert models.item_type(fx.ssh_key("b")) == 5
    assert models.item_type({"name": "x"}) is None


def test_is_login():
    assert models.is_login(fx.login("a")) is True
    assert models.is_login(fx.note("n")) is False


def test_has_passkey():
    assert models.has_passkey(fx.passkey_login("a")) is True
    assert models.has_passkey(fx.login("b")) is False
    assert models.has_passkey(fx.note("n")) is False


def test_has_uris():
    assert models.has_uris(fx.login("a")) is True
    assert models.has_uris(fx.no_uri_login("b")) is False
    assert models.has_uris(fx.note("n")) is False
