# Org-aware Phase A — Implementation Plan (probe-lite + org dedup + file-tool fix)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bw-dedup` whole-vault aware: read every org's items as a read-only reference and **clear personal logins that are duplicated in an org** (org is authoritative), never touching org data; and fix the file tool's `exclude_org_dupes` to the same content-fingerprint rule.

**Architecture:** A light `capabilities.py` enumerates orgs and returns their items read-only (full plan/limit detection is Phase B — not needed for dedup). `plan.py` gains a `ClearPersonalDupOp` and a cross-boundary pass in `build_dedup_plan`. `cli_dedup` fetches the org reference and applies clears as gated, journalled, reversible personal-item deletes. Orgs are never written.

**Tech Stack:** Python 3.11+ stdlib + `cryptography`. `pytest`. Drives `bw` via the existing `bw_adapter`. Reuses `identity`, `models`, `checkpoint`, `keyprovider`, `tmpfs`.

**Repo:** `/home/xt8664/workspace/code/platform/bw-vault-tools` (core already merged to `main`). Branch: `feat/org-aware-dedup`. **Spec:** `docs/design/2026-06-01-org-aware-vault-tooling-design.md`. Task 5 is in the **separate** repo `bitwarden-vault-cleanup` (branch `feat/org-exclude-fix`).

---

## File Structure

```
src/bw_vault_tools/
  bw_adapter.py   + list_organizations(), list_items()        (read-only org access)
  capabilities.py NEW: org_reference_items(prof) -> [org item dicts] (read-only)
  plan.py         + ClearPersonalDupOp; build_dedup_plan(items, folders, org_reference=None)
  cli_dedup.py    run_dedup fetches org reference, applies ClearPersonalDupOp (gated delete)
tests/  test_bw_adapter.py(+)  test_capabilities.py(new)  test_plan.py(+)  test_cli_dedup.py(+)

# separate repo, Task 5:
bitwarden-vault-cleanup/bitwarden_vault_cleanup.py  exclude_org_dupes -> content fingerprint
```

`identity.fingerprint(item)` = `(normalized-uri, username, password)`, vault-agnostic, so personal↔org matching is free.

---

## Task 1: `bw_adapter` — read-only org access

**Files:** Modify `src/bw_vault_tools/bw_adapter.py`; Modify `tests/test_bw_adapter.py`.

- [ ] **Step 1: Append failing tests** to `tests/test_bw_adapter.py`

```python
def test_list_organizations_parses():
    r = FakeRunner()
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
```

- [ ] **Step 2: Run, expect FAIL**

Run: `cd /home/xt8664/workspace/code/platform/bw-vault-tools && .venv/bin/python -m pytest tests/test_bw_adapter.py -k "list_organizations or list_items" -v`
Expected: FAIL — `AttributeError: ... no attribute 'list_organizations'`.

- [ ] **Step 3: Implement** — add to `BwProfile` in `bw_adapter.py` (after `export`)

```python
    def list_organizations(self) -> list:
        return json.loads(self._run(["bw", "list", "organizations"]))

    def list_items(self) -> list:
        return json.loads(self._run(["bw", "list", "items"]))
```

- [ ] **Step 4: Run, expect PASS** — same command (2 tests).
- [ ] **Step 5: Commit**

```bash
git add src/bw_vault_tools/bw_adapter.py tests/test_bw_adapter.py
git commit -m "feat(adapter): list_organizations + list_items (read-only org access)"
```

---

## Task 2: `capabilities.py` — read-only org reference

**Files:** Create `src/bw_vault_tools/capabilities.py`, `tests/test_capabilities.py`.

- [ ] **Step 1: Write the failing test** — `tests/test_capabilities.py`

```python
from bw_vault_tools import capabilities


class FakeProfile:
    def __init__(self, items):
        self._items = items

    def list_items(self):
        return list(self._items)


def test_org_reference_items_returns_only_org_items():
    prof = FakeProfile([
        {"id": "p1", "organizationId": None, "type": 1},
        {"id": "o1", "organizationId": "org-a", "type": 1},
        {"id": "o2", "organizationId": "org-b", "type": 1},
    ])
    ref = capabilities.org_reference_items(prof)
    assert {i["id"] for i in ref} == {"o1", "o2"}


def test_org_reference_items_empty_when_no_orgs():
    prof = FakeProfile([{"id": "p1", "organizationId": None}])
    assert capabilities.org_reference_items(prof) == []
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_capabilities.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement** — `src/bw_vault_tools/capabilities.py`

```python
"""Read-only probe of the vault's organization surface.

Phase A: enumerate org items as a read-only reference for dedup. Full plan/collection-limit
detection (API / empirical / known-tier) is Phase B (sync-mirror), not needed here."""


def org_reference_items(prof) -> list:
    """All items belonging to any organization the user can access. READ-ONLY reference set —
    these items are never edited, deleted, or relocated; they only tell dedup which PERSONAL
    items are redundant."""
    return [it for it in prof.list_items() if it.get("organizationId")]
```

- [ ] **Step 4: Run, expect PASS** (2 tests).
- [ ] **Step 5: Commit**

```bash
git add src/bw_vault_tools/capabilities.py tests/test_capabilities.py
git commit -m "feat(capabilities): org_reference_items (read-only org surface)"
```

---

## Task 3: `plan.py` — `ClearPersonalDupOp` + cross-boundary pass

**Files:** Modify `src/bw_vault_tools/plan.py`, `tests/test_plan.py`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_plan.py`

```python
def test_clear_personal_dup_when_present_in_org():
    # personal login identical (uri+user+pass) to an org item -> clear the personal one
    items = [fx.login("p", uri="https://x.com", username="u", password="pw")]
    org = [fx.login("o", uri="https://x.com", username="u", password="pw")]
    p = plan.build_dedup_plan(items, [], org_reference=org)
    clears = [o for o in p.ops if isinstance(o, plan.ClearPersonalDupOp)]
    assert [c.item_id for c in clears] == ["p"]
    assert "p" in p.removed_ids()
    # the org item id never appears in any op
    assert "o" not in {getattr(o, "item_id", None) for o in p.ops}


def test_personal_not_in_org_is_untouched():
    items = [fx.login("p", uri="https://x.com", username="u", password="pw")]
    org = [fx.login("o", uri="https://other.com", username="v", password="zz")]
    p = plan.build_dedup_plan(items, [], org_reference=org)
    assert [o for o in p.ops if isinstance(o, plan.ClearPersonalDupOp)] == []


def test_org_reference_none_is_unchanged():
    items = [fx.login("a", username="u", password="p"), fx.login("b", username="u", password="p")]
    p = plan.build_dedup_plan(items, [])              # no org_reference -> old behavior
    assert [o for o in p.ops if isinstance(o, plan.ClearPersonalDupOp)] == []


def test_clear_op_inverse_is_restore():
    op = plan.ClearPersonalDupOp(item_id="p", payload={"id": "p"})
    assert op.destructive is True
    assert op.inverse() == {"action": "restore", "item_id": "p"}
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_plan.py -k "clear or org_reference" -v`
Expected: FAIL — no attribute `ClearPersonalDupOp`.

- [ ] **Step 3a: Add the op** — in `plan.py`, after `MergeOp`

```python
@dataclass
class ClearPersonalDupOp:
    """Soft-delete a PERSONAL login that is identical to an org item (org is authoritative).
    Org data is never touched; only the redundant personal copy is removed."""
    item_id: str
    payload: dict
    destructive: bool = field(default=True, init=False)

    def inverse(self) -> dict:
        return {"action": "restore", "item_id": self.item_id}
```

- [ ] **Step 3b: Count it in `removed_ids`** — extend `Plan.removed_ids`

```python
    def removed_ids(self) -> set:
        out = set()
        for o in self.ops:
            if isinstance(o, (DeleteOp, ClearPersonalDupOp)):
                out.add(o.item_id)
            elif isinstance(o, MergeOp):
                out.update(o.drop_ids)
        return out
```

- [ ] **Step 3c: Add the cross-boundary pass + the param** — change `build_dedup_plan` signature and append the pass before `return p`

```python
def build_dedup_plan(items: list[dict], folders: list[dict], org_reference: list | None = None) -> Plan:
```

…and immediately before `return p`:

```python
    # cross-boundary: clear personal logins that already exist in an org (org authoritative,
    # read-only). Applies to surviving personal logins (group winners), never to org items.
    if org_reference:
        org_fps = {identity.fingerprint(o) for o in org_reference
                   if models.is_login(o) and models.has_uris(o)}
        by_id = {it["id"]: it for it in items}
        for kid in list(p.kept_ids):
            it = by_id.get(kid)
            if it and models.is_login(it) and models.has_uris(it) and identity.fingerprint(it) in org_fps:
                p.ops.append(ClearPersonalDupOp(item_id=kid, payload=it))
                p.kept_ids.discard(kid)        # moved from kept -> removed
    return p
```

- [ ] **Step 4: Run, expect PASS** — `.venv/bin/python -m pytest tests/test_plan.py -v` (existing + 4 new). Verify the invariant tests still pass (cleared item is in `removed_ids`, so `check_invariant` accounting holds).
- [ ] **Step 5: Commit**

```bash
git add src/bw_vault_tools/plan.py tests/test_plan.py
git commit -m "feat(plan): ClearPersonalDupOp + cross-boundary clear of personal dupes in orgs"
```

---

## Task 4: `cli_dedup` — fetch org reference + apply clears

**Files:** Modify `src/bw_vault_tools/cli_dedup.py`, `tests/test_cli_dedup.py`.

- [ ] **Step 1: Update the test fake + add a test** — in `tests/test_cli_dedup.py`

Add `list_items` to the existing `FakeProfile` (so org fetch is a no-op for current tests) and append a new test:

```python
# add this method to the existing FakeProfile class:
#     def list_items(self):
#         return list(getattr(self, "_org", []))

def test_run_dedup_clears_personal_dup_in_org(tmp_path):
    items = [fx.login("p", uri="https://x.com", username="u", password="pw")]
    prof = FakeProfile(items)
    prof._org = [fx.login("o", uri="https://x.com", username="u", password="pw")]  # org has it
    res = cli_dedup.run_dedup(prof, approver=approve_all, run_dir=str(tmp_path), key_provider=kp())
    assert prof.deleted == ["p"]                 # personal dup cleared
    assert res.applied_destructive == 1
```

(Existing `FakeProfile` instances without `_org` return `[]` from `list_items`, so all prior tests are unaffected.)

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_cli_dedup.py -k clears_personal_dup -v`
Expected: FAIL — `run_dedup` doesn't fetch org reference yet (`AttributeError` on `list_items`, or no clear applied).

- [ ] **Step 3: Wire it in** — `cli_dedup.py`

(a) add the import: `from . import checkpoint, keyprovider, plan as planmod, tmpfs, capabilities`

(b) in `run_dedup`, fetch the org reference and pass it to the plan:

```python
    vault = prof.export()
    items = vault.get("items", [])
    by_id = {it["id"]: it for it in items}
    org_reference = capabilities.org_reference_items(prof) if hasattr(prof, "list_items") else []
    p = planmod.build_dedup_plan(items, vault.get("folders", []), org_reference=org_reference)
```

(c) handle the new op in `_apply_destructive` (treat exactly like `DeleteOp` — soft-delete + restore inverse):

```python
def _apply_destructive(prof, op, by_id, run):
    if isinstance(op, (planmod.DeleteOp, planmod.ClearPersonalDupOp)):
        if op.item_id in run.completed_ids:
            return
        run.record(op.item_id, op.inverse())
        prof.delete(op.item_id)
    elif isinstance(op, planmod.MergeOp):
        ...   # unchanged
```

- [ ] **Step 4: Run full suite, expect PASS** — `.venv/bin/python -m pytest -q` (all green; the new clear test + all prior).
- [ ] **Step 5: VALIDATION — live `--plan` against the real vault** (read-only; the org clear shows in the plan count):

```bash
cd /home/xt8664/workspace/code/platform/bw-vault-tools && . .venv/bin/activate
python -m bw_vault_tools.cli_dedup --vault sandulache --appdata "$HOME/.config/Bitwarden CLI"
```
Expected: plan summary now reflects any personal-dupes-in-org as destructive (gated) clears; **no writes**. Cross-check: the plan's destructive count may rise by the number of personal logins that also live in Familion / Sandulache 2.0. Record the delta in the commit.

- [ ] **Step 6: Commit**

```bash
git add src/bw_vault_tools/cli_dedup.py tests/test_cli_dedup.py
git commit -m "feat(cli_dedup): fetch org reference + apply ClearPersonalDupOp (gated, journalled); validated --plan"
```

---

## Task 5: file tool — fix `exclude_org_dupes` to content fingerprint

**Files (separate repo):** Modify `bitwarden-vault-cleanup/bitwarden_vault_cleanup.py`; add a test.

Branch: `cd /home/xt8664/workspace/code/platform/bitwarden-vault-cleanup && git checkout -b feat/org-exclude-fix`.

- [ ] **Step 1: Write the failing test** — create `tests/test_org_exclude.py`

```python
import bitwarden_vault_cleanup as bvc


def _login(id, uri="https://x.com", user="u", pw="p"):
    return {"id": id, "type": 1, "name": uri,
            "login": {"uris": [{"uri": uri, "match": None}], "username": user, "password": pw}}


def test_exclude_org_dupes_matches_by_content_not_id():
    personal = [_login("p-aaa", user="u", pw="p"),                 # same content as org item
                _login("p-bbb", uri="https://keep.com", user="v", pw="q")]
    org = [_login("o-zzz", user="u", pw="p")]                      # DIFFERENT id, same content
    kept = bvc.exclude_org_dupes(personal, org)
    kept_ids = {e["id"] for e in kept}
    assert "p-aaa" not in kept_ids        # dropped: content-identical to an org item
    assert "p-bbb" in kept_ids            # kept: not in org
```

- [ ] **Step 2: Run, expect FAIL**

Run: `cd /home/xt8664/workspace/code/platform/bitwarden-vault-cleanup && .venv/bin/python -m pytest tests/test_org_exclude.py -v`
Expected: FAIL — current `exclude_org_dupes(personal, org_ids)` matches by id, so `p-aaa` is wrongly kept.

- [ ] **Step 3: Reimplement `exclude_org_dupes`** by content fingerprint (reuse the existing `normalize_uri`)

Replace the existing `exclude_org_dupes` function with:

```python
def _content_fp(entry):
    login = entry.get("login") or {}
    uris = login.get("uris") or []
    uri = normalize_uri(uris[0]["uri"]) if uris and uris[0].get("uri") else None
    pw = login.get("password")
    return (uri, login.get("username"), pw if isinstance(pw, str) else str(pw) if pw is not None else None)


def exclude_org_dupes(personal_entries, org_items):
    """Drop personal entries whose CONTENT (uri+username+password) matches an org item.
    Matches by content fingerprint, not id (ids never match across exports)."""
    org_fps = {_content_fp(o) for o in org_items if "login" in o and (o["login"].get("uris"))}
    kept = []
    for entry in personal_entries:
        if "login" in entry and entry["login"].get("uris") and _content_fp(entry) in org_fps:
            print(f"-> Removed personal entry already in org vault: {entry.get('name')} ({entry['id']})")
        else:
            kept.append(entry)
    return kept
```

Then update its **call site** in `main()`: it currently passes `org_ids` (a set of ids). Change the call from `exclude_org_dupes(cleaned_personal, org_ids)` to `exclude_org_dupes(cleaned_personal, org_items)` (pass the org items list, not the id set). Remove the now-unused `org_ids = {...}` line if nothing else uses it.

- [ ] **Step 4: Run, expect PASS** — `.venv/bin/python -m pytest tests/test_org_exclude.py -v`; then full suite `.venv/bin/python -m pytest -q` (no regressions); `ruff check . 2>/dev/null || true`.
- [ ] **Step 5: Commit + merge**

```bash
git add bitwarden_vault_cleanup.py tests/test_org_exclude.py
git commit -m "fix(org): exclude_org_dupes matches by content fingerprint, not id"
```
(Then finish via `superpowers:finishing-a-development-branch`.)

---

## Self-Review

**Spec coverage (Phase A scope):**
- Org enumeration / read-only reference → Tasks 1–2 (`list_organizations`/`list_items`, `org_reference_items`). ✓
- Cornerstone 1 (orgs read-only; clear personal dupes; no relocate) → Task 3 `ClearPersonalDupOp` only ever targets a *personal* `item_id`; org items never enter an op (`test_clear_personal_dup_when_present_in_org` asserts org id absent). ✓
- Cornerstone 4 (reversible, gated) → Task 4 applies clears via the gated destructive path + journal restore; soft-delete (trash). ✓
- Preservation invariant intact → cleared id moves kept→removed, counted by `removed_ids`; invariant tests re-run in Task 3 Step 4. ✓
- File-tool `exclude_org_dupes` fixed to content fingerprint → Task 5. ✓
- Full plan/limit detection + Pathways + sync-mirror → **explicitly Phase B**, not in this plan (capabilities.py docstring says so). ✓

**Placeholder scan:** none. The `_apply_destructive` MergeOp branch is shown as `...   # unchanged` only to indicate the existing code stays — the engineer keeps the current body; not a placeholder for new code.

**Type consistency:** `prof.list_items()/list_organizations()` (Task 1) used by `capabilities.org_reference_items(prof)` (Task 2) and `cli_dedup.run_dedup` (Task 4). `build_dedup_plan(items, folders, org_reference=None)` (Task 3) called with `org_reference=` in Task 4. `ClearPersonalDupOp(item_id, payload)` + `.inverse()` (Task 3) handled in `_apply_destructive` (Task 4) and counted in `removed_ids` (Task 3). File-tool `exclude_org_dupes(personal_entries, org_items)` signature change + call-site update both in Task 5. Consistent.

---

## Execution note

Branch `feat/org-aware-dedup` in bw-vault-tools (Tasks 1–4); branch `feat/org-exclude-fix` in bitwarden-vault-cleanup (Task 5). Both build on already-merged code. Live `--plan` validation in Task 4 is read-only; any real `--apply` of clears follows the same backup-anchored, journalled procedure used for `bw-import`. Finish each branch via `superpowers:finishing-a-development-branch`. Phase B (Pathways UX + sync-mirror) is a later plan after `bw-sync`'s core.
