"""Fold regression: the cascade now lives *inside* bw_vault_tools (it was the standalone
bw-cascade package). These lock the merged surface — version, entry-point internals, and the two
pre-fold security hardening choices (no baked-in gateway URL; credential hints are filenames only)."""

from bw_vault_tools import cascade, gateway
from bw_vault_tools import policy  # noqa: F401  (import surface present)
import bw_vault_tools


def test_version_is_0_4_0():
    assert bw_vault_tools.__version__ == "0.4.0"


def test_cascade_exposes_main_and_run():
    assert callable(cascade.main)
    assert callable(cascade.run)


def test_reason_short_circuits_without_url(monkeypatch):
    # gateway defaults to no URL; with none set it must NOT attempt a network call.
    monkeypatch.setattr(gateway, "GATEWAY_URL", "")
    assert gateway.reason(system="s", prompt="p", timeout=1) is None


def test_credential_hint_is_filename_only():
    # Estate convention: name only the cred *filename* (never the full credstore host path).
    assert cascade._CRED_HINT
    for value in cascade._CRED_HINT.values():
        assert value.endswith(".cred")
        assert "/" not in value
