# Changelog

## 0.4.0 — merged bw_cascade orchestration

- **Fold `bw-cascade` into `bw_vault_tools`.** The standalone `bw-cascade` package — a thin
  orchestration layer that already imported `bw_vault_tools` (backup, checkpoint, cli_dedup,
  cli_import, cli_sync, sources, bw_adapter, keyprovider) and shelled the `bw` CLI only for a
  status preflight — is now the `bw_vault_tools.cascade` / `.policy` / `.gateway` submodules. The
  weekly unattended cascade (`backup → import → dedup A → dedup B → two-way sync`) is exposed as a
  single new console script, `bw-vault-cascade = "bw_vault_tools.cascade:main"`: one package, one
  set of CLI scripts, no separate `PYTHONPATH` join. Governance unchanged — a conservative
  loss-free approver (loss-free dedup ops apply; anything destructive/ambiguous is held) backed by
  an optional reasoning-gateway digest (env-gated via `REASONING_GATEWAY_URL`, stdlib only, no
  baked-in URL) and the durable backup-before-sync gate below. The now-obsolete `bw-cascade`
  package repo is archived with a redirect to `bw-vault-tools` v0.4.0.

## 0.3.0 — durable, validated backup-before-sync gate

- **Backup-before-sync safeguard (`backup.py`).** The unattended `bw-vault-cascade` now, before it
  mutates *anything*, guarantees a validated, encrypted snapshot of **both** vaults is on durable
  store (`data_home()/bw-vault-tools/backups/`, override via `--backup-dir` / `BW_VAULT_BACKUP_DIR`).
  The gate runs **before** the import stage, so the snapshot captures the true pre-cascade state
  (a bug in a later stage cannot bake itself into the "backup"). Each stored export is
  **decrypt-round-trip validated** before it is trusted, the store keeps a `latest.json` pointer to
  the newest good copy, older copies are pruned past a retention limit (never the current one), and
  a backup newer than the last applied sync is treated as up-to-date. If a valid, up-to-date backup
  cannot be produced, the cascade **refuses to run** (exit 2, nothing touched) instead of mutating
  into the unknown.

  This is complementary to, not a replacement for, the per-run `RunDir` baseline/journal that
  `--undo` uses for reversible per-op rollback *inside* a run; it is a durable, validated
  pre-state on persistent storage. It is **not** a Bitwarden account-disaster image (that needs
  server-side restore) — see the README disclaimer.

  Security note: the on-disk index is secret-free. Fingerprints are `sha256` of the **encrypted**
  blob bytes (`sha256(file)`), an integrity marker — never of `content.content_key`, which folds in
  the raw password/totp and would otherwise be an offline password oracle.

  Mirrored in the `bw-cascade` orchestrator (see `--no-backup` to opt out — not recommended) and the
  RUNBOOK.

## 0.2.0 — contextual dedup engine + restored snapshot module

- **Contextual deduplication.** bw-dedup now adapts its grouping to the *user*, not a single rule:
  a real email identity collapses across a site's sub-domains and URL forms to its registrable
  domain, while a role/generic username (admin, support, …) stays pinned to the exact host, so
  separate tenants of the same service are never fused. Identical-content detection now folds in the
  password, TOTP seed and custom fields, so *rotated* credentials merge (losing nothing) instead of
  being deleted. Merges record deprecated previous passwords in a clearly-labelled custom field and
  contain TOTP seeds (promoting a seed to active when the survivor has none).
- **Restored `snapshot.py`.** The shipped code read and wrote the encrypted last-synced snapshot for
  bw-sync, but the module was never packaged, so `bw-sync` raised
  `ImportError: cannot import name 'snapshot'`. It now ships (fixes issue #1).
- **Secrets out of argv (PR #2).** `bw edit item` / `bw create item` take the item on stdin instead
  of argv, and failures raise a sanitized `BwError` (never argv, stdin or stdout).

Contributors: thanks to **@joarley** (issue #1) for the report that restored the snapshot module.

## 0.1.0 — first release

Four local, reversible, plan-then-apply CLI tools for Bitwarden / Vaultwarden, driven through the
`bw` CLI. Everything runs on your machine — no cloud service, no daemon, no telemetry.

- **bw-dedup** — in-place single-vault deduplicator. Content-based merge (unions URIs, notes,
  custom fields, TOTP seeds) and exact-duplicate removal. Org-aware: clears personal logins that
  already live in an organization, never writes to orgs. Passkey/SSH guarded; soft-delete to the
  30-day trash; journalled `--undo`.
- **bw-import** — additive browser-password import. Detects installed browsers by executable
  (cross-platform; never reads their stores), ingests the CSVs you export, and creates only the
  logins not already present.
- **bw-sync** — stateful, approval-gated two-way sync between two vaults. 3-way merge against a
  persisted, encrypted last-synced snapshot. Genuine divergences do a **lossless field-union**
  (notes, custom fields, URIs, TOTP) and gate only an un-mergeable password clash; conflict
  direction is configurable (`--conflict newest|a-wins|b-wins`). Org-aware; passkeys held; SSH
  keys mirrored. Every `--apply` writes an encrypted pre-mutation baseline of both vaults and a
  per-op `--undo` journal.
- **bw-totp** — import Google Authenticator seeds from an export screenshot (or a pasted
  `otpauth-migration://` URI). Match by service + username; **set-missing / skip-identical /
  never-overwrite (duplicate instead) / ask-when-ambiguous**. Journalled `--undo`.

Reversibility throughout: every `--apply` is journalled and reversible with `--undo`; deletes are
soft (restorable from the 30-day trash); plaintext scratch lives only in RAM and is shredded on
exit; the snapshot and journals are encrypted.

Cross-platform (Linux, macOS, Windows). Successor to the file-based
[bitwarden-vault-cleanup](https://github.com/no84by/bitwarden-vault-cleanup); the deduplication
algorithm lives on in `identity.py`.
