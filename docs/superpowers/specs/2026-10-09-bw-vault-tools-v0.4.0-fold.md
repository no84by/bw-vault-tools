# Spec — Fold `bw_cascade` into `bw_vault_tools`, v0.4.0

**Date:** 09.10.2026 · **Author:** session orchestrator · **Status:** draft → **design-advisor gate → plan**
**Repo:** `platform/bw-vault-tools` (published: GitHub `origin` + Forgejo `inrastructure`). Packaging target for the fold.

---

## 1. Why

`bw-cascade` (`personal/bw-cascade`, `pkg=bw_cascade`) is a **thin 3-module orchestration layer** that imports the `bw_vault_tools` engine and runs the weekly unattended cascade (`backup → import → dedup A → dedup B → two-way sync`) behind a loss-free approval policy + a reasoning-gateway digest. It is the *only* code in that repo (`cascade.py`, `policy.py`, `gateway.py`); it depends on `bw-vault-tools` (declared `dependencies = ["bw-vault-tools"]`) and imports 8 modules from it at runtime. The separate wrapper package + its two-path systemd `PYTHONPATH` is cruft that only exists to point at `bw_vault_tools`. The objective (operator, 09.10.2026): **fold `bw_cascade` into `bw_vault_tools`, bump to v0.4.0**, deploy on fed, re-arm the timer, first graded run.

The prior session's note ("bw_cascade is independent, shells `bw` directly") was **wrong**: `cascade.py` line 4 does `from bw_vault_tools import backup, checkpoint, cli_dedup, cli_import, cli_sync, sources` and `main()` re-imports `bw_adapter, keyprovider`. It shells `bw` for preflight only; the engine lives in `bw_vault_tools`.

## 2. Design

### 2.1 Package change (the fold)
- **Move** `bw_cascade/{cascade,policy,gateway}.py` → `bw_vault_tools/{cascade,policy,gateway}.py`.
- **Add** console-script entry point: `bw-vault-cascade = "bw_vault_tools.cascade:main"` (parallels the existing `bw-dedup / bw-sync / bw-import / bw-totp`; no name collision).
- **Collapse** the explicit-`main()` import `from bw_vault_tools import bw_adapter, keyprovider` → `from . import bw_adapter, keyprovider` (same package; relative import is correct post-fold and avoids a hard namespace dependency inside `main`).
- `cascade.py`'s `from . import gateway, policy` is a **relative import** and stays valid unchanged.
- `cascade.py` line 4 `from bw_vault_tools import backup, checkpoint, cli_dedup, cli_import, cli_sync, sources` → **`from . import backup, checkpoint, cli_dedup, cli_import, cli_sync, sources`** (relative, parity with line 6; removes the latent absolute-namespace dependency the fold would otherwise leave undocumented).
- **Drop** the `bw-cascade` package: no `bw_cascade` namespace remains. The reasoning-gateway (`gateway.py`) keeps its **env-gated** default (`REASONING_GATEWAY_URL`, stdlib `/v1/messages` client, no baked-in URL) — unchanged behaviour.

### 2.2 Version
- `pyproject.toml` `version` `0.3.0` → `0.4.0`; `src/bw_vault_tools/__init__.py` `__version__` → `0.4.0`. CHANGELOG gains a `## 0.4.0 — merged bw_cascade orchestration` entry.

### 2.3 Tests
- Port `bw_cascade/tests/{test_cascade,test_policy}.py` into `bw_vault_tools/tests/`, rewriting imports from `bw_cascade.*` → `bw_vault_tools.cascade|policy`.
- **Drop** the bw-cascade `tests/conftest.py` sys.path shim (repo-1 src injection becomes dead — the package now *is* repo-1).
- Keep the existing 23 bw-vault-tools tests green; add a regression test that `bw_vault_tools.cascade` exposes `main`/`run` and that the entry point resolves.

### 2.4 systemd (fed, system-level in `/etc/systemd/system/`)
- **Move systemd/ into the repo.** Copy `bw-cascade/systemd/{bw-vault-cascade.service,bw-vault-cascade.timer}` → `bw-vault-tools/systemd/` as the **canonical, deployed source** (bw-vault-tools currently ships no systemd/ dir; deleting the bw-cascade repo would otherwise remove the deploy source and silently fail the timer).
- Existing unit runs from **source** via `PYTHONPATH` (two paths) + `LoadCredentialEncrypted` creds (`bw_session_a/b`, `snapshot_pass`) + `REASONING_GATEWAY_URL=http://127.0.0.1:8787`; `ExecStart=... python3 -m bw_cascade.cascade ...`.
- **Merged unit**: `ExecStart=/usr/bin/bash -lc '... exec python3 -m bw_vault_tools.cascade ...'`, `PYTHONPATH=/home/xt8664/workspace/code/platform/bw-vault-tools/src` (**single** path). Config files (unit/timer) are shipped in the repo `systemd/` alongside any future units.
- Params (`--appdata-a/b --snapshot --conflict a-wins`), cred names, and host-specific strings stay **out of the shipped package** (RUNBOOK § re-seal semantics preserved).

### 2.5 Deprecations that the fold removes
- `bw-count.py` (`~/bw-count.py`, Fed-only, untracked) already imports **only from `bw_vault_tools`** (`cli_sync._org_fps`, `plan.build_dedup_plan`, `merge.three_way`, …) — it keeps working. Its `sys.path` line for the dead `bw-cascade/src` is a harmless no-op; **remove it on next edit** (not part of this fold's correctness).

## 3. What does NOT move (explicit out-of-scope for v0.4.0)
- **Ownership model:** `bw_vault_tools` stays the engine; the folded modules are its own submodules (the cascade is the composition, now inside the same package).
- **bw-run vs cascade creds** stay separate (`bw-claude-session.cred` ≠ `bw-cascade-session-*.cred`); documented, not merged.
- **Hardware commit-frozen** primary checkout discipline (see §4).

## 4. Hardware / process constraints (must hold)
- **Primary checkout is commit-frozen (layer 2 pre-commit).** The fold's package edits must happen in a **linked worktree + `mac/bw-vault-tools-v0.4.0-fold` branch** off `origin/main` at `~/workspace/claude-wt/bw-vault-tools-v0.4.0-fold`; land via `/merge-review`, then `session-worktree.sh --done`. Tests run with `pythonpath=src` (no editable install hack) as the load path.
- **Subagent-driven dev** for the mechanical moves (file move, entry point, version bump, test port, unit text). Orchestrator holds the commit + deploy.
- **LSP (pyright) + tests** run after the fold; any break routes back through `receiving-code-review`.

## 5. Validation (per-phase, runnable assertions)
- `pytest` green in the merged worktree (baseline captured pre-fold; post-fold expected 0 failures + 1+ new cascade regression).
- `python3 -c "import bw_vault_tools.cascade"` imports clean; entry point `bw-vault-cascade --help` resolves.
- Merged unit + unit `--dry-run` on fed (syntax), no diff in the `--appdata/snapshot/conflict` args.
- **Pre-enable checks:** `systemd-analyze verify systemd/bw-vault-cascade.service` on fed; live import/preflight against the real cred files (`python3 -m bw_vault_tools.cascade --help` + any `--preflight-only` the module exposes) **before** `systemctl enable --now`; residual host grep for `bw_cascade\.` (in systemd, cron, scripts) remediated to `bw_vault_tools.cascade` before enabling.
- **Energy & response time:** the cascade is a **weekly batch timer** (`OnCalendar=Sun 04:00`); this fold changes **no runtime path or cadence** and adds **no new always-on cost** (the timer already exists; config merely relocates). Budget = **None** — baseline/re-measure are therefore skipped (spec §5/Phase-7), per the "None is a valid answer" rule.
- Free of plaintext secrets; systemd unit carries no secret (only cred filenames).

## 6. Risks / unknowns (to be closed by advisor + plan)
1. Why the separate package was added in the first place — is it a deploy-time split (separate publish/CI) or was it always meant to be folded? If a deploy-time split, verify merging doesn't drop a publish path.
2. Whether fed should **pip-install** the merged package instead of PYTHONPATH-from-source (single code path; bigger change — evaluate, may defer to note).
3. Presence of a CI/publish hook that keys off the `bw-cascade` repo name.
4. Rename hygiene: `.pyc`/`__pycache__` in the moved files; ensure `mypy`/coverage pick up the new module path.

## 7. Blind spots
- **Deployment story** for fed (source-run vs pip) and whether the timer ships enabled — needs a fed walk.
- **Ownership of `bw-count.py`** (untracked) — is it meant to become a tracked test or stay a manual probe?

---

### Design-advisor verdict (APPROVED 09.10.2026 — adopt-with-changes; TIER single-Opus)
Rationale: mechanical, reversible local package fold; no wall / credential-change / send-path / money / blast-radius surface. The 5 adopted changes below are folded into §§2.1/2.4/3/5; the dep-list claim in risk #2 of the verdict was a misread (source `pyproject.toml` already lists `dependencies = ["bw-vault-tools"]`, confirmed) and is noted only to double-check on deploy.

Highest-risk surface = the systemd unit (item 1). Cheapest catch before enabling = `systemd-analyze verify` + a live `bw_vault_tools.cascade` import/preflight against the real cred files, then a residual `bw_cascade.` host grep.

Summary of adopted changes:
- **systemd/ becomes a bw-vault-tools repo dir** — copy `bw-cascade/systemd/{bw-vault-cascade.service,bw-vault-cascade.timer}` into `bw-vault-tools/systemd/` as the canonical, deployed source (bw-vault-tools currently has no systemd/ dir). Unit `ExecStart` → `python3 -m bw_vault_tools.cascade`; PYTHONPATH → single `bw-vault-tools/src`.
- **line-4 import** `from bw_vault_tools import backup, checkpoint, cli_dedup, cli_import, cli_sync, sources` → `from . import ...` for parity with line 6.
- **entry-point regression** asserts `cascade.main`/`run` callable + `--help` exits 0 (full `console_scripts` resolution only verified post-install).
- **working tree folds, not index** — bw-cascade has 2 uncommitted edits that are *security improvements*: `gateway.py` defaults to **no** baked-in gateway URL (`if not GATEWAY_URL: return None`), and `cascade.py._CRED_HINT` names only the cred **filename** (`bw-cascade-session-a.cred`), never the full host path. Fold those versions.
- **enumerate version surface** post-bump: grep `0.3.0` across the repo, confirm only pyproject + `__init__.__version__` + CHANGELOG `## 0.4.0` (inserted **above** existing 0.3.0) changed.
