import os

from bw_vault_tools import sources

FX = os.path.join(os.path.dirname(__file__), "fixtures")


def test_classify_export_kinds():
    assert sources.classify_export(os.path.join(FX, "chromium.csv")) == "chromium_csv"
    assert sources.classify_export(os.path.join(FX, "firefox.csv")) == "firefox_csv"
    assert sources.classify_export(os.path.join(FX, "safari.csv")) == "safari_csv"
    assert sources.classify_export(os.path.join(FX, "random.csv")) is None


def test_classify_handles_utf8_bom(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(b"\xef\xbb\xbfname,url,username,password,note\nX,https://x.com,u,pw,\n")
    assert sources.classify_export(str(p)) == "chromium_csv"


def test_csv_to_items_maps_and_assigns_uuid():
    items = sources.csv_to_items(os.path.join(FX, "chromium.csv"), "chromium_csv")
    assert len(items) == 2
    a = items[0]
    assert a["type"] == 1 and a["login"]["username"] == "alice"
    assert a["login"]["uris"][0]["uri"] == "https://example.com"
    assert len(a["id"]) == 36 and a["login"]["fido2Credentials"] == []
    assert items[1]["login"]["uris"] is None          # NoUrlRow


def test_detect_browsers_presence_only(tmp_path, monkeypatch):
    (tmp_path / ".mozilla" / "firefox").mkdir(parents=True)
    monkeypatch.setattr(sources.platform, "system", lambda: "Linux")
    found = sources.detect_browsers(home=str(tmp_path))
    assert "firefox" in found and "chrome" not in found


def test_scan_for_exports(tmp_path):
    import shutil
    for fn in ("chromium.csv", "random.csv"):
        shutil.copy(os.path.join(FX, fn), tmp_path / fn)
    found = sources.scan_for_exports([str(tmp_path)])
    assert [k for _, k in found] == ["chromium_csv"]


def test_collect_source_watch_and_skip():
    hit = sources.collect_source("chrome", expected_kinds={"chromium_csv"},
                                 watch=lambda k, s: os.path.join(FX, "chromium.csv"),
                                 ask=lambda p: "", now=lambda: 0.0, info=lambda *_: None)
    assert hit.endswith("chromium.csv")
    skip = sources.collect_source("chrome", expected_kinds={"chromium_csv"},
                                  watch=lambda k, s: None, ask=lambda p: "", now=lambda: 9.0,
                                  timeout=0.0, info=lambda *_: None)
    assert skip is None
