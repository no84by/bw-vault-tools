import base64
import json
import subprocess

import pytest

from bw_vault_tools import bw_adapter


def test_create_strips_cross_vault_scoped_fields():
    captured = {}
    def runner(args, input=None):
        if args[:2] == ["bw", "create"]:
            captured["item"] = json.loads(base64.b64decode(input))
            return json.dumps({"id": "new"})
        return ""
    prof = bw_adapter.BwProfile("/dev/shm/A", "S", runner=runner)
    prof.create({"id": "old", "type": 1, "name": "x", "folderId": "F-in-A",
                 "organizationId": "O-in-A", "collectionIds": ["C"], "login": {}})
    it = captured["item"]
    assert it["folderId"] is None and it["organizationId"] is None and it["collectionIds"] is None


def test_edit_refreshes_revisiondate_on_stale_cipher():
    state = {"edits": 0}
    def runner(args, input=None):
        if args[:2] == ["bw", "edit"]:
            state["edits"] += 1
            if state["edits"] == 1:
                raise bw_adapter.BwError("edit item", "a", 1, "out of date", out_of_date=True)
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

    def __call__(self, args, input=None):
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


def test_default_runner_injects_session_via_env_and_never_via_argv(monkeypatch):
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
    # The session must NEVER reach argv: it would be readable by any local user via `ps`.
    assert "--session" not in captured["cmd"]
    assert "SESS" not in captured["cmd"]
    assert captured["cmd"][:3] == ["bw", "edit", "item"]


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


# --- secrets never on argv / never in error text -------------------------------------------------
SECRET = "hunter2-SECRET-MARKER-xyz"


def _capture_default_runner(monkeypatch, fail_stderr=None):
    calls = []

    def fake_run(cmd, capture_output, text, check, env, input=None):
        calls.append({"cmd": list(cmd), "input": input})
        if fail_stderr is not None:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr=fail_stderr)

        class R:
            stdout = "{}"
        return R()

    monkeypatch.setattr(bw_adapter.subprocess, "run", fake_run)
    return calls


def test_edit_and_create_pass_item_on_stdin_never_argv(monkeypatch):
    calls = _capture_default_runner(monkeypatch)
    item = {"id": "a", "name": "x", "login": {"password": SECRET}}
    prof = bw_adapter.BwProfile("/dev/shm/A", "S")
    prof.edit("a", item)
    prof.create(item)
    enc = base64.b64encode(json.dumps(item).encode()).decode()
    for c in calls:
        joined = " ".join(c["cmd"])
        assert SECRET not in joined and enc not in c["cmd"]
        assert all(len(a) < 60 for a in c["cmd"]), "a long blob is on argv"
    assert calls[0]["cmd"] == ["bw", "edit", "item", "a"]
    assert calls[1]["cmd"] == ["bw", "create", "item"]
    for c in calls:                                      # the encoded item travels on stdin
        assert SECRET in base64.b64decode(c["input"]).decode()


def test_failing_bw_call_error_text_has_no_secret(monkeypatch):
    echoed = base64.b64encode(json.dumps({"login": {"password": SECRET}}).encode()).decode()
    _capture_default_runner(monkeypatch, fail_stderr=f"Error: bad payload {echoed} {SECRET} {{\"password\": \"{SECRET}\"}}")
    prof = bw_adapter.BwProfile("/dev/shm/A", "S")
    with pytest.raises(bw_adapter.BwError) as ei:
        prof.edit("58eed737-id", {"id": "a", "login": {"password": SECRET}})
    text = str(ei.value) + repr(ei.value)
    import traceback
    text += "".join(traceback.format_exception(ei.value))
    assert SECRET not in text and echoed not in text
    assert "edit" in text and "58eed737-id" in text and "exit" in text
    assert ei.value.returncode == 1


def test_bw_error_caps_stderr(monkeypatch):
    _capture_default_runner(monkeypatch, fail_stderr="e" * 5000)
    with pytest.raises(bw_adapter.BwError) as ei:
        bw_adapter.BwProfile("/dev/shm/A", "S").delete("a")
    assert len(str(ei.value)) < 600


def test_stale_cipher_retry_still_works_via_default_runner(monkeypatch):
    n = {"edit": 0}

    def fake_run(cmd, capture_output, text, check, env, input=None):
        class R:
            stdout = json.dumps({"id": "a", "revisionDate": "2030"})
        if cmd[:2] == ["bw", "edit"]:
            n["edit"] += 1
            if n["edit"] == 1:
                raise subprocess.CalledProcessError(1, cmd, stderr="cipher is out of date")
        return R()
    monkeypatch.setattr(bw_adapter.subprocess, "run", fake_run)
    assert bw_adapter.BwProfile("/dev/shm/A", "S").edit("a", {"id": "a"})["id"] == "a"
    assert n["edit"] == 2


def test_bw_error_verb_and_id_ignore_flags(monkeypatch):
    # `bw export --format json` used to report verb "export --format" and item id "json".
    _capture_default_runner(monkeypatch, fail_stderr="boom")
    run = bw_adapter._make_runner("/dev/shm/A", "S")
    for args, want in ((["bw", "export", "--format", "json"], ("export", None)),
                       (["bw", "list", "items", "--search", "x"], ("list items", None)),
                       (["bw", "get", "item", "abc-id"], ("get item", "abc-id"))):
        with pytest.raises(bw_adapter.BwError) as ei:
            run(args)
        assert (ei.value.verb, ei.value.item_id) == want


def test_multiline_leaf_is_redacted_after_whitespace_normalisation(monkeypatch):
    notes = "line one secret\n  line two SECRETNOTE"
    _capture_default_runner(monkeypatch, fail_stderr=f"Error: rejected notes {notes} end")
    prof = bw_adapter.BwProfile("/dev/shm/A", "S")
    with pytest.raises(bw_adapter.BwError) as ei:
        prof.edit("a", {"id": "a", "notes": notes})
    # (the [REDACTED] marker itself is later rewritten to [JSON] by the bracket pattern)
    assert "SECRETNOTE" not in str(ei.value) and "line two" not in str(ei.value)
