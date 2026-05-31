# Bitwarden two-way sync (`bw-sync`) — design

**Scope:** Build `bw-sync`, a stateful, approval-gated, fully-reversible two-way
synchroniser between two Bitwarden/Vaultwarden servers (here: the canonical self-hosted
`vault.sandulache.net` and the original `bitwarden.com` cloud account), with heavy item
overlap. It runs **unmanaged and end-to-end local** — one command drives both vaults via the
`bw` CLI, performs a true 3-way merge against a persisted last-synced snapshot, and applies
only minimal per-item deltas, **never** purge+reimport. It ships in the new `bw-vault-tools`
repo alongside `bw-dedup` (see companion spec), sharing a core library.

**Companion docs:**
- `docs/superpowers/specs/2026-05-31-bw-dedup-cli-design.md` — the single-vault dedup tool (shared core)
- `docs/superpowers/specs/2026-05-31-bw-cli-compatibility-matrix.md` — `bw` version/feature delta + "lock the old script in time"
- Upstream prior art: `no84by/bitwarden-vault-cleanup` v1.9 — dedup algorithm lifted into `identity.py`
- `CLAUDE.md` — no-plaintext-credentials cornerstone; `clients/anneberg/scripts/with-creds.sh` — TPM2→tmpfs→shred pattern mirrored here

---

## Cornerstones — operator-imposed (2026-05-31)

> **1. No destructive op without explicit approval.** A *destructive* op is any `bw delete`,
> or any `bw edit` that overwrites a field whose other-side value has diverged from the last
> sync. The tool auto-applies only **provably-no-data-loss** ops (new-item creates; one-sided
> edits where the other side still equals the snapshot). Every delete and every overwriting
> edit is gated for the operator's approval. (Mirrors `[[feedback-assistant-not-chatbot]]`:
> only irreversible actions warrant confirmation — and here every irreversible one does.)

> **2. Every run is reversible, between stages.** Before any mutation: an encrypted full
> baseline export of both vaults. The apply phase runs in escalating stages, each preceded by
> a fresh encrypted checkpoint, plus a fine-grained op journal with inverse pre-images. Soft
> delete (30-day trash) + `bw restore` is the primary reversal primitive; checkpoints and the
> journal are the belt-and-braces.

> **3. Passkeys/SSH keys are never destructively touched.** `bw export` emits an empty
> `login.fido2Credentials` array (clients#6925), so the tool cannot round-trip passkeys
> faithfully. Any item bearing `fido2Credentials` or of type 5 (SSH key) is force-classified
> non-destructive and flagged for manual handling — this overrides all merge classifications
> below. Never auto-delete or overwrite such an item.

---

## §1 — Repo strategy

New public repo **`bw-vault-tools`** with two entrypoints — `bw-sync` (this spec) and
`bw-dedup` (companion) — over a shared core. `bw-dedup` is the single-vault degenerate case of
this engine, so they share identity-matching, the bw adapter, the key provider, the safety
classifier, and the tmpfs/shred discipline. The original `no84by/bitwarden-vault-cleanup` repo
is **maintained with a v2.0 safety+compat patch** (type-5 + passkey-guard; banner → this
repo); its dedup algorithm is lifted into `identity.py` with MIT attribution (`NOTICE` +
README credit).

## §2 — Components

Python package, each module one job, independently testable:

| Module | One job | Depends on |
|---|---|---|
| `cli.py` | arg parse, orchestrate a run, render plan, collect approvals | all |
| `bootstrap.py` | self-install deps (venv + pinned `cryptography`), re-exec in venv, detect/version-gate `bw` | — |
| `bw_adapter.py` | drive two `bw` CLIs via isolated `BITWARDENCLI_APPDATA_DIR`+`BW_SESSION`; export / create / edit / delete / restore | `bw` binary |
| `identity.py` | (from old project) normalize-URI, content fingerprint, match "same logical item" across A/B/S | — |
| `merge.py` | the 3-way merge → typed delta plan | `identity`, `plan` |
| `plan.py` | typed op objects + safe-vs-destructive classification (incl. passkey/SSH guard) | — |
| `snapshot.py` | load/save encrypted `S`; per-item ID-mapping table | `keyprovider` |
| `keyprovider.py` | pluggable: `passphrase` (`cryptography` AES-256-GCM+scrypt) default, `tpm2` (`systemd-creds`) hook | — |
| `checkpoint.py` | encrypted staged snapshots under `runs/`; op journal + `undo` | `keyprovider`, `bw_adapter` |
| `tmpfs.py` | `/dev/shm` staging + `shred`-on-exit trap | — |

**Data flow:** `bootstrap` → `bw_adapter.export(A,B)` → `snapshot.load(S)` →
`merge.three_way(A,B,S)` → `plan` → render → **auto-apply safe ops; gate destructive ops** →
`checkpoint` per stage → `bw_adapter.apply` → `snapshot.save(S')`.

## §3 — Identity & the snapshot (crux of stateful sync)

Item IDs differ across A, B, and S, so correlation is explicit. `S` stores, per **logical**
item: a synthetic `link_id` (uuid) + `id_on_A` + `id_on_B` + content `fingerprint` + the full
last-synced payload (`canonical`).

- Each run: match A-items to `S` by `id_on_A`, B-items by `id_on_B` — O(1), stable.
- **First run** (empty `S`): pair A↔B by `identity.py`'s content fingerprint to collapse the
  overlap without creating duplicates. **Ambiguous pairings are gated**, never guessed.
- An item with no `S` match and no content-twin on the other side → genuine add.
- `fingerprint` = normalized-URI + username + type for logins; (name, type) for notes/cards/
  identities/SSH. Mirrors the old dedup grouping key, reused verbatim.

## §4 — 3-way merge classification

Per logical item, comparing A-now / B-now / S-last. **The passkey/SSH guard (Cornerstone 3)
is evaluated first and overrides every row.**

| Situation | Class | Proposed action |
|---|---|---|
| New on one side (no `S`) | **safe** | auto-create on the other side |
| Edited on one side, other still == `S` | **safe** | auto-push edit (other side loses nothing) |
| Edited on **both**, diverged | **destructive** → gate | newest `revisionDate` wins; graft loser's unique non-empty fields; show field diff; `approve / edit / keep-both` |
| Gone from one side, other == `S` | **destructive** → gate | propose delete on the other (soft-delete to trash; never auto) |
| Gone from one side, **edited** on other | **ambiguous** → gate | propose **keep** (edit implies intent); operator decides |
| Item bears `fido2Credentials` or type 5 | **guarded** | force non-destructive; flag for manual; overrides all above |

"Safe" = provably no data loss → auto. "Destructive" = could lose/remove data → always gated.
Applied as **minimal per-item `bw create/edit/delete`** — untouched items keep their IDs,
folders, and password history. **Never** `bw export`-then-purge-then-import.

## §5 — Security, execution, bootstrap, testing

- **Plaintext exposure:** every vault export and the decrypted `S` live only in `/dev/shm`
  tmpfs; a `shred`-on-exit trap removes them (mirrors `with-creds.sh`). `S` at rest is
  encrypted (passphrase or TPM2). No plaintext credential ever touches disk or git.
- **Two isolated sessions:** each vault is a profile — its own `BITWARDENCLI_APPDATA_DIR`
  (separate `data.json` + server config + account) and its own `BW_SESSION`. The adapter
  never lets a call cross profiles.
- **Key provider (pluggable):** default `passphrase` — key = scrypt(passphrase) (passphrase
  from prompt, env, or a file; may itself live in Bitwarden), payload AES-256-GCM via pip
  `cryptography`. Optional `tpm2` — `systemd-creds encrypt/decrypt` sealed to the host (fed
  box). One interface; `S` and all checkpoints use the selected provider.
- **Bootstrap (self-install, operator-imposed):** `bootstrap.py` ensures a repo-local `.venv`,
  pip-installs the single pinned dep (`cryptography==<pinned>`), and re-execs the CLI inside
  it; detects `bw` and **version-gates** it (warn if outside the tested `2026.4–2026.5`
  range), confirms `shred` (coreutils) and — only when `tpm2` provider is chosen —
  `systemd-creds`. Missing `bw` → actionable error (it cannot be pip-installed; print the
  npm/native install hint). No manual setup step for the user.
- **Execution = unmanaged:** `bw-sync run` with `--plan` (dry-run, default — prints the
  classified plan and exits) and `--apply` (executes with the approval gates). No daemon, no
  timer shipped. A `--yes-safe-only` flag applies just the additive/no-data-loss half
  non-interactively, for users who want to script that subset.
- **Interaction model = inline interactive terminal (TTY), like Claude Code's prompts.** Every
  approval gate renders in-place in the terminal: the field-level diff prints inline and the
  operator chooses with a single keystroke — `[a]pprove / [e]dit / [k]eep-both / [s]kip` (and
  `[A]pprove-all-of-this-class` for batches). No web UI, no external editor, no hand-editing
  JSON. `[e]dit` opens the proposed item in `$EDITOR` (or an inline field-prompt fallback).
  **Non-TTY fallback:** if stdin is not a terminal, destructive ops are **refused, never
  auto-approved** — the run applies the safe set (equivalent to `--yes-safe-only`), writes the
  full plan, and exits non-zero so a wrapper can surface "N destructive ops need a human."
- **Testing:** `identity.py` + `merge.py` are pure functions over synthetic three-vault JSON
  fixtures → every §4 row unit-tested, including a type-5 SSH-key fixture and a
  `fido2Credentials` fixture proving the guard forces non-destructive. `bw_adapter` mocked in
  unit tests; exercised once live against a throwaway pair of local vaults, then one real
  two-server `--plan`.

## §6 — Checkpoints & staged rollback (built-in)

Nothing mutates before a baseline exists; every stage is individually reversible.

```
runs/<ts>/
  00-pre/        encrypted full export of A + B           ← before ANY mutation (baseline)
  01-safe-adds/  checkpoint, then apply additive creates   (non-destructive)
  02-safe-edits/ checkpoint, then apply one-sided edits     (non-destructive)
     ── approval gate ──
  03-conflicts/  checkpoint, then apply approved merges     (destructive)
     ── approval gate ──
  04-deletes/    checkpoint, then apply approved deletes     (destructive, soft → trash)
  05-snapshot/   write new S' (old S kept as S.prev)
  journal.ndjson encrypted op log with inverse pre-images
```

- Each `NN-*` holds the encrypted A+B export as of that stage's start. Reverse stage 04 =
  re-import `04-deletes/`; reverse the whole run = re-import `00-pre/`.
- **Op journal** records each applied op with its inverse (create→delete; edit old→new→restore
  old; delete payload `P`→recreate `P`). `bw-sync undo <run> [--to-stage NN]` replays inverses
  in reverse order — finer than the stage checkpoints. Deletes use trash, so `undo` of a
  delete is a `bw restore`.
- **Snapshot continuity:** prior `S` kept as `S.prev` until the run fully succeeds → an
  interrupted/failed run never corrupts the sync baseline; it resumes or rolls back cleanly
  from `00-pre` + `journal` + `S.prev`.
- All checkpoints + journal encrypted (same provider as `S`), staged via `/dev/shm`, retained
  under `runs/` with configurable GFS-style retention.

---

## Non-goals (YAGNI)

Daemon/timer/managed service (explicitly unmanaged); organization/collection sync;
attachment-body diffing (snapshot records attachment metadata only); conflict auto-resolution
beyond the approved newest-wins-with-graft proposal; faithful passkey sync (blocked by the
upstream export gap — guarded out instead); any change to the old script beyond its shipped
v2.0 safety patch.

## Risks & assumptions

- **`revisionDate` accuracy** underpins conflict resolution; if a server clock or import skews
  it, the diff-before-apply gate is the backstop (operator sees what loses).
- **First-run fingerprint pairing** can mis-pair near-identical items; gated, not auto.
- **Vaultwarden vs bitwarden.com API parity** for `bw create/edit/delete/restore` assumed;
  validated in the live adapter test before any `--apply`.
- **Passkey/SSH export gap** is upstream and may change; the guard is conservative regardless.
