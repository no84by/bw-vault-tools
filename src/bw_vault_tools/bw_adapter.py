"""Drive one Bitwarden `bw` CLI profile (isolated app-data dir + session)."""
import base64
import json
import os
import re
import subprocess


class BwError(RuntimeError):
    """A `bw` call failed. Carries ONLY: the verb, the item id, the exit code and a capped,
    redacted slice of bw's stderr. Never argv, never stdin, never stdout -- `bw edit/create`
    echo the whole item (passwords included) and a CalledProcessError traceback prints argv.
    """

    def __init__(self, verb: str, item_id, returncode, stderr: str = "", out_of_date: bool = False):
        self.verb, self.item_id, self.returncode = verb, item_id, returncode
        self.out_of_date = out_of_date
        self.stderr_redacted = stderr
        super().__init__(f"bw {verb}{' ' + item_id if item_id else ''} failed (exit {returncode})"
                         f"{': ' + stderr if stderr else ''}")


_B64 = re.compile(r"[A-Za-z0-9+/=_-]{24,}")
_JSON_BLOB = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]")
_STDERR_CAP = 200
_ID_VERBS = {"get", "edit", "delete", "restore"}   # verbs whose 4th argv slot is an item id


def _leaf_strings(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            _leaf_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _leaf_strings(v, out)
    elif isinstance(obj, str) and len(obj) >= 6:
        out.add(obj)


def _redact(text: str, stdin_item: str = "") -> str:
    """Strip anything that could be item content from bw's stderr, then cap it."""
    text = " ".join((text or "").split())
    if stdin_item:
        try:
            leaves = set()
            _leaf_strings(json.loads(base64.b64decode(stdin_item)), leaves)
            # `text` is whitespace-normalised above, so a multi-line notes value only matches
            # once it is normalised the same way.
            leaves = {" ".join(leaf.split()) for leaf in leaves} - {""}
            for leaf in sorted(leaves, key=len, reverse=True):
                text = text.replace(leaf, "[REDACTED]")
        except Exception:
            pass
    text = _JSON_BLOB.sub("[JSON]", text)
    text = _B64.sub("[B64]", text)
    return text[:_STDERR_CAP]


def _make_runner(appdata_dir: str, session: str):
    """The single place the app-data dir + session are injected.

    The session travels in the ENVIRONMENT only (never `--session`, which leaked via `ps`).
    An item / any secret payload is passed as `input=` (STDIN): `bw edit item <id>` and
    `bw create item` read the encoded JSON from stdin when the argument is omitted. Failures are
    re-raised as a sanitized BwError (see its docstring), with the CalledProcessError suppressed.
    """
    def run(args: list, input=None) -> str:
        env = dict(os.environ, BITWARDENCLI_APPDATA_DIR=appdata_dir, BW_SESSION=session)
        try:
            return subprocess.run(args, capture_output=True, text=True, check=True, env=env,
                                  input=input).stdout
        except subprocess.CalledProcessError as e:
            raw = e.stderr or ""
            # "edit item", "list items", "export" -- never a flag or its value (`export --format json`)
            obj = args[2] if len(args) > 2 and not args[2].startswith("-") else None
            verb = args[1] + (f" {obj}" if obj else "")
            item_id = (args[3] if obj and args[1] in _ID_VERBS and len(args) > 3
                       and not args[3].startswith("-") else None)
            raise BwError(verb, item_id, e.returncode, _redact(raw, input or ""),
                          out_of_date="out of date" in raw) from None
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
        return _echo_json(self._run(["bw", "create", "item"], input=_enc(item)), "create item")

    def edit(self, item_id: str, item: dict) -> dict:
        try:
            return _echo_json(self._run(["bw", "edit", "item", item_id], input=_enc(item)),
                              "edit item", item_id)
        except BwError as e:
            if not e.out_of_date:
                raise
            # optimistic-lock miss (a prior edit in this run bumped the cipher): refresh the
            # item's revisionDate from the server and retry once.
            item = {**item, "revisionDate": self.get(item_id).get("revisionDate")}
            return _echo_json(self._run(["bw", "edit", "item", item_id], input=_enc(item)),
                              "edit item", item_id)

    def delete(self, item_id: str, permanent: bool = False) -> None:
        args = ["bw", "delete", "item", item_id] + (["--permanent"] if permanent else [])
        self._run(args)

    def restore(self, item_id: str) -> None:
        self._run(["bw", "restore", "item", item_id])


def _enc(item: dict) -> str:
    return base64.b64encode(json.dumps(item).encode()).decode()


def _echo_json(out: str, verb: str, item_id=None) -> dict:
    """Parse the item bw echoes back from edit/create. A parse failure must not carry the output."""
    try:
        return json.loads(out)
    except ValueError:
        raise BwError(verb, item_id, 0, "non-JSON output (suppressed)") from None
