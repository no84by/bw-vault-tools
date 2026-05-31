import pytest

from bw_vault_tools import keyprovider


def test_passphrase_roundtrip():
    kp = keyprovider.PassphraseProvider("correct horse battery staple")
    blob = kp.encrypt(b"top secret")
    assert blob != b"top secret"
    assert kp.decrypt(blob) == b"top secret"


def test_wrong_passphrase_fails():
    blob = keyprovider.PassphraseProvider("right").encrypt(b"x")
    with pytest.raises(Exception):
        keyprovider.PassphraseProvider("wrong").decrypt(blob)


def test_tpm2_deferred():
    with pytest.raises(NotImplementedError):
        keyprovider.from_config("tpm2")
