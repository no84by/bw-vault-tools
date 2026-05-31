# bw-vault-tools

Two local, unmanaged command-line tools for Bitwarden / Vaultwarden vaults, driven through the
official `bw` CLI:

- **`bw-dedup`** — in-place single-vault deduplicator. Reads a live vault via `bw export`,
  computes a plan, and applies minimal per-item `bw edit`/`bw delete` deltas. Never
  purge+reimport. The advanced successor to
  [`bitwarden-vault-cleanup`](https://github.com/no84by/bitwarden-vault-cleanup) (still
  maintained as the simple file-based tool, updated to v2.0 for current `bw`).
- **`bw-sync`** — stateful, approval-gated, fully-reversible **two-way sync** between two
  vaults (e.g. a self-hosted Vaultwarden and the bitwarden.com cloud). A true 3-way merge
  against a persisted, encrypted last-synced snapshot.

Both run entirely on your machine. No cloud service, no daemon, no telemetry.

## Status

Pre-implementation. The design is complete and lives under [`docs/design/`](docs/design/):

- [`bw-twoway-sync-design.md`](docs/design/2026-05-31-bw-twoway-sync-design.md)
- [`bw-dedup-cli-design.md`](docs/design/2026-05-31-bw-dedup-cli-design.md)
- [`bw-cli-compatibility-matrix.md`](docs/design/2026-05-31-bw-cli-compatibility-matrix.md)

## Principles

- **No destructive op without explicit approval.** Deletes and overwriting edits are gated
  behind an inline terminal prompt. Only provably-no-data-loss operations apply automatically.
- **Every run is reversible.** An encrypted baseline export precedes any mutation; the apply
  phase is staged with a checkpoint before each stage and a fine-grained op journal with
  inverse pre-images (`undo`). Deletes are soft (30-day trash) and restorable.
- **No plaintext secrets at rest.** Vault exports and the working snapshot live only in
  `/dev/shm` tmpfs and are shredded on exit. The persisted snapshot is encrypted (passphrase
  by default; TPM2 on supported hosts).
- **Passkeys and SSH keys are never destructively touched.** `bw export` does not faithfully
  round-trip passkeys (`fido2Credentials`), so such items are guarded out of every destructive
  operation and flagged for manual handling.
- **Self-bootstrapping.** First run creates a local virtualenv, installs its one dependency
  (`cryptography`), and re-execs itself. It detects and version-gates `bw`. No manual setup.

## Requirements

- Python 3.11+
- [Bitwarden CLI](https://bitwarden.com/help/cli/) `bw` on `PATH` (tested against 2026.4–2026.5)
- A POSIX system with `/dev/shm` and `shred` (the TPM2 key provider additionally needs
  `systemd-creds`)

## Credit

The deduplication algorithm originates in
[`no84by/bitwarden-vault-cleanup`](https://github.com/no84by/bitwarden-vault-cleanup) (MIT) and
lives on here in `identity.py`. See [`NOTICE`](NOTICE).

## License

MIT. See [`LICENSE`](LICENSE).
