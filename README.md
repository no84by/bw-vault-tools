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
- **`bw-sync`** *(implemented)* — stateful, approval-gated, reversible **two-way sync** between
  two vaults. A true 3-way merge against a persisted, encrypted last-synced snapshot (adds +
  edits + deletes, newest-wins conflicts, passkey-guarded, journalled undo).

`bw-dedup` and `bw-sync` are **org-aware**: they read all your organizations read-only. Dedup
clears personal logins that already live in an org (orgs are never written). An optional
capacity-adaptive **sync-mirror** (replicate Vaultwarden orgs into a target org's collections,
free-tier-aware) is built as a planning core; its live org-*write* apply is gated pending a test
org. The tools are otherwise **personal-vault-scoped for writes** — they never modify org-shared
items.

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

- **`bw-dedup`**, **`bw-import`**, **`bw-sync`** — implemented and tested (91 tests).
- **Org-aware dedup** + Phase-B mirror **planning core** — implemented; the live org-write
  apply is gated pending a test org.

Designs + plans live under [`docs/design/`](docs/design/) and [`docs/plans/`](docs/plans/).

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

## Install

```bash
# from GitHub (creates the bw-dedup / bw-import / bw-sync commands)
pip install "git+https://github.com/no84by/bw-vault-tools"

# or from a clone
git clone https://github.com/no84by/bw-vault-tools && cd bw-vault-tools && pip install .
```

`cryptography` self-installs on first run if missing. You can also run without installing:
`python -m bw_vault_tools.cli_dedup ...` (likewise `cli_import`, `cli_sync`).

## Bitwarden CLI setup (one-time)

Each vault is a **profile** = an isolated `BITWARDENCLI_APPDATA_DIR` with its own login. Your
default profile already works; a second server (e.g. bitwarden.com) gets its own dir:

```bash
# default profile (e.g. your self-hosted Vaultwarden) — already set up if `bw` works
bw login            # then `bw unlock` when prompted

# a second, isolated profile (only needed for bw-sync)
export BITWARDENCLI_APPDATA_DIR=~/.bw/cloud
bw config server https://bitwarden.com
bw login            # separate account; does not touch your other profile
unset BITWARDENCLI_APPDATA_DIR
```

The tools unlock interactively (or read `BW_SESSION` from the environment if you export it).

## Usage

> **Always run `--plan` first** (the default — read-only, shows exactly what would change and
> writes nothing). Only add `--apply` once the plan looks right. Deletes are soft (30-day trash)
> and every `--apply` is journalled, so `--undo` reverses it.

### `bw-dedup` — clean one vault in place

```bash
# 1. dry-run: see the plan (no changes). --appdata is the profile's data dir.
bw-dedup --vault myvault --appdata "$HOME/.config/Bitwarden CLI"

# 2. apply: auto-applies no-data-loss ops, prompts you to approve each delete/merge.
#    Prompts once for a 'snapshot passphrase' (encrypts the undo journal).
bw-dedup --vault myvault --appdata "$HOME/.config/Bitwarden CLI" --apply

# 3. undo a run if needed (RUN_DIR is printed/located under the run; same passphrase)
bw-dedup --vault myvault --appdata "$HOME/.config/Bitwarden CLI" --undo <RUN_DIR>
```
Org-aware: it reads your orgs read-only and proposes clearing personal logins that already live
in an org (org copy is kept). It never edits or deletes org items.

### `bw-import` — pull browser passwords into the vault

```bash
# In each browser: Settings -> Export passwords -> save the CSV to ~/Downloads.
# Then:
bw-import --vault myvault --appdata "$HOME/.config/Bitwarden CLI"          # --plan: shows N new / M already present
bw-import --vault myvault --appdata "$HOME/.config/Bitwarden CLI" --apply  # bw create only the new logins
bw-import --vault myvault --appdata "$HOME/.config/Bitwarden CLI" --undo <RUN_DIR>
```
Detects installed browsers (presence only — never reads their stores), guides the export, and
creates **only** logins not already in your vault (re-running is a no-op).

### `bw-sync` — two-way sync between two vaults

```bash
# A = profile A's data dir, B = profile B's data dir, SNAPSHOT = where the encrypted
# last-synced state lives (created on first --apply).
bw-sync --appdata-a "$HOME/.config/Bitwarden CLI" --appdata-b ~/.bw/cloud \
        --snapshot ~/.bw/sync-state.enc                                   # --plan
bw-sync --appdata-a "$HOME/.config/Bitwarden CLI" --appdata-b ~/.bw/cloud \
        --snapshot ~/.bw/sync-state.enc --apply                           # apply (gated prompts)
```
First run pairs identical items and creates the divergent ones both ways. Later runs use the
snapshot for a true 3-way merge (edits + deletes + newest-wins conflicts), all gated and
reversible. **Tip:** take a `bw export` backup of both vaults before the first `--apply`.

## Credit

The deduplication algorithm originates in
[`no84by/bitwarden-vault-cleanup`](https://github.com/no84by/bitwarden-vault-cleanup) (MIT) and
lives on here in `identity.py`. See [`NOTICE`](NOTICE).

## License

MIT. See [`LICENSE`](LICENSE).
