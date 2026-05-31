import json

from bw_vault_tools import bw_adapter


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["bw", "export"]:
            return json.dumps({"items": [{"id": "a"}]})
        if args[:2] == ["bw", "edit"]:
            return json.dumps({"id": "a"})
        return ""


def test_export_parses_json_and_requests_json_format():
    r = FakeRunner()
    prof = bw_adapter.BwProfile("/dev/shm/A", "SESS", runner=r)
    assert prof.export()["items"][0]["id"] == "a"
    assert "--format" in r.calls[-1] and "json" in r.calls[-1]


def test_delete_soft_default_and_permanent():
    r = FakeRunner()
    prof = bw_adapter.BwProfile("/dev/shm/A", "SESS", runner=r)
    prof.delete("a")
    assert r.calls[-1] == ["bw", "delete", "item", "a"]
    prof.delete("a", permanent=True)
    assert "--permanent" in r.calls[-1]


def test_restore_calls_bw_restore():
    r = FakeRunner()
    bw_adapter.BwProfile("/dev/shm/A", "SESS", runner=r).restore("a")
    assert r.calls[-1] == ["bw", "restore", "item", "a"]


def test_default_runner_injects_env_and_session(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, check, env, input=None):
        captured["cmd"], captured["env"] = cmd, env

        class R:
            stdout = "{}"
        return R()

    monkeypatch.setattr(bw_adapter.subprocess, "run", fake_run)
    prof = bw_adapter.BwProfile("/dev/shm/A", "SESS")
    prof.edit("a", {"id": "a"})
    assert captured["env"]["BITWARDENCLI_APPDATA_DIR"] == "/dev/shm/A"
    assert captured["env"]["BW_SESSION"] == "SESS"
    assert captured["cmd"][-2:] == ["--session", "SESS"]
