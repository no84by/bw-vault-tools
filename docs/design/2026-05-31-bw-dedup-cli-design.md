# Bitwarden single-vault dedup (`bw-dedup`) — design

**Scope:** Build `bw-dedup`, a safe, **`bw`-CLI-driven**, in-place single-vault deduplicator —
the modernised successor to `no84by/bitwarden-vault-cleanup` v1.9. It replaces that tool's
manual *web-vault export → clean JSON → purge → reimport* loop with a one-command flow that
reads a live vault via `bw export`, computes a typed plan, and applies **minimal per-item
deltas** (`bw edit`/`bw delete`) — never purge+reimport. It ships in `bw-vault-tools`,
sharing a core with `bw-sync` (see companion spec); the old dedup algorithm is the shared
`identity.py`. Runs **unmanaged and end-to-end local**, self-installing its dependencies.

**Companion docs:**
- `docs/superpowers/specs/2026-05-31-bw-twoway-sync-design.md` — the two-way sync engine (same repo + core)
- `docs/superpowers/specs/2026-05-31-bw-cli-compatibility-matrix.md` — `bw` version/feature delta; freezes the old script
- Upstream prior art: `no84by/bitwarden-vault-cleanup` v1.9 (MIT) — algorithm lifted into `identity.py`

---

## Cornerstones — operator-imposed (2026-05-31)

> **1. No destructive op without explicit approval.** Auto-apply only no-data-loss ops
> (URI-merge into a single kept item, folder assignment, reused-password note flags). Every
> `bw delete` and every overwriting `bw edit` is gated. Deletes are **soft** (30-day trash),
> reversible via `bw restore`.

> **2. Passkeys/SSH keys are never destructively touched.** `bw export` emits empty
> `fido2Credentials` (clients#6925); items bearing `fido2Credentials` or type 5 (SSH key) are
> excluded from any merge/delete and flagged for manual handling. Overrides all dedup logic.

> **3. Reversible.** Pre-run encrypted full export + op journal with inverse pre-images;
> `bw-dedup undo <run>`. Soft-delete is the primary reversal primitive.

---

## Goal & relationship to the old script

The old v1.9 script is a **pure function on a file**: it reads a manually-exported JSON, groups
logins by `(normalized-URI, username, password)`, merges app+web URI variants into the
best-scored entry (`lastUsedDate → revisionDate → password-uniqueness → creationDate`),
removes exact duplicates, assigns folders by username substring, flags reused passwords in
notes, and writes a cleaned JSON the user then purges+reimports by hand. It only understands
item types 1–4 and silently passes through everything without a URI.

`bw-dedup` keeps that core (now `identity.py`) and changes the **edges**:

| Concern | Old v1.9 | `bw-dedup` |
|---|---|---|
| Input | manual web-vault JSON export | live `bw export --format json` from an unlocked session |
| Output | cleaned JSON for manual reimport | **per-item `bw edit`/`bw delete` deltas applied in place** |
| Reimport | purge whole vault + import | never purge — preserves IDs, folders, password history |
| Item types | 1–4 only; type 5 dropped from labels | counts/preserves **type 5 (SSH key)** |
| Passkeys/SSH | unaware | detected and **guarded out** of destructive ops |
| Deletes | hard (whatever reimport yields) | **soft** (trash, 30-day) + `restore` undo |
| Safety | none (user eyeballs the JSON) | typed plan, destructive ops **gated**, fully reversible |
| Setup | manual python + place files | **self-bootstraps** deps; one command |

## Flow

```
bw-dedup --vault <profile> [--plan | --apply]
  → bootstrap: ensure venv + deps; detect/version-gate bw
  → unlock ONE session (BITWARDENCLI_APPDATA_DIR + BW_SESSION for <profile>)
  → bw export --format json  → /dev/shm (shred-on-exit)
  → identity.py: group, score, plan merges/dedup/folder/flags  → typed plan
  → guard: drop any fido2Credentials/type-5 item from destructive buckets, flag
  → render plan;  auto-apply SAFE ops;  GATE each destructive op for approval
  → apply via per-item bw edit / bw delete (soft);  write op journal
  (--plan = dry-run, default: print plan and exit)
```

## Plan op taxonomy (from `identity.py`, reused)

- **merge-URIs** (safe): collapse same-credential app+web entries → one kept entry with the
  union of URIs; loser entries soft-deleted (gated, since deletion).
- **drop-exact-duplicate** (gated): identical `(uri,username,password)` + dates → keep one,
  soft-delete rest.
- **assign-folder** (safe): set `folderId` when an existing folder name is a substring of the
  username (unchanged from old logic; only fills empty `folderId`).
- **flag-reused-password** (safe): append the reuse note to `notes` (idempotent; skips if the
  flag line is already present).
- Ambiguous groups (same `revisionDate`, differing passwords, no clear winner) → **retained,
  listed** (old tool's behaviour preserved) — never auto-merged.

## Architecture (shared core)

Reuses the `bw-vault-tools` core: `bootstrap.py`, `bw_adapter.py` (single-profile here),
`identity.py`, `plan.py` (safe-vs-destructive + passkey/SSH guard), `checkpoint.py` (pre-run
export + journal + `undo`), `tmpfs.py`. No `snapshot.py`/`merge.py`/two-profile logic — those
are sync-only. `bw-dedup` is `bw-sync`'s single-vault degenerate case minus the 3-way merge.

## Security / execution / bootstrap

Identical disciplines to `bw-sync` §5: tmpfs+shred for the export, no plaintext at rest,
self-bootstrapping deps (local `.venv` + pinned `cryptography`, re-exec; `bw` detect +
version-gate; actionable error if `bw` absent), unmanaged CLI with `--plan` default, and the
same **inline interactive-terminal (TTY) approval model** — diffs render in place, single-
keystroke `[a]pprove / [e]dit / [k]eep-both / [s]kip`, non-TTY refuses destructive ops.

## Testing

`identity.py` + plan-builder are pure functions over JSON fixtures — port the old tool's
behaviours as regression fixtures (URI-merge, exact-dup, folder-assign, reuse-flag, ambiguous-
retain) plus **new** fixtures: a type-5 SSH-key item (counted, never merged) and a
`fido2Credentials` login (guarded out of every destructive bucket). `bw_adapter` mocked for
unit tests; one live run against the real self-hosted vault in `--plan`, then a single-item
`--apply` round-tripped and `undo`-restored.

## Non-goals

Cross-server anything (that's `bw-sync`); attachment dedup; org/collection scope; any change
to the old script beyond its shipped v2.0 safety patch.

## Risks & assumptions

- `bw export` fidelity for types 1–5 (minus the known passkey gap) assumed; the guard covers
  the gap. Validated against a real export in the live `--plan` test.
- Folder-by-username-substring is a heuristic carried over from v1.9; kept identical to avoid
  surprising existing users, and it only *fills* empty `folderId` (non-destructive).
