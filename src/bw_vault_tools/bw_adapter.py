"""Drive one Bitwarden `bw` CLI profile (isolated app-data dir + session)."""
import base64
import json
import os
import subprocess


def _make_runner(appdata_dir: str, session: str):
    """The single place env + --session are injected."""
    def run(args: list) -> str:
        env = dict(os.environ, BITWARDENCLI_APPDATA_DIR=appdata_dir, BW_SESSION=session)
        full = args + ["--session", session] if args[:1] == ["bw"] else args
        return subprocess.run(full, capture_output=True, text=True, check=True, env=env).stdout
    return run


class BwProfile:
    def __init__(self, appdata_dir: str, session: str, runner=None):
        self.appdata_dir, self.session = appdata_dir, session
        self._run = runner or _make_runner(appdata_dir, session)

    def export(self) -> dict:
        return json.loads(self._run(["bw", "export", "--format", "json", "--raw"]))

    def list_organizations(self) -> list:
        return json.loads(self._run(["bw", "list", "organizations"]))

    def list_items(self) -> list:
        return json.loads(self._run(["bw", "list", "items"]))

    def get(self, item_id: str) -> dict:
        return json.loads(self._run(["bw", "get", "item", item_id]))

    def create(self, item: dict) -> dict:
        # the item comes from a different vault (sync) or a CSV (import): strip vault-scoped fields
        # so it lands as a clean personal item — a source folderId/org is invalid in the target.
        item = {**item, "folderId": None, "organizationId": None, "collectionIds": None}
        return json.loads(self._run(["bw", "create", "item", _enc(item)]))

    def edit(self, item_id: str, item: dict) -> dict:
        try:
            return json.loads(self._run(["bw", "edit", "item", item_id, _enc(item)]))
        except subprocess.CalledProcessError as e:
            if "out of date" not in (e.stderr or ""):
                raise
            # optimistic-lock miss (a prior edit in this run bumped the cipher): refresh the
            # item's revisionDate from the server and retry once.
            item = {**item, "revisionDate": self.get(item_id).get("revisionDate")}
            return json.loads(self._run(["bw", "edit", "item", item_id, _enc(item)]))

    def delete(self, item_id: str, permanent: bool = False) -> None:
        args = ["bw", "delete", "item", item_id] + (["--permanent"] if permanent else [])
        self._run(args)

    def restore(self, item_id: str) -> None:
        self._run(["bw", "restore", "item", item_id])


def _enc(item: dict) -> str:
    return base64.b64encode(json.dumps(item).encode()).decode()
