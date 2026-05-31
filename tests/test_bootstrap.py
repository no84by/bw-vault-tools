from bw_vault_tools import bootstrap


def test_parse_bw_version():
    assert bootstrap.parse_bw_version("2026.4.2") == (2026, 4, 2)
    assert bootstrap.parse_bw_version("2026.5.0\n") == (2026, 5, 0)


def test_version_band():
    assert bootstrap.version_supported((2026, 4, 2)) is True
    assert bootstrap.version_supported((2026, 5, 9)) is True
    assert bootstrap.version_supported((2025, 12, 0)) is False
    assert bootstrap.version_supported((2027, 1, 0)) is False
