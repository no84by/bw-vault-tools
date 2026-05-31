# bw-vault-tools — Core + bw-dedup Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared core of `bw-vault-tools` plus the `bw-dedup` single-vault deduplicator: a safe, reversible, bw-CLI-driven tool that deduplicates one live vault via per-item deltas, never purge+reimport.

**Architecture:** Pure-function core (`identity`, `plan`) decides *what* to change from an exported item list; IO modules (`bw_adapter`, `checkpoint`, `keyprovider`, `tmpfs`, `bootstrap`) execute it safely. `cli_dedup` wires them with an inline-TTY approval gate. Every destructive op is gated; every input item round-trips unless explicitly/approvedly removed (the issue-#1 invariant). `bw-sync` (Plan 2) reuses every module here.

**Tech Stack:** Python 3.11+, stdlib only except `cryptography` (passphrase key provider, self-installed via a lazy-import guard). `pytest` for tests. Drives the Bitwarden `bw` CLI via `subprocess`.

**Repo:** `/home/xt8664/workspace/code/platform/bw-vault-tools` (already scaffolded).
**Spec:** `docs/superpowers/specs/2026-05-31-bw-dedup-cli-design.md` (+ sync spec §2/§5, compat matrix).

---

## Revision 2 — feature-dev architecture review (2026-05-31)

Rev 1 was reviewed by three parallel architects (clean / correctness / simplicity). Binding changes folded in below:

- **Dedup classification fixed (correctness-critical):** use v2.0's proven three-field `all_same` check (password AND revisionDate AND creationDate), not Rev-1's invented URI heuristic. Port the notes-merge.
- **`check_invariant` made real:** Rev-1's predicate was a tautology. Now full accounting + a negative test.
- **Undo uses `restore`, not `create`:** the inverse of a soft-delete is `bw restore <id>` (same ID), never a new item.
- **Reuse index over all items**, not deduped-only (passkey/preserved items can share a password).
- **Bootstrap = lazy-import guard** (operator choice): pip-install `cryptography` on `ImportError`; no venv/re-exec. `bootstrap.py` is just `ensure_bw()` + version parse.
- **`bw_adapter`:** one runner factory inside the adapter (env+session in one place); no dead `_bw`, no `input=` kwarg, no `list_items` in Plan 1.
- **`fingerprint(include_password=True)`** — dedup keys on password; Plan-2 sync pairing will call with `include_password=False` (keys on type) so a password change across vaults still matches.
- **Typed `Op.inverse()`** instead of freeform journal dicts; **`_folder_op` extracted** to a pure function.
- **Re-run safety:** `RunDir.completed_ids` skips already-applied item ops after an interrupted run.
- **`key_provider` required when `apply=True`** (no silent `_NullRun`). **`Tpm2Provider` deferred to Plan 2** (stub raises). android-URI regex fixed (`@(.+?)(?:/|$)`).

## Revision 3 — carried from bitwarden-vault-cleanup v2.1 (2026-05-31)

Hardening lessons from the v2.1 review of the predecessor script, ported here so the rewrite ships with them from day one:

- **`normalize_uri` null/empty-safe** (Task 2): `{"uri": null}` entries returned `""` instead of crashing the run (`AttributeError`). Test added.
- **`merge_uris` skips null URIs** (Task 4): a `None` uri would otherwise make `sorted()` raise. Test added.
- **Export-shape validation** (Task 12 `_validate_export`): reject a non-dict / missing-`items` export, warn on zero items, before any plan/apply. (Encrypted-export case is N/A — `bw export` is always decrypted JSON — so it is intentionally not re-checked here.)
- Already covered, no carry needed: no plaintext secrets printed (logs counts only), non-TTY refuses destructive ops, no-plaintext-at-rest (tmpfs+shred + encrypted snapshot — stronger than the script's 0600), preserve-non-deduplicable invariant (Cornerstone 4), `all_same` dedup.

---

## File Structure (Plan 1)

```
src/bw_vault_tools/
  models.py       item dict helpers: item_type, is_login, has_passkey, has_uris, first_uri
  identity.py     PURE: normalize_uri, fingerprint(include_password), group_logins, score/pick_best, merge_uris
  plan.py         PURE: typed ops (+inverse), classify safe/destructive, passkey/SSH guard, build_dedup_plan, check_invariant
  tmpfs.py        /dev/shm staging + shred-on-exit
  keyprovider.py  KeyProvider ABC; PassphraseProvider (AES-GCM+scrypt); Tpm2Provider (Plan-2 stub)
  bw_adapter.py   BwProfile: one runner factory (env+session); export/create/edit/delete/restore
  checkpoint.py   RunDir: encrypted baseline + journal (typed inverse) + completed_ids + undo_plan
  bootstrap.py    lazy-import guard + ensure_bw (detect/version-gate)
  cli_dedup.py    run_dedup (plan/apply/approve/journal/undo) + main()
tests/  fixtures.py + one test_*.py per module
```

Item types: 1 login, 2 note, 3 card, 4 identity, 5 SSH key. Items are plain dicts.

---

## Task 1: Test harness + item-model helpers

**Files:** Create `tests/fixtures.py`, `src/bw_vault_tools/models.py`, `tests/test_models.py`.

- [ ] **Step 1: `tests/fixtures.py`**

```python
"""Builders for Bitwarden export item dicts used in tests."""

def login(id, name="site", uri="https://site.com", username="u", password="p",
          fido2=None, folder_id=None, revision="2026-01-01T00:00:00.000Z",
          creation="2026-01-01T00:00:00.000Z", notes=None):
    uris = [{"uri": uri, "match": None}] if uri is not None else None
    return {"id": id, "organizationId": None, "folderId": folder_id, "type": 1,
            "name": name, "notes": notes, "revisionDate": revision, "creationDate": creation,
            "login": {"uris": uris, "username": username, "password": password,
                      "totp": None, "fido2Credentials": fido2 or []}}

def passkey_login(id, **kw):
    return login(id, fido2=[{"credentialId": "c-" + id, "rpId": "site.com"}], **kw)

def no_uri_login(id, **kw):
    return login(id, uri=None, **kw)

def note(id, name="note", text="secret"):
    return {"id": id, "organizationId": None, "folderId": None, "type": 2,
            "name": name, "notes": text, "secureNote": {"type": 0}}

def ssh_key(id, name="key"):
    return {"id": id, "organizationId": None, "folderId": None, "type": 5, "name": name,
            "notes": None, "sshKey": {"privateKey": "PRIV", "publicKey": "ssh-ed25519 AAAA",
                                       "keyFingerprint": "SHA256:x"}}

def unknown_type(id, type=99, name="weird"):
    return {"id": id, "organizationId": None, "folderId": None, "type": type, "name": name}

def vault(items, folders=None):
    return {"encrypted": False, "folders": folders or [], "items": items}
```

- [ ] **Step 2: failing test** — `tests/test_models.py`

```python
from bw_vault_tools import models
from tests import fixtures as fx

def test_item_type():
    assert models.item_type(fx.login("a")) == 1
    assert models.item_type(fx.ssh_key("b")) == 5
    assert models.item_type({"name": "x"}) is None

def test_is_login():
    assert models.is_login(fx.login("a")) is True
    assert models.is_login(fx.note("n")) is False

def test_has_passkey():
    assert models.has_passkey(fx.passkey_login("a")) is True
    assert models.has_passkey(fx.login("b")) is False           # fido2Credentials == []
    assert models.has_passkey(fx.note("n")) is False

def test_has_uris():
    assert models.has_uris(fx.login("a")) is True
    assert models.has_uris(fx.no_uri_login("b")) is False
    assert models.has_uris(fx.note("n")) is False
```

- [ ] **Step 3: run, expect FAIL** — `pip install -e '.[dev]'` then `python -m pytest tests/test_models.py -v` (FAIL: no module `models`).

- [ ] **Step 4: implement `models.py`**

```python
"""Helpers for reading Bitwarden export item dicts (no mutation)."""

def item_type(item: dict) -> int | None:
    t = item.get("type")
    return t if isinstance(t, int) else None

def is_login(item: dict) -> bool:
    return isinstance(item.get("login"), dict)

def _login(item: dict) -> dict:
    lg = item.get("login")
    return lg if isinstance(lg, dict) else {}

def has_passkey(item: dict) -> bool:
    return bool(_login(item).get("fido2Credentials"))

def has_uris(item: dict) -> bool:
    return bool(_login(item).get("uris"))

def first_uri(item: dict) -> str | None:
    uris = _login(item).get("uris") or []
    return uris[0]["uri"] if uris else None
```

- [ ] **Step 5: run, expect PASS** (4 tests).
- [ ] **Step 6: commit** — `git add ...; git commit -m "feat(core): item-model helpers + test fixtures"`

---

## Task 2: `identity.py` — normalize_uri (android regex fixed)

**Files:** Create `src/bw_vault_tools/identity.py`, `tests/test_identity.py`.

- [ ] **Step 1: failing test** — `tests/test_identity.py`

```python
from bw_vault_tools import identity

def test_normalize_uri_strips_scheme_and_path():
    assert identity.normalize_uri("https://Example.com/login/") == "example.com"
    assert identity.normalize_uri("http://example.com") == "example.com"

def test_normalize_uri_android_with_and_without_trailing_slash():
    assert identity.normalize_uri("androidapp://com.example.app") == "com.example.app"
    assert identity.normalize_uri("android://hash@com.example.app/") == "com.example.app"
    assert identity.normalize_uri("android://hash@com.example.app") == "com.example.app"  # no slash

def test_normalize_uri_tolerates_null_and_empty():
    # carried from bitwarden-vault-cleanup v2.1: a {"uri": null} entry must not crash the run
    assert identity.normalize_uri(None) == ""
    assert identity.normalize_uri("") == ""
```

- [ ] **Step 2: run, expect FAIL** (no attribute `normalize_uri`).

- [ ] **Step 3: implement** — `src/bw_vault_tools/identity.py`

```python
"""Pure identity/dedup functions. Algorithm ported from bitwarden-vault-cleanup (MIT)."""
import re

def normalize_uri(uri: str | None) -> str:
    if not uri:                                        # tolerate {"uri": null} / empty (v2.1 carry)
        return ""
    if uri.startswith("android://") or uri.startswith("androidapp://"):
        m = re.search(r"@(.+?)(?:/|$)", uri)          # tolerate missing trailing slash
        return m.group(1) if m else uri.split("://")[-1]
    uri = re.sub(r"^https?://", "", uri).lower().rstrip("/")
    return uri.split("/")[0]
```

- [ ] **Step 4: run, expect PASS** (3 tests). **Step 5: commit** `feat(identity): normalize_uri (android-safe, null-safe, MIT-credited)`.

---

## Task 3: `identity.py` — fingerprint (parameterized) + group_logins

**Files:** Modify `identity.py`, `tests/test_identity.py`.

- [ ] **Step 1: failing test** — append

```python
from tests import fixtures as fx

def test_fingerprint_same_creds_match():
    a = fx.login("a", uri="https://site.com", username="u", password="p")
    b = fx.login("b", uri="http://site.com/login", username="u", password="p")
    assert identity.fingerprint(a) == identity.fingerprint(b)

def test_fingerprint_differs_on_password_when_included():
    a, b = fx.login("a", password="p1"), fx.login("b", password="p2")
    assert identity.fingerprint(a) != identity.fingerprint(b)

def test_fingerprint_ignores_password_when_excluded():
    # Plan-2 sync pairing: same item, password changed on one side -> still matches.
    a, b = fx.login("a", password="p1"), fx.login("b", password="p2")
    assert identity.fingerprint(a, include_password=False) == identity.fingerprint(b, include_password=False)

def test_group_logins_buckets_by_fingerprint():
    items = [fx.login("a", username="u", password="p"),
             fx.login("b", uri="https://site.com/x", username="u", password="p"),
             fx.login("c", username="other", password="p")]
    assert sorted(len(g) for g in identity.group_logins(items).values()) == [1, 2]
```

- [ ] **Step 2: run, expect FAIL** (no attribute `fingerprint`).

- [ ] **Step 3: implement** — append to `identity.py`

```python
from collections import defaultdict
from . import models

def fingerprint(item: dict, include_password: bool = True) -> tuple:
    """Stable key for 'same logical login'.

    Dedup (one vault, find same-credential dupes): include_password=True ->
        (normalized-uri, username, password).
    Sync first-run pairing (two vaults, same item may have a changed password):
        include_password=False -> (normalized-uri, username, type).
    Defined only for logins that have at least one URI.
    """
    uri = models.first_uri(item)
    login = item.get("login", {})
    base = (normalize_uri(uri) if uri else None, login.get("username"))
    if include_password:
        pw = login.get("password") or ""
        if not isinstance(pw, str):
            pw = str(pw)
        return base + (pw.strip(),)
    return base + (models.item_type(item),)

def group_logins(items: list[dict]) -> dict[tuple, list[dict]]:
    """Group URI-bearing logins by dedup fingerprint. Items without URIs are NOT grouped
    (cannot be matched) — the caller preserves them."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for it in items:
        if models.is_login(it) and models.has_uris(it):
            groups[fingerprint(it)].append(it)
    return dict(groups)
```

- [ ] **Step 4: run, expect PASS** (6 tests). **Step 5: commit** `feat(identity): fingerprint(include_password) + group_logins`.

---

## Task 4: `identity.py` — scoring, pick_best, merge_uris

**Files:** Modify `identity.py`, `tests/test_identity.py`.

- [ ] **Step 1: failing test** — append

```python
def test_pick_best_prefers_newer_revision():
    a = fx.login("a", revision="2026-01-01T00:00:00.000Z")
    b = fx.login("b", revision="2026-05-01T00:00:00.000Z")
    assert identity.pick_best([a, b], reused=set())["id"] == "b"

def test_pick_best_prefers_unique_over_reused_password():
    a = fx.login("a", password="reused")
    b = fx.login("b", password="unique")
    assert identity.pick_best([a, b], reused={"reused"})["id"] == "b"

def test_merge_uris_unions_and_sorts():
    a = fx.login("a", uri="https://site.com")
    b = fx.login("b", uri="https://app.site.com")
    assert identity.merge_uris([a, b]) == ["https://app.site.com", "https://site.com"]

def test_merge_uris_skips_null_uris():
    # carried from bitwarden-vault-cleanup v2.1: a null uri must not crash sorted()
    a = fx.login("a", uri="https://site.com")
    a["login"]["uris"].append({"uri": None, "match": None})
    assert identity.merge_uris([a]) == ["https://site.com"]
```

- [ ] **Step 2: run, expect FAIL** (no attribute `pick_best`).

- [ ] **Step 3: implement** — append to `identity.py`

```python
def _last_used(item: dict) -> str:
    return max((h.get("lastUsedDate", "") for h in (item.get("passwordHistory") or [])), default="")

def score_entry(item: dict, reused: set[str]) -> tuple:
    """Sort key; MAX entry is the one to keep (mirrors v2.0):
    lastUsedDate, revisionDate, password-uniqueness, creationDate, id."""
    login = item.get("login", {})
    return (_last_used(item), item.get("revisionDate", ""),
            (login.get("password") or "") not in reused,
            item.get("creationDate", ""), item.get("id", ""))

def pick_best(group: list[dict], reused: set[str]) -> dict:
    return sorted(group, key=lambda e: score_entry(e, reused))[-1]

def merge_uris(group: list[dict]) -> list[str]:
    # filter falsy uris (v2.1 carry): a {"uri": null} entry would otherwise make sorted() raise
    return sorted({u["uri"] for e in group for u in (e.get("login", {}).get("uris") or []) if u.get("uri")})
```

- [ ] **Step 4: run, expect PASS** (9 tests). **Step 5: commit** `feat(identity): score_entry + pick_best + merge_uris`.

---

## Task 5: `plan.py` — typed ops with `inverse()`

**Files:** Create `src/bw_vault_tools/plan.py`, `tests/test_plan.py`.

- [ ] **Step 1: failing test** — `tests/test_plan.py`

```python
from bw_vault_tools import plan

def test_op_destructive_flags():
    assert plan.DeleteOp("x", {"id": "x"}).destructive is True
    assert plan.MergeOp("a", ["b"], [], {}).destructive is True
    assert plan.AssignFolderOp("a", "f", "F").destructive is False
    assert plan.FlagReusedOp("a", "!").destructive is False

def test_delete_inverse_is_restore():
    inv = plan.DeleteOp("x", {"id": "x"}).inverse()
    assert inv == {"action": "restore", "item_id": "x"}

def test_merge_inverse_restores_keep_and_restores_drops():
    op = plan.MergeOp(keep_id="a", drop_ids=["b"], uris=["u"], keep_before={"id": "a", "name": "old"})
    inv = op.inverse()
    assert inv["action"] == "edit" and inv["item"] == {"id": "a", "name": "old"}
    assert inv["restore_ids"] == ["b"]
```

- [ ] **Step 2: run, expect FAIL** (no attribute `DeleteOp`).

- [ ] **Step 3: implement op dataclasses** — `src/bw_vault_tools/plan.py`

```python
"""Typed plan operations and the dedup planner. Pure (no IO)."""
from dataclasses import dataclass, field

@dataclass
class AssignFolderOp:
    item_id: str
    folder_id: str
    folder_name: str
    destructive: bool = field(default=False, init=False)
    def inverse(self) -> dict:
        return {"action": "edit", "item_id": self.item_id, "clear_folder": True}

@dataclass
class FlagReusedOp:
    item_id: str
    note: str
    destructive: bool = field(default=False, init=False)
    def inverse(self) -> dict:
        return {"action": "noop"}            # appended note; safe to leave on undo

@dataclass
class DeleteOp:
    """Soft-delete an exact-duplicate loser. payload kept for record only."""
    item_id: str
    payload: dict
    destructive: bool = field(default=True, init=False)
    def inverse(self) -> dict:
        return {"action": "restore", "item_id": self.item_id}

@dataclass
class MergeOp:
    """Keep keep_id with unioned uris (+merged notes); soft-delete each drop_id."""
    keep_id: str
    drop_ids: list[str]
    uris: list[str]
    keep_before: dict                         # pre-edit snapshot of keep, for undo
    destructive: bool = field(default=True, init=False)
    def inverse(self) -> dict:
        return {"action": "edit", "item_id": self.keep_id, "item": self.keep_before,
                "restore_ids": list(self.drop_ids)}
```

- [ ] **Step 4: run, expect PASS** (3 tests). **Step 5: commit** `feat(plan): typed ops with inverse()`.

---

## Task 6: `plan.py` — build_dedup_plan (v2.0 all_same) + guard + real invariant

**Files:** Modify `plan.py`, `tests/test_plan.py`.

- [ ] **Step 1: failing tests** — append (cover every rule + Cornerstone 4 positive AND negative)

```python
from tests import fixtures as fx

def _plan(items, folders=None):
    return plan.build_dedup_plan(items, folders or [])

def test_exact_duplicates_produce_delete_op():
    # identical password+revision+creation -> all_same -> delete losers
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p")]
    p = _plan(items)
    dels = [o for o in p.ops if isinstance(o, plan.DeleteOp)]
    assert len(dels) == 1 and dels[0].item_id == "b"           # a kept (id tiebreak)

def test_date_or_uri_variants_produce_merge_op():
    items = [fx.login("a", uri="https://site.com", username="u", password="p", revision="2026-01-01T00:00:00.000Z"),
             fx.login("b", uri="https://app.site.com", username="u", password="p", revision="2026-02-01T00:00:00.000Z")]
    p = _plan(items)
    merges = [o for o in p.ops if isinstance(o, plan.MergeOp)]
    assert len(merges) == 1
    assert set(merges[0].uris) == {"https://site.com", "https://app.site.com"}

def test_passkey_login_preserved_and_never_destructive():
    items = [fx.passkey_login("a", username="u", password="p"),
             fx.login("b", username="u", password="p")]   # same fingerprint as the passkey one
    p = _plan(items)
    destructive_targets = set()
    for o in p.gated_ops:
        destructive_targets |= {getattr(o, "item_id", None), getattr(o, "keep_id", None)}
        destructive_targets |= set(getattr(o, "drop_ids", []))
    assert "a" not in destructive_targets
    assert "a" in p.preserved_ids and "a" in p.flagged_guard_ids
    assert len(p.gated_ops) == 0                              # singleton b -> no op either

def test_ssh_key_and_no_uri_and_unknown_preserved():
    items = [fx.ssh_key("s"), fx.no_uri_login("n"), fx.unknown_type("w"), fx.login("a")]
    p = _plan(items)
    assert {"s", "n", "w"} <= p.preserved_ids
    assert "s" in p.flagged_guard_ids

def test_reused_password_flagged_even_when_one_side_is_preserved():
    items = [fx.passkey_login("k", uri="https://x.com", username="u", password="shared"),
             fx.login("a", uri="https://y.com", username="v", password="shared")]
    p = _plan(items)
    flags = [o for o in p.ops if isinstance(o, plan.FlagReusedOp)]
    assert any(o.item_id == "a" for o in flags)               # reuse seen across full item set

def test_preservation_invariant_holds():
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p"),
             fx.no_uri_login("n"), fx.ssh_key("s"), fx.passkey_login("k", password="z")]
    p = _plan(items)
    assert p.check_invariant({"a", "b", "n", "s", "k"}) is True

def test_invariant_catches_a_silent_drop():
    p = plan.Plan()                       # empty plan: nothing removed, nothing accounted
    # input has an id that is neither removed nor preserved nor kept -> invariant must fail
    assert p.check_invariant({"ghost"}) is False
```

- [ ] **Step 2: run, expect FAIL** (no attribute `build_dedup_plan`).

- [ ] **Step 3: implement** — append to `plan.py`

```python
from . import identity, models

_REUSE_NOTE = "[bw-dedup] [!] This password is reused across multiple sites."

def reused_passwords(items: list[dict]) -> set[str]:
    """Passwords used on >1 distinct domain. Computed over the FULL item set so a
    preserved/passkey item sharing a password still triggers a flag on its twin."""
    from collections import defaultdict
    pw_domains: dict[str, set[str]] = defaultdict(set)
    for e in items:
        if not models.is_login(e):
            continue
        login = e["login"]
        pw = login.get("password")
        if not (pw and isinstance(pw, str) and pw.strip()):
            continue
        for u in (login.get("uris") or []):
            pw_domains[pw.strip()].add(identity.normalize_uri(u["uri"]))
    return {pw for pw, doms in pw_domains.items() if len(doms) > 1}

def _folder_op(item: dict, folder_ids: dict[str, str]) -> "AssignFolderOp | None":
    """Pure: assign an existing folder whose name is a substring of the username, only if
    folderId is empty. Ported from v2.0 assign_folder_id."""
    if item.get("folderId"):
        return None
    uname = (item.get("login") or {}).get("username") or ""
    for name, fid in folder_ids.items():
        if name in uname:
            return AssignFolderOp(item_id=item["id"], folder_id=fid, folder_name=name)
    return None

@dataclass
class Plan:
    ops: list = field(default_factory=list)
    preserved_ids: set = field(default_factory=set)
    kept_ids: set = field(default_factory=set)            # group winners we kept/edited
    flagged_passkey_ids: set = field(default_factory=set)
    flagged_guard_ids: set = field(default_factory=set)

    @property
    def safe_ops(self):
        return [o for o in self.ops if not o.destructive]

    @property
    def gated_ops(self):
        return [o for o in self.ops if o.destructive]

    def removed_ids(self) -> set:
        out = set()
        for o in self.ops:
            if isinstance(o, DeleteOp):
                out.add(o.item_id)
            elif isinstance(o, MergeOp):
                out.update(o.drop_ids)
        return out

    def check_invariant(self, input_ids: set) -> bool:
        """Cornerstone 4: nothing silently lost.
        (a) we only removed inputs; (b) every input is accounted for as
        removed, preserved, or a kept group-winner."""
        removed = self.removed_ids()
        accounted = self.preserved_ids | self.kept_ids | removed
        return removed <= input_ids and input_ids <= accounted

def build_dedup_plan(items: list[dict], folders: list[dict]) -> Plan:
    p = Plan()
    folder_ids = {f["name"]: f["id"] for f in folders}
    reused = reused_passwords(items)                       # full set (see reused_passwords)

    dedup_input = []
    for it in items:
        if models.is_login(it) and models.has_passkey(it):
            p.preserved_ids.add(it["id"]); p.flagged_passkey_ids.add(it["id"]); p.flagged_guard_ids.add(it["id"])
        elif models.item_type(it) == 5:
            p.preserved_ids.add(it["id"]); p.flagged_guard_ids.add(it["id"])
        elif models.is_login(it) and models.has_uris(it):
            dedup_input.append(it)
        else:                                              # non-login / no-URI login / unknown -> preserve (issue #1)
            p.preserved_ids.add(it["id"])

    for group in identity.group_logins(dedup_input).values():
        best = identity.pick_best(group, reused)
        p.kept_ids.add(best["id"])
        fo = _folder_op(best, folder_ids)
        if fo:
            p.ops.append(fo)
        losers = [e for e in group if e["id"] != best["id"]]
        if losers:
            all_same = all(
                e["login"].get("password") == best["login"].get("password")
                and e.get("revisionDate") == best.get("revisionDate")
                and e.get("creationDate") == best.get("creationDate")
                for e in group)
            if all_same:                                   # exact duplicates -> drop losers
                for e in losers:
                    p.ops.append(DeleteOp(item_id=e["id"], payload=e))
            else:                                          # variants -> merge uris+notes into best
                p.ops.append(MergeOp(keep_id=best["id"], drop_ids=[e["id"] for e in losers],
                                     uris=identity.merge_uris(group), keep_before=best))
        if (best["login"].get("password") or "") in reused:
            p.ops.append(FlagReusedOp(item_id=best["id"], note=_REUSE_NOTE))
    return p
```

- [ ] **Step 4: run, expect PASS** (all). **Step 5: commit** `feat(plan): build_dedup_plan (all_same) + guard + invariant`.

---

## Task 7: `tmpfs.py` — /dev/shm staging + shred-on-exit

**Files:** Create `src/bw_vault_tools/tmpfs.py`, `tests/test_tmpfs.py`.

- [ ] **Step 1: failing test**

```python
import os
from bw_vault_tools import tmpfs

def test_tmpfs_file_in_shm_and_shreds():
    seen = {}
    with tmpfs.tmpfs_file(suffix=".json") as path:
        assert path.startswith("/dev/shm/")
        open(path, "w").write("secret"); seen["p"] = path
        assert os.path.exists(path)
    assert not os.path.exists(seen["p"])
```

- [ ] **Step 2: run, expect FAIL.**
- [ ] **Step 3: implement**

```python
"""Plaintext-safe scratch in /dev/shm with shred-on-exit (mirrors with-creds.sh)."""
import os, subprocess, tempfile, shutil
from contextlib import contextmanager
SHM = "/dev/shm"

def _shred(path: str) -> None:
    try:
        subprocess.run(["shred", "-u", path], check=False)
    except FileNotFoundError:
        pass
    if os.path.exists(path):
        try: os.remove(path)
        except OSError: pass

@contextmanager
def tmpfs_file(suffix: str = ""):
    fd, path = tempfile.mkstemp(prefix="bwvt-", suffix=suffix, dir=SHM); os.close(fd)
    try: yield path
    finally: _shred(path)

@contextmanager
def tmpfs_dir():
    path = tempfile.mkdtemp(prefix="bwvt-", dir=SHM)
    try: yield path
    finally:
        for root, _, files in os.walk(path):
            for fn in files: _shred(os.path.join(root, fn))
        shutil.rmtree(path, ignore_errors=True)
```

- [ ] **Step 4: PASS. Step 5: commit** `feat(tmpfs): /dev/shm staging with shred-on-exit`.

---

## Task 8: `keyprovider.py` — passphrase (Tpm2 deferred to Plan 2)

**Files:** Create `src/bw_vault_tools/keyprovider.py`, `tests/test_keyprovider.py`.

- [ ] **Step 1: failing test**

```python
import pytest
from bw_vault_tools import keyprovider

def test_passphrase_roundtrip():
    kp = keyprovider.PassphraseProvider("correct horse battery staple")
    blob = kp.encrypt(b"top secret")
    assert blob != b"top secret" and kp.decrypt(blob) == b"top secret"

def test_wrong_passphrase_fails():
    blob = keyprovider.PassphraseProvider("right").encrypt(b"x")
    with pytest.raises(Exception):
        keyprovider.PassphraseProvider("wrong").decrypt(blob)

def test_tpm2_deferred():
    with pytest.raises(NotImplementedError):
        keyprovider.from_config("tpm2")
```

- [ ] **Step 2: run, expect FAIL.**
- [ ] **Step 3: implement**

```python
"""Pluggable encryption. Passphrase: scrypt -> AES-256-GCM (cryptography).
Blob: b"BWV1" | salt(16) | nonce(12) | ct. Tpm2 lands in Plan 2."""
import os
from abc import ABC, abstractmethod
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

class KeyProvider(ABC):
    @abstractmethod
    def encrypt(self, data: bytes) -> bytes: ...
    @abstractmethod
    def decrypt(self, blob: bytes) -> bytes: ...

class PassphraseProvider(KeyProvider):
    MAGIC = b"BWV1"
    def __init__(self, passphrase: str):
        self._pw = passphrase.encode("utf-8")
    def _key(self, salt: bytes) -> bytes:
        return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(self._pw)
    def encrypt(self, data: bytes) -> bytes:
        salt, nonce = os.urandom(16), os.urandom(12)
        return self.MAGIC + salt + nonce + AESGCM(self._key(salt)).encrypt(nonce, data, None)
    def decrypt(self, blob: bytes) -> bytes:
        if blob[:4] != self.MAGIC:
            raise ValueError("not a bw-vault-tools blob")
        salt, nonce, ct = blob[4:20], blob[20:32], blob[32:]
        return AESGCM(self._key(salt)).decrypt(nonce, ct, None)

def from_config(mode: str, passphrase: str | None = None) -> KeyProvider:
    if mode == "passphrase":
        if not passphrase:
            raise ValueError("passphrase mode requires a passphrase")
        return PassphraseProvider(passphrase)
    if mode == "tpm2":
        raise NotImplementedError("tpm2 provider lands in Plan 2 (bw-sync)")
    raise ValueError(f"unknown key provider mode: {mode}")
```

- [ ] **Step 4: PASS (3). Step 5: commit** `feat(keyprovider): passphrase AES-GCM+scrypt (tpm2 deferred)`.

---

## Task 9: `bw_adapter.py` — one runner factory (env+session in one place)

**Files:** Create `src/bw_vault_tools/bw_adapter.py`, `tests/test_bw_adapter.py`.

- [ ] **Step 1: failing test** (inject a fake runner; no real `bw`)

```python
import json
from bw_vault_tools import bw_adapter

class FakeRunner:
    def __init__(self): self.calls = []
    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["bw", "export"]: return json.dumps({"items": [{"id": "a"}]})
        if args[:2] == ["bw", "edit"]:   return json.dumps({"id": "a"})
        return ""

def test_export_parses_json_and_requests_json_format():
    r = FakeRunner()
    prof = bw_adapter.BwProfile("/dev/shm/A", "SESS", runner=r)
    assert prof.export()["items"][0]["id"] == "a"
    assert "--format" in r.calls[-1] and "json" in r.calls[-1]

def test_delete_soft_default_and_permanent():
    r = FakeRunner()
    prof = bw_adapter.BwProfile("/dev/shm/A", "SESS", runner=r)
    prof.delete("a");            assert r.calls[-1] == ["bw", "delete", "item", "a"]
    prof.delete("a", permanent=True); assert "--permanent" in r.calls[-1]

def test_restore_calls_bw_restore():
    r = FakeRunner(); bw_adapter.BwProfile("/dev/shm/A", "SESS", runner=r).restore("a")
    assert r.calls[-1] == ["bw", "restore", "item", "a"]

def test_default_runner_injects_env_and_session(monkeypatch):
    captured = {}
    def fake_run(cmd, capture_output, text, check, env, input=None):
        captured["cmd"], captured["env"] = cmd, env
        class R: stdout = "{}"
        return R()
    monkeypatch.setattr(bw_adapter.subprocess, "run", fake_run)
    prof = bw_adapter.BwProfile("/dev/shm/A", "SESS")     # real default runner
    prof.edit("a", {"id": "a"})
    assert captured["env"]["BITWARDENCLI_APPDATA_DIR"] == "/dev/shm/A"
    assert captured["env"]["BW_SESSION"] == "SESS"
    assert captured["cmd"][-2:] == ["--session", "SESS"]
```

- [ ] **Step 2: run, expect FAIL.**
- [ ] **Step 3: implement** — env+session injection lives ONCE, in `_make_runner`

```python
"""Drive one Bitwarden `bw` CLI profile (isolated app-data dir + session)."""
import base64, json, os, subprocess

def _make_runner(appdata_dir: str, session: str):
    """The single place env + --session are injected."""
    def run(args: list[str]) -> str:
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

    def create(self, item: dict) -> dict:
        return json.loads(self._run(["bw", "create", "item", _enc(item)]))

    def edit(self, item_id: str, item: dict) -> dict:
        return json.loads(self._run(["bw", "edit", "item", item_id, _enc(item)]))

    def delete(self, item_id: str, permanent: bool = False) -> None:
        args = ["bw", "delete", "item", item_id] + (["--permanent"] if permanent else [])
        self._run(args)

    def restore(self, item_id: str) -> None:
        self._run(["bw", "restore", "item", item_id])

def _enc(item: dict) -> str:
    return base64.b64encode(json.dumps(item).encode()).decode()
```

- [ ] **Step 4: PASS (4). Step 5: commit** `feat(bw_adapter): single-profile driver, one runner factory`.

---

## Task 10: `checkpoint.py` — encrypted baseline + typed-inverse journal + completed_ids

**Files:** Create `src/bw_vault_tools/checkpoint.py`, `tests/test_checkpoint.py`.

- [ ] **Step 1: failing test**

```python
import os
from bw_vault_tools import checkpoint, keyprovider

def kp(): return keyprovider.PassphraseProvider("pw")

def test_baseline_encrypted_and_roundtrips(tmp_path):
    run = checkpoint.RunDir(str(tmp_path), kp())
    run.write_baseline("vault", {"items": [{"id": "a"}]})
    raw = open(os.path.join(run.dir, "00-pre", "vault.json.enc"), "rb").read()
    assert b'"items"' not in raw
    assert run.read_baseline("vault")["items"][0]["id"] == "a"

def test_journal_records_completed_and_undo_is_reverse(tmp_path):
    run = checkpoint.RunDir(str(tmp_path), kp())
    run.record(item_id="b", inverse={"action": "restore", "item_id": "b"})
    run.record(item_id="a", inverse={"action": "edit", "item_id": "a", "item": {"id": "a"}})
    assert run.completed_ids == {"a", "b"}
    assert [e["action"] for e in run.undo_plan()] == ["edit", "restore"]   # reverse order

def test_completed_ids_survive_reopen(tmp_path):
    r1 = checkpoint.RunDir(str(tmp_path), kp()); r1.record("x", {"action": "noop"})
    r2 = checkpoint.RunDir(str(tmp_path), kp())                            # reopen same dir
    assert "x" in r2.completed_ids
```

- [ ] **Step 2: run, expect FAIL.**
- [ ] **Step 3: implement**

```python
"""Reversibility: encrypted baseline export + journal of typed inverse pre-images."""
import json, os

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
            n = int.from_bytes(data[i:i+4], "big"); i += 4
            out.append(json.loads(self.kp.decrypt(data[i:i+n]))); i += n
        return out

    def undo_plan(self) -> list:
        """Inverse actions in reverse order; feed each to bw_adapter to reverse the run."""
        return [e["inverse"] for e in reversed(self.read_journal())]
```

- [ ] **Step 4: PASS (3). Step 5: commit** `feat(checkpoint): baseline + typed-inverse journal + completed_ids`.

---

## Task 11: `bootstrap.py` — lazy-import guard + ensure_bw

**Files:** Create `src/bw_vault_tools/bootstrap.py`, `tests/test_bootstrap.py`.

- [ ] **Step 1: failing test** (pure version logic; the install path is exercised manually)

```python
from bw_vault_tools import bootstrap

def test_parse_bw_version():
    assert bootstrap.parse_bw_version("2026.4.2") == (2026, 4, 2)
    assert bootstrap.parse_bw_version("2026.5.0\n") == (2026, 5, 0)

def test_version_band():
    assert bootstrap.version_supported((2026, 4, 2)) is True
    assert bootstrap.version_supported((2026, 5, 9)) is True
    assert bootstrap.version_supported((2025, 12, 0)) is False
    assert bootstrap.version_supported((2027, 1, 0)) is False
```

- [ ] **Step 2: run, expect FAIL.**
- [ ] **Step 3: implement** — lazy-import guard replaces all venv/re-exec machinery

```python
"""Self-bootstrap: install the lone dependency on demand, then version-gate bw."""
import re, shutil, subprocess, sys

TESTED_MIN, TESTED_MAX = (2026, 4), (2026, 5)

def ensure_cryptography() -> None:
    """Import cryptography; if missing, pip-install it into the active interpreter and retry."""
    try:
        import cryptography  # noqa: F401
        return
    except ImportError:
        print("[bootstrap] installing cryptography...", file=sys.stderr)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "cryptography>=42,<45"])
        import cryptography  # noqa: F401,F811

def parse_bw_version(text: str) -> tuple[int, int, int]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    if not m:
        raise ValueError(f"unparseable bw version: {text!r}")
    return tuple(int(x) for x in m.groups())  # type: ignore

def version_supported(v: tuple[int, int, int]) -> bool:
    return TESTED_MIN <= (v[0], v[1]) <= TESTED_MAX

def ensure_bw() -> tuple[int, int, int]:
    if not shutil.which("bw"):
        sys.exit("bw CLI not found on PATH. Install: npm i -g @bitwarden/cli "
                 "(or the native binary from bitwarden.com/help/cli).")
    out = subprocess.run(["bw", "--version"], capture_output=True, text=True, check=True).stdout
    v = parse_bw_version(out)
    if not version_supported(v):
        print(f"[warn] bw {v[0]}.{v[1]}.{v[2]} outside tested band "
              f"{TESTED_MIN[0]}.{TESTED_MIN[1]}-{TESTED_MAX[0]}.{TESTED_MAX[1]}; proceeding.",
              file=sys.stderr)
    return v
```

- [ ] **Step 4: PASS (2). Step 5: commit** `feat(bootstrap): lazy-import guard + bw version-gate`.

---

## Task 12: `cli_dedup.py` — run_dedup (apply/approve/journal/undo) + notes-merge

**Files:** Modify `src/bw_vault_tools/cli_dedup.py`, create `tests/test_cli_dedup.py`.

- [ ] **Step 1: failing test**

```python
from bw_vault_tools import cli_dedup, keyprovider
from tests import fixtures as fx

class FakeProfile:
    def __init__(self, items): self._items=items; self.deleted=[]; self.edited=[]
    def export(self): return fx.vault(list(self._items))
    def edit(self, item_id, item): self.edited.append((item_id, item))
    def delete(self, item_id, permanent=False): self.deleted.append(item_id)
    def restore(self, item_id): pass

def approve_all(op): return True
def kp(): return keyprovider.PassphraseProvider("t")

def test_apply_dedups_and_preserves(tmp_path):
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p"),
             fx.no_uri_login("n"), fx.ssh_key("s"), fx.passkey_login("k", password="z")]
    prof = FakeProfile(items)
    res = cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), key_provider=kp())
    assert prof.deleted == ["b"] and res.applied_destructive == 1
    assert {"n", "s", "k"}.isdisjoint(prof.deleted)

def test_plan_mode_makes_no_changes(tmp_path):
    prof = FakeProfile([fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p")])
    cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), apply=False)
    assert prof.deleted == [] and prof.edited == []

def test_apply_requires_key_provider(tmp_path):
    prof = FakeProfile([fx.login("a")])
    try:
        cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), apply=True, key_provider=None)
        assert False, "expected ValueError"
    except ValueError:
        pass

def test_dedup_is_idempotent(tmp_path):
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="v", password="q",
             uri="https://other.com")]                 # no dupes
    prof = FakeProfile(items)
    res = cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), key_provider=kp())
    assert res.applied_destructive == 0 and prof.deleted == []

def test_non_tty_approver_refuses(monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli_dedup.tty_approver(object()) is False

def test_interrupted_run_skips_completed(tmp_path):
    # pre-seed journal as if 'b' was already deleted in a prior interrupted run
    run = cli_dedup.checkpoint.RunDir(str(tmp_path), kp())
    run.record("b", {"action": "restore", "item_id": "b"})
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p")]
    prof = FakeProfile(items)
    cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), key_provider=kp())
    assert "b" not in prof.deleted          # already done; not re-deleted
```

- [ ] **Step 2: run, expect FAIL.**
- [ ] **Step 3: implement** — `src/bw_vault_tools/cli_dedup.py`

```python
"""bw-dedup entrypoint: bootstrap -> export -> plan -> approve -> apply -> journal."""
import sys, argparse
from dataclasses import dataclass
from . import plan as planmod, keyprovider, checkpoint, tmpfs

@dataclass
class DedupResult:
    applied_safe: int = 0
    applied_destructive: int = 0
    preserved: int = 0

def tty_approver(op) -> bool:
    """Inline terminal gate. Non-TTY -> refuse destructive ops."""
    if not sys.stdin.isatty():
        return False
    tgt = getattr(op, "item_id", None) or getattr(op, "keep_id", None)
    return input(f"  apply {type(op).__name__} on {tgt}? [a]pprove/[s]kip: ").strip().lower() == "a"

def _merged_notes(group_items: list[dict]) -> str | None:
    notes = []
    for e in group_items:
        n = (e.get("notes") or "").strip()
        if n and n not in notes:
            notes.append(n)
    return "\n\n".join(notes) if notes else None

def _apply_safe(prof, op, by_id):
    if isinstance(op, planmod.AssignFolderOp):
        item = dict(by_id[op.item_id]); item["folderId"] = op.folder_id
        prof.edit(op.item_id, item)
    elif isinstance(op, planmod.FlagReusedOp):
        item = dict(by_id[op.item_id]); note = item.get("notes") or ""
        item["notes"] = note if op.note in note else (note + "\n\n" + op.note).strip()
        prof.edit(op.item_id, item)

def _apply_destructive(prof, op, by_id, run):
    if isinstance(op, planmod.DeleteOp):
        if op.item_id in run.completed_ids:
            return
        run.record(op.item_id, op.inverse())
        prof.delete(op.item_id)
    elif isinstance(op, planmod.MergeOp):
        keep = dict(by_id[op.keep_id]); keep["login"] = dict(keep["login"])
        keep["login"]["uris"] = [{"uri": u, "match": None} for u in op.uris]
        merged = _merged_notes([by_id[op.keep_id]] + [by_id[d] for d in op.drop_ids])
        if merged:
            keep["notes"] = merged
        if op.keep_id not in run.completed_ids:
            run.record(op.keep_id, op.inverse())
            prof.edit(op.keep_id, keep)
        for d in op.drop_ids:
            if d in run.completed_ids:
                continue
            run.record(d, {"action": "restore", "item_id": d})
            prof.delete(d)

def _validate_export(vault) -> None:
    """Guard the bw export shape (v2.1 carry). bw export is always decrypted JSON, so the
    encrypted-export case can't arise here, but a malformed/empty export must never lead to a
    destructive apply on a wrong assumption."""
    if not isinstance(vault, dict) or "items" not in vault:
        raise ValueError("bw export did not return a vault object with an 'items' key")
    if not vault.get("items"):
        print("[warn] export contains zero items — verify this is the intended vault.",
              file=sys.stderr)

def run_dedup(prof, approver=tty_approver, run_dir="/dev/shm/bwvt-run", apply=True,
              key_provider=None) -> DedupResult:
    if apply and key_provider is None:
        raise ValueError("key_provider required when apply=True (reversibility)")
    vault = prof.export()
    _validate_export(vault)
    items = vault.get("items", [])
    by_id = {it["id"]: it for it in items}
    p = planmod.build_dedup_plan(items, vault.get("folders", []))
    assert p.check_invariant(set(by_id)), "preservation invariant violated"

    res = DedupResult(preserved=len(p.preserved_ids))
    print(f"plan: {len(p.safe_ops)} safe, {len(p.gated_ops)} destructive, "
          f"{len(p.preserved_ids)} preserved ({len(p.flagged_guard_ids)} guarded).")
    if not apply:
        print("[--plan] dry-run; no changes.")
        return res

    run = checkpoint.RunDir(run_dir, key_provider)
    run.write_baseline("vault", vault)
    for op in p.safe_ops:
        _apply_safe(prof, op, by_id); res.applied_safe += 1
    for op in p.gated_ops:
        if approver(op):
            _apply_destructive(prof, op, by_id, run); res.applied_destructive += 1
    return res

def main() -> int:                       # full live wiring in Task 13
    raise SystemExit(_main())

def _main() -> int:
    return 0
```

- [ ] **Step 4: run, expect PASS** (6). **Step 5: commit** `feat(cli_dedup): run_dedup (apply/approve/journal, notes-merge, re-run safe)`.

---

## Task 13: Live wiring + manual dry-run validation

**Files:** Modify `src/bw_vault_tools/cli_dedup.py` (`main`/`_main`, `_unlock`, `undo`).

- [ ] **Step 1: implement live `main()`** (lazy-import guard first; no venv/execve)

```python
def main() -> int:
    import os, getpass
    from . import bootstrap, bw_adapter
    bootstrap.ensure_cryptography()          # self-install dep if missing
    bootstrap.ensure_bw()

    ap = argparse.ArgumentParser(prog="bw-dedup")
    ap.add_argument("--vault", required=True, help="label for output/logs")
    ap.add_argument("--appdata", required=True, help="BITWARDENCLI_APPDATA_DIR for this profile")
    ap.add_argument("--apply", action="store_true", help="apply (default: --plan dry-run)")
    ap.add_argument("--undo", metavar="RUN_DIR", help="reverse a prior run from its journal")
    args = ap.parse_args()

    session = os.environ.get("BW_SESSION") or _unlock(args.appdata)
    prof = bw_adapter.BwProfile(args.appdata, session)

    if args.undo:
        return _undo(prof, args.undo)

    kp = None
    if args.apply:
        kp = keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: "))
    with tmpfs.tmpfs_dir() as rd:
        res = run_dedup(prof, run_dir=rd, apply=args.apply, key_provider=kp)
    print(f"done: {res.applied_safe} safe, {res.applied_destructive} destructive, {res.preserved} preserved.")
    return 0

def _undo(prof, run_dir: str) -> int:
    import getpass
    run = checkpoint.RunDir(run_dir, keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: ")))
    for inv in run.undo_plan():
        a = inv["action"]
        if a == "restore":
            prof.restore(inv["item_id"])
        elif a == "edit":
            prof.edit(inv["item_id"], inv["item"])
            for rid in inv.get("restore_ids", []):
                prof.restore(rid)
        # "noop"/"clear_folder" -> nothing to reverse
    print("undo complete.")
    return 0

def _unlock(appdata: str) -> str:
    import os, subprocess, getpass
    env = dict(os.environ, BITWARDENCLI_APPDATA_DIR=appdata)
    pw = getpass.getpass("bw master password: ")
    return subprocess.run(["bw", "unlock", "--raw"], input=pw + "\n", text=True,
                          capture_output=True, check=True, env=env).stdout.strip()
```

Delete the temporary `_main()` stub from Task 12.

- [ ] **Step 2: full suite green** — `python -m pytest -v` (all tasks). **Step 3: lint** — `ruff check src tests` (clean).

- [ ] **Step 4: VALIDATION — live `--plan` against the real self-hosted vault** (Phase-5/7 gate; interactive):

```bash
cd /home/xt8664/workspace/code/platform/bw-vault-tools && pip install -e .
export BITWARDENCLI_APPDATA_DIR=~/.bw/sandulache
bw config server https://vault.sandulache.net
python -m bw_vault_tools.cli_dedup --vault sandulache --appdata ~/.bw/sandulache   # no --apply
```

Expected: a `plan: N safe, M destructive, K preserved (G guarded)` line; **no** changes; cross-check that every id in `flagged_guard_ids` is absent from gated-op targets (assert in a scratch run). Record N/M/K/G in the commit.

- [ ] **Step 5: commit** `feat(cli_dedup): live bw wiring + undo; validated --plan on self-hosted (N/M/K/G)`.

---

## Self-Review (Rev 2)

**Spec coverage:** Goal/no-purge → Tasks 9,12,13. Cornerstone 1 (gate destructive) → 12. Cornerstone 2 (passkey/SSH guard) → 6 + tests. Cornerstone 3 (reversible: baseline+journal+undo, soft-delete) → 10,12,13. Cornerstone 4 (preservation invariant) → 6 real predicate + positive/negative tests, runtime assert in `run_dedup`. Op taxonomy → 5,6. New types (5/passkey) → 1,6. tmpfs/no-plaintext → 7,13. Self-bootstrap → 11 (lazy-import). Inline-TTY + non-TTY refuse → 12 (tested). Idempotency + re-run safety → 12 (tested). ✓

**Placeholder scan:** none — `_main()` stub explicitly deleted in Task 13 Step 1.

**Type consistency:** `BwProfile(appdata_dir, session, runner=None)` consistent (9, fake in 12, real in 13). `run_dedup(prof, approver, run_dir, apply, key_provider)` consistent (12 tests, 13 caller). `RunDir(base, key_provider)` + `.record(item_id, inverse)` + `.completed_ids` + `.undo_plan()` consistent (10, 12, 13). Op attrs (`item_id/keep_id/drop_ids/uris/keep_before/payload/folder_id/note`) + `inverse()` consistent (5 defs, 12 apply, 13 undo). `MergeOp` carries `keep_before` (5) used by `inverse()` and built in 6. `fingerprint(item, include_password=True)` (3) — dedup uses default; Plan 2 will pass `False`.

---

## Next — Plan 2 (`bw-sync`)

Adds `snapshot.py` (encrypted S + ID-map), `merge.py` (3-way classification, spec §4, reusing `identity.fingerprint(include_password=False)` + `pick_best`), `Tpm2Provider`, two-profile orchestration, staged checkpoints `01..05`, and the `bw-sync undo` command. Every module here is reused unchanged.
