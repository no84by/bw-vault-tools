# Changelog

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
