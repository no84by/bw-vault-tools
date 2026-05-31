# bw-vault-tools

Local, unmanaged command-line tools for Bitwarden / Vaultwarden vaults, driven through the
official `bw` CLI:

- **`bw-dedup`** *(implemented)* — in-place single-vault deduplicator. Reads a live vault via
  `bw export`, computes a plan, and applies minimal per-item `bw edit`/`bw delete` deltas. Never
  purge+reimport. The advanced successor to
  [`bitwarden-vault-cleanup`](https://github.com/no84by/bitwarden-vault-cleanup) (still
  maintained as the simple file-based tool).
- **`bw-import`** *(implemented)* — aggregate passwords from your installed browsers into the
  live vault. Detects browsers (presence-only — never reads their stores), guides each browser's
  own CSV export, and `bw create`s only the logins not already present (additive-only,
  plan-then-approve, journalled undo).
- **`bw-sync`** *(designed, not yet built)* — stateful, approval-gated, reversible **two-way
  sync** between two vaults. A true 3-way merge against a persisted, encrypted last-synced
  snapshot.

Together with [`bitwarden-vault-cleanup`](https://github.com/no84by/bitwarden-vault-cleanup),
these form one family at three automation levels: **Manual** (the file-based cleaner), **CLI**
(`bw-dedup`/`bw-import`), **Auto** (`bw-sync`). Same dedup core (`identity.py`). All run entirely
on your machine — no cloud service, no daemon, no telemetry.

## Which tool should I use?

- **New here, or want the simplest possible thing?** Use
  **[bitwarden-vault-cleanup](https://github.com/no84by/bitwarden-vault-cleanup)** — a single
  download-and-run Python file, no install and no CLI. It cleans/deduplicates an *exported*
  vault JSON for you to re-import. That is the entry-level tool for everyone.
- **Comfortable in a terminal and want more?** `bw-vault-tools` (this repo) is the advanced
  option: it drives the `bw` CLI directly for **in-place** delta dedup (no purge+reimport) and
  reversible **two-way sync** between two vaults. Same dedup algorithm at the core
  (`identity.py`, credited to the entry-level tool), more power and more setup.

## Status

- **`bw-dedup`** and **`bw-import`** — implemented and tested (58 tests).
- **`bw-sync`** — designed; implementation pending.

Designs + plans live under [`docs/design/`](docs/design/) and [`docs/plans/`](docs/plans/):

- [`bw-dedup-cli-design.md`](docs/design/2026-05-31-bw-dedup-cli-design.md)
- [`bw-import-design.md`](docs/design/2026-05-31-bw-import-design.md)
- [`bw-twoway-sync-design.md`](docs/design/2026-05-31-bw-twoway-sync-design.md)
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
