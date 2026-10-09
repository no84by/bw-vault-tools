# Plan — Fold `bw_cascade` into `bw_vault_tools`, v0.4.0

**Date:** 09.10.2026 · **Spec:** `specs/2026-10-09-bw-vault-tools-v0.4.0-fold.md` · **Advisor:** adopt-with-changes (TIER single-Opus)
**Design phase complete; this is Phase 2 → Phase 3.** Implementation is **subagent-driven** (`local-llm` on the Mac); the orchestrator holds the commit, deploy, and dry-run gates. Read the spec + this plan before any edit.

**Design facts the plan depends on (do not re-derive):**
- `bw_cascade` = 3 modules (`cascade/gateway/policy`) that already `from bw_vault_tools import ...`; it is the orchestration layer, not the engine. Fold = move these 3 into `bw_vault_tools`, add one entry point, drop the wrapper package.
- **Fold the WORKING TREE, not the index.** bw-cascade has 2 uncommitted edits that are security improvements: `gateway.py` → `GATEWAY_URL = (os.environ.get("REASONING_GATEWAY_URL") or "").strip()` + `if not GATEWAY_URL: return None` (no baked-in localhost URL); `cascade.py._CRED_HINT` → cred **filenames only** (`bw-cascade-session-a.cred`), never the full host path.
- Entry point currently: `bw-vault-cascade = "bw_cascade.cascade:main"`. New: `bw-vault-cascade = "bw_vault_tools.cascade:main"`.
- Pre-existing entry points in bw-vault-tools: `bw-dedup / bw-sync / bw-import / bw-totp`. No name collision.
- Version is `0.3.0` in `pyproject.toml` + `__init__.py:__version__`. CHANGELOG has `## 0.3.0 — durable, validated backup-before-sync gate` at the top (do not overwrite it — insert 0.4.0 **above** it).
- bw-cascade tests live in `bw-cascade/tests/{test_cascade,test_policy}.py`; `conftest.py` injects repo-1 src into `sys.path` (dead after fold).
- systemd unit on fed is `system`-level (`/etc/systemd/system/`); shipped copies in `bw-cascade/systemd/`.

**Sequence gates:** each phase's validation block MUST pass before the next phase starts (Phase 5 `verification-before-completion`). Build test → run test → mark box. `_CRED_HINT`/gateway get **no** full-secret / full-host path in code.

---

## Phase 0 — Branch + fold the working tree (orchestrator)
- [ ] **Branch** bw-vault-tools: `git checkout -b mac/bw-vault-tools-v0.4.0-fold` off its `main`. (Session-branch isolation — never commit to main.)
- [ ] In bw-cascade, **commit the 2 working-tree security edits** onto a WIP commit (do NOT leave them uncommitted; they fold into the new modules). `git log -1` to confirm the diff is exactly the filename-only `_CRED_HINT` + no-baked-in-gateway changes.
- [ ] `git` line 6 `from . import gateway, policy` already relative — leave it.
- Validate: `git branch --show-current` = `mac/bw-vault-tools-v0.4.0-fold`; `git status --short` clean in bw-vault-tools.

## Phase 1 — Move the 3 modules into `bw_vault_tools` (local-llm, one Agent)
- [ ] Copy `bw_cascade/cascade.py` → `bw_vault_tools/cascade.py`.
- [ ] Copy `bw_cascade/policy.py` → `bw_vault_tools/policy.py`.
- [ ] Copy `bw_cascade/gateway.py` → `bw_vault_tools/gateway.py`.
- [ ] cascade.py **line 4**: `from bw_vault_tools import backup, checkpoint, cli_dedup, cli_import, cli_sync, sources` → `from . import backup, checkpoint, cli_dedup, cli_import, cli_sync, sources`.
- [ ] cascade.py **`main()`** (inside function): `from bw_vault_tools import bw_adapter, keyprovider` → `from . import bw_adapter, keyprovider`.
- Validate: `grep -n "from bw_vault_tools import" src/bw_vault_tools/cascade.py` → **empty** (both imports are now relative). `python3 -c "import ast; ast.parse(open('src/bw_vault_tools/cascade.py').read())"` → parses.

## Phase 2 — Package metadata (local-llm, one Agent)
- [ ] Add entry point to `pyproject.toml` `[project.scripts]`: `bw-vault-cascade = "bw_vault_tools.cascade:main"`.
- [ ] Confirm `dependencies` still lists `bw-vault-tools` (do not drop it).
- [ ] Bump `version = "0.4.0"` in pyproject.toml.
- [ ] Bump `src/bw_vault_tools/__init__.py` `__version__ = "0.4.0"`.
- [ ] CHANGELOG: insert `## 0.4.0 — merged bw_cascade orchestration` block **above** the existing `## 0.3.0` line; do not alter 0.3.0 content.
- [ ] Update `README.md` entry-point list (add `bw-vault-cascade`).
- Validate: `grep -rn "0\.3\.0" src pyproject.toml CHANGELOG.md` → only `CHANGELOG.md` under its existing `## 0.3.0` heading should match; pyproject + `__init__` now read `0.4.0`.

## Phase 3 — systemd/ canonical source into repo (local-llm, one Agent)
- [ ] `mkdir -p systemd` in bw-vault-tools repo root; copy `bw-cascade/systemd/{bw-vault-cascade.service,bw-vault-cascade.timer}` into `bw-vault-tools/systemd/`.
- [ ] service `ExecStart`: `python3 -m bw_cascade.cascade` → `python3 -m bw_vault_tools.cascade`.
- [ ] service `Environment=PYTHONPATH=...` → **single** path `/home/xt8664/workspace/code/platform/bw-vault-tools/src` (drop the `bw-cascade/src:` prefix). Keep `REASONING_GATEWAY_URL=http://127.0.0.1:8787`, the `BW_SESSION_*`/`BWVT_SNAPSHOT_PASSPHRASE` sourcing, and `--appdata/a,b --snapshot --conflict a-wins` unchanged.
- [ ] timer: unchanged.
- Validate: `grep -n "bw_cascade" systemd/bw-vault-cascade.service` → empty; `grep -n "bw_vault_tools.cascade" systemd/bw-vault-cascade.service` → exactly one match (ExecStart).

## Phase 4 — Tests (local-llm, one Agent)
- [ ] Port `bw-cascade/tests/{test_cascade,test_policy}.py` → `bw-vault-tools/tests/`. Rewrite `bw_cascade.cascade|policy` imports → `bw_vault_tools.cascade|policy`.
- [ ] **Delete** bw-cascade `tests/conftest.py` (repo-1 sys.path shim — dead now).
- [ ] New regression `tests/test_cascade_fold.py`: assert `bw_vault_tools.cascade.main` + `.run` are callable; `bw_vault_tools.gateway.GATEWAY_URL` unset → `reason(...)` returns None (no baked-in URL); `bw_vault_tools.cascade._CRED_HINT` values are filenames only (no `/` path).
- Validate: `python3 -m pytest tests/ -q --co` collects the new tests; no import errors on collection.

## Phase 5 — Validate (orchestrator, inline) ← **phase exit gate**
- [ ] **Baseline count:** before first mutation, `python3 -m pytest tests/ -q | tail -1` → record pass count. *(Do this before Phase 1 if strict; capture now as the committed-tree baseline and confirm post-fold matches.)*
- [ ] **Post-fold:** `python3 -m pytest tests/ -q` → all green (baseline count + new cascade tests).
- [ ] Import check: `python3 -c "import bw_vault_tools.cascade, bw_vault_tools.policy, bw_vault_tools.gateway"`.
- [ ] Entry-point smoke (post-install, optional here): `pip install -e . -q; bw-vault-cascade --help | head -3`.
- Validate: pytest line reads `passed` with **no failures**; import exits 0. If any fail → fix in-stage once, else stop and escalate (two-attempt rule).

## Phase 6 — Deploy on fed (orchestrator gate; mechanical step may be delegated local-llm)
- [ ] On fed: `cp bw-vault-tools/systemd/bw-vault-cascade.{service,timer} /etc/systemd/system/` (backup old copies first).
- [ ] `systemd-analyze verify /etc/systemd/system/bw-vault-cascade.service` → clean.
- [ ] `systemctl daemon-reload`.
- [ ] **Pre-enable live check** against real cred files: `REASONING_GATEWAY_URL=http://127.0.0.1:8787 python3 -m bw_vault_tools.cascade --help` (and `--preflight-only` if exposed) → exits 0, no `bw_cascade.` import error.
- [ ] Residual host grep: `grep -rni "bw_cascade" /etc/systemd/system /home/xt8664/.config/cron* 2>/dev/null` → remediate any `bw_cascade` ref to `bw_vault_tools.cascade`.
- [ ] `systemctl enable --now bw-vault-cascade.timer` — only after all of the above pass.
- Validate: `systemctl is-enabled bw-vault-cascade.timer` = enabled; `systemctl status bw-vault-cascade.timer -l --no-pager` shows next run.

## Phase 7 — Graded dry-run (orchestrator)
- [ ] First real cascade run (weekly `Sun 04:00` window, or force one): drive `python3 -m bw_vault_tools.cascade` against live sessions A/B if available; grade on objective markers: exit 0, backup gate enforced (`enforce_store_backup` ran), dedup ops applied per loss-free policy, held ops routed to gateway digest (or plain digest if no gateway).
- [ ] If no live sessions: at minimum `--help` + a staged `import→dedup` dry on fixture exports (document what was exercised).
- Validate: grader agent (fresh) sees exit 0 + backup gate marker + policy filter in run log; no `critical_errors`.

## Phase 8 — Propagate (orchestrator + subagents)
- [ ] GitHub: tag `v0.4.0` at the fold commit (main fast-forwarded to include it), create release `bw-vault-tools 0.4.0 — merged bw_cascade orchestration` with the CHANGELOG body (mirror the v0.3.0 pattern).
- [ ] Forgejo: tag `v0.4.0` + release (matching body) via stdin `fj_curl` — no secret in argv/env.
- [ ] **bw-cascade repo disposition:** mark Forgejo `inrastructure/bw-cascade` deprecated/archived with a redirect note to `bw-vault-tools` v0.4.0 (do not hard-delete — keep the roll-back launch path). Document in memory.
- [ ] Memory: write `[[bw-vault-tools-v0.4.0-fold]]` capturing: fold rationale, the security-improvement uncommitted edits (so future folds don't drop working-tree state), and the systemd→repo canonical source.
- Validate: both release objects exist with `v0.4.0`; memory entry exists; archived repo link resolves.

---

### Receive gate (after Phase 3 hand-backs)
Before Accept on Phase 1/2/3/4 hand-backs that changed files: orchestrator runs `code-review` (bugs) + `simplify` (reuse) at mid tier; findings return to the same executor. Hand-backs with no diff skip the gate.

### Stopped/deferred
- Whether fed should **pip-install** the merged package vs `PYTHONPATH`-from-source — a follow-up (defer; from-source keeps the cred-sourcing model unchanged for v0.4.0).
- `bw-count.py` stale `bw-cascade/src` sys.path line — remove on next edit (untracked, Fed-only, out of this fold's correctness).
