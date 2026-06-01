"""Reversibility: encrypted baseline export + journal of typed inverse pre-images."""
import json
import os
import sys
import time


def data_home() -> str:
    """OS-appropriate per-user data dir (XDG on Linux, Application Support on macOS, LOCALAPPDATA
    on Windows)."""
    if sys.platform == "win32":
        return os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support")
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def new_run_dir(tool: str) -> str:
    """A persistent, timestamped run directory holding only ENCRYPTED artifacts (baseline +
    journal). Persisted (NOT tmpfs) so `--undo <run_dir>` still works after the process exits."""
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    d = os.path.join(data_home(), "bw-vault-tools", "runs", f"{tool}-{ts}")
    os.makedirs(d, exist_ok=True)
    return d


class RunDir:
    def __init__(self, base: str, key_provider):
        self.dir, self.kp = base, key_provider
        os.makedirs(os.path.join(self.dir, "00-pre"), exist_ok=True)
        self._journal = os.path.join(self.dir, "journal.ndjson.enc")
        self.completed_ids = {e["item_id"] for e in self.read_journal()}

    def write_baseline(self, label: str, vault: dict) -> None:
        with open(os.path.join(self.dir, "00-pre", f"{label}.json.enc"), "wb") as f:
            f.write(self.kp.encrypt(json.dumps(vault).encode()))

    def read_baseline(self, label: str) -> dict:
        blob = open(os.path.join(self.dir, "00-pre", f"{label}.json.enc"), "rb").read()
        return json.loads(self.kp.decrypt(blob))

    def record(self, item_id: str, inverse: dict) -> None:
        line = self.kp.encrypt(json.dumps({"item_id": item_id, "inverse": inverse}).encode())
        with open(self._journal, "ab") as f:
            f.write(len(line).to_bytes(4, "big") + line)
        self.completed_ids.add(item_id)

    def read_journal(self) -> list:
        out = []
        if not os.path.exists(self._journal):
            return out
        data = open(self._journal, "rb").read()
        i = 0
        while i < len(data):
            n = int.from_bytes(data[i:i + 4], "big")
            i += 4
            out.append(json.loads(self.kp.decrypt(data[i:i + n])))
            i += n
        return out

    def undo_plan(self) -> list:
        """Inverse actions in reverse order; feed each to bw_adapter to reverse the run."""
        return [e["inverse"] for e in reversed(self.read_journal())]
