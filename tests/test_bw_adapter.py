import base64
import json
import subprocess

from bw_vault_tools import bw_adapter


def test_create_strips_cross_vault_scoped_fields():
    captured = {}
    def runner(args):
        if args[:2] == ["bw", "create"]:
            captured["item"] = json.loads(base64.b64decode(args[3]))
            return json.dumps({"id": "new"})
        return ""
    prof = bw_adapter.BwProfile("/dev/shm/A", "S", runner=runner)
    prof.create({"id": "old", "type": 1, "name": "x", "folderId": "F-in-A",
                 "organizationId": "O-in-A", "collectionIds": ["C"], "login": {}})
    it = captured["item"]
    assert it["folderId"] is None and it["organizationId"] is None and it["collectionIds"] is None


def test_edit_refreshes_revisiondate_on_stale_cipher():
    state = {"edits": 0}
    def runner(args):
        if args[:2] == ["bw", "edit"]:
            state["edits"] += 1
            if state["edits"] == 1:
                raise subprocess.CalledProcessError(
                    1, args, stderr="The client copy of this cipher is out of date. Resync and retry.")
            return json.dumps({"id": "a"})
        if args[:2] == ["bw", "get"]:
            return json.dumps({"id": "a", "revisionDate": "2030-01-01T00:00:00.000Z"})
        return ""
    prof = bw_adapter.BwProfile("/dev/shm/A", "S", runner=runner)
    out = prof.edit("a", {"id": "a", "name": "x", "revisionDate": "2020-01-01T00:00:00.000Z"})
    assert out["id"] == "a" and state["edits"] == 2          # retried once after refreshing


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


def test_list_organizations_parses():
    r_orgs = '[{"id":"o1","name":"Familion","type":0,"status":2,"enabled":true}]'

    def runner(args):
        if args[:2] == ["bw", "list"] and "organizations" in args:
            return r_orgs
        return "[]"
    prof = bw_adapter.BwProfile("/dev/shm/A", "S", runner=runner)
    orgs = prof.list_organizations()
    assert orgs[0]["name"] == "Familion"


def test_list_items_parses():
    def runner(args):
        if args[:3] == ["bw", "list", "items"]:
            return '[{"id":"a","organizationId":"o1"},{"id":"b","organizationId":null}]'
        return "[]"
    prof = bw_adapter.BwProfile("/dev/shm/A", "S", runner=runner)
    items = prof.list_items()
    assert {i["id"] for i in items} == {"a", "b"}
