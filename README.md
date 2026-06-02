# bw-vault-tools

Local, unmanaged command-line tools for Bitwarden / Vaultwarden vaults, driven through the
official `bw` CLI:

- **`bw-dedup`** — in-place single-vault deduplicator. Reads a live vault via
  `bw export`, computes a plan, and applies minimal per-item `bw edit`/`bw delete` deltas. Never
  purge+reimport. The advanced successor to
  [`bitwarden-vault-cleanup`](https://github.com/no84by/bitwarden-vault-cleanup) (still
  maintained as the simple file-based tool).
- **`bw-import`** — aggregate passwords from your installed browsers into the
  live vault. Detects browsers (presence-only — never reads their stores), guides each browser's
  own CSV export, and `bw create`s only the logins not already present (additive-only,
  plan-then-approve, journalled undo).
- **`bw-sync`** — stateful, approval-gated, reversible **two-way sync** between
  two vaults. A true 3-way merge against a persisted, encrypted last-synced snapshot (adds +
  edits + lossless field-union merges + deletes, configurable conflict policy, passkey-guarded,
  journalled undo).
- **`bw-totp`** — import **Google Authenticator** TOTP seeds into a vault. Decode
  an export screenshot (or paste the `otpauth-migration://` URI), match each account to a login,
  and set the seed where missing — skipping identical ones, never overwriting a different one
  (duplicates instead), surfacing ambiguous matches for you to resolve. Journalled undo.

`bw-dedup` and `bw-sync` are **org-aware**: they read all your organizations read-only. Dedup
clears personal logins that already live in an org (orgs are never written). An optional
capacity-adaptive **sync-mirror** (replicate Vaultwarden orgs into a target org's collections,
free-tier-aware) is built as a planning core; its live org-*write* apply is gated pending a test
org. The tools are otherwise **personal-vault-scoped for writes** — they never modify org-shared
items.

Together with [`bitwarden-vault-cleanup`](https://github.com/no84by/bitwarden-vault-cleanup),
these form one family: **Manual** (the file-based cleaner), **CLI** (`bw-dedup`/`bw-import`/
`bw-totp`), **Auto** (`bw-sync`). Same dedup core (`identity.py`). All run entirely on your
machine — no cloud service, no daemon, no telemetry.

## Which tool should I use?

- **New here, or want the simplest possible thing?** Use
  **[bitwarden-vault-cleanup](https://github.com/no84by/bitwarden-vault-cleanup)** — a single
  download-and-run Python file, no install and no CLI. It cleans/deduplicates an *exported*
  vault JSON for you to re-import. That is the entry-level tool for everyone.
- **Comfortable in a terminal and want more?** `bw-vault-tools` (this repo) is the advanced
  option: it drives the `bw` CLI directly for **in-place** delta dedup (no purge+reimport) and
  reversible **two-way sync** between two vaults. Same dedup algorithm at the core
  (`identity.py`, credited to the entry-level tool), more power and more setup.

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

Runs on **Linux, macOS, and Windows**.

- Python 3.11+
- [Bitwarden CLI](https://bitwarden.com/help/cli/) `bw` on `PATH` (tested against 2026.4–2026.5).
  On Windows use the **native `bw.exe`** (not the npm `bw.cmd` shim).
- Scratch/secrets are kept in RAM (`/dev/shm`) on Linux and in the OS temp dir on macOS/Windows,
  shredded on exit. No POSIX-only requirement.
- **`bw-totp` only**, to decode an export *image*: install the extra — `pip install
  "bw-vault-tools[totp]"` (cross-platform; bundles zbar on Windows, uses system `libzbar` on
  Linux/macOS — `dnf/apt/brew install zbar`). Or skip it entirely and pass the
  `otpauth-migration://` text via `--uri` (no decoder needed, works on every OS).

## Install

```bash
pip install "git+https://github.com/no84by/bw-vault-tools"   # creates bw-dedup / bw-import / bw-sync
```
(On Windows, run the same in PowerShell.) `cryptography` self-installs on first run if missing.
No-install alternative: `python -m bw_vault_tools.cli_dedup ...` (likewise `cli_import`, `cli_sync`).

## Bitwarden CLI setup (one-time)

Each vault is a **profile** = an isolated data directory (`BITWARDENCLI_APPDATA_DIR`) with its own
login. Your default profile already works once `bw login` + `bw unlock` succeed. The profile data
directory differs by OS — you pass it as `--appdata`:

| OS | Default `bw` profile directory |
|---|---|
| Linux | `~/.config/Bitwarden CLI` |
| macOS | `~/Library/Application Support/Bitwarden CLI` |
| Windows | `%APPDATA%\Bitwarden CLI` (i.e. `C:\Users\<you>\AppData\Roaming\Bitwarden CLI`) |

A **second** profile (only needed for `bw-sync`) is just a different directory:

```bash
# Linux / macOS (bash/zsh)
export BITWARDENCLI_APPDATA_DIR=~/.bw/cloud
bw config server https://bitwarden.com && bw login
unset BITWARDENCLI_APPDATA_DIR
```
```powershell
# Windows (PowerShell)
$env:BITWARDENCLI_APPDATA_DIR = "$env:USERPROFILE\.bw\cloud"
bw config server https://bitwarden.com ; bw login
Remove-Item Env:\BITWARDENCLI_APPDATA_DIR
```

The tools unlock interactively (or read `BW_SESSION` from the environment if you export it).

## Usage

> **Always run `--plan` first** (the default — read-only, shows exactly what would change and
> writes nothing). Only add `--apply` once the plan looks right. Deletes are soft (30-day trash)
> and every `--apply` is journalled, so `--undo` reverses it.

In the commands below, replace `<PROFILE_DIR>` with your profile directory from the table above
(quote it — the default path contains a space). The commands are identical on Linux, macOS, and
Windows; only the directory string differs.

### `bw-dedup` — clean one vault in place

```bash
bw-dedup --vault myvault --appdata "<PROFILE_DIR>"            # 1. plan (read-only)
bw-dedup --vault myvault --appdata "<PROFILE_DIR>" --apply    # 2. apply (auto-safe ops; prompts to approve each delete/merge; asks a snapshot passphrase for the undo journal)
bw-dedup --vault myvault --appdata "<PROFILE_DIR>" --undo <RUN_DIR>   # 3. reverse a run
```
Org-aware: reads your orgs read-only and proposes clearing personal logins that already live in
an org (the org copy is kept). It never edits or deletes org items.

### `bw-import` — pull browser passwords into the vault

```bash
# First, in each browser: Settings -> Export passwords -> save the CSV to your Downloads folder.
bw-import --vault myvault --appdata "<PROFILE_DIR>"           # plan: N new / M already present
bw-import --vault myvault --appdata "<PROFILE_DIR>" --apply   # bw create only the new logins
bw-import --vault myvault --appdata "<PROFILE_DIR>" --undo <RUN_DIR>
```
Detects installed browsers (presence only — never reads their stores), guides the export, and
creates **only** logins not already in your vault (re-running is a no-op).

### `bw-sync` — two-way sync between two vaults

```bash
# <DIR_A> / <DIR_B> are the two profile directories; --snapshot is where the encrypted
# last-synced state lives (created on first --apply). Use a path under your home dir.
bw-sync --appdata-a "<DIR_A>" --appdata-b "<DIR_B>" --snapshot "<SNAPSHOT_PATH>"           # plan
bw-sync --appdata-a "<DIR_A>" --appdata-b "<DIR_B>" --snapshot "<SNAPSHOT_PATH>" --apply   # apply
```
First run pairs identical items and creates the divergent ones both ways. Later runs use the
snapshot for a true 3-way merge (one-sided edits + lossless field-union merges + deletes), all
gated and reversible. A genuine divergence unions notes/custom fields/URIs/TOTP from both sides
and gates only an un-mergeable password clash. Conflict direction is configurable with
`--conflict newest|a-wins|b-wins` (default `newest`); `a-wins`/`b-wins` make one vault canonical.
Every `--apply` automatically writes an encrypted pre-mutation baseline export of **both** vaults
before touching anything, on top of the per-op `--undo` journal — no manual backup step required.

### `bw-totp` — import Google Authenticator seeds

```bash
# In Google Authenticator: Transfer accounts -> Export accounts -> screenshot each QR.
bw-totp --vault myvault --appdata "<PROFILE_DIR>" --image qr1.png --image qr2.png          # plan
bw-totp --vault myvault --appdata "<PROFILE_DIR>" --image qr1.png --apply                  # apply
bw-totp --vault myvault --appdata "<PROFILE_DIR>" --uri 'otpauth-migration://offline?data=...'  # no decoder
bw-totp --vault myvault --appdata "<PROFILE_DIR>" --undo <RUN_DIR>
```
Matches each account to a login by service + username. **Sets** the seed only where one is
missing, **skips** identical ones, **never overwrites** a different seed (creates a duplicate
instead), and **asks** (interactively) for anything ambiguous rather than guessing. Pure decode +
match; the seeds never leave your machine.

## Credit

The deduplication algorithm originates in
[`no84by/bitwarden-vault-cleanup`](https://github.com/no84by/bitwarden-vault-cleanup) (MIT) and
lives on here in `identity.py`. The Google Authenticator export decoder parses the
`otpauth-migration` `MigrationPayload` protobuf
([google-authenticator-android](https://github.com/google/google-authenticator-android),
Apache-2.0; format per the community reverse-engineering, notably Alexander Bakker's writeup), and
decodes the QR via [zbar](https://github.com/mchehab/zbar) /
[pyzbar](https://github.com/NaturalHistoryMuseum/pyzbar). See [`NOTICE`](NOTICE).

## Disclaimer

These tools modify a live password vault. They are provided **AS IS**, with **no support**, **no
guarantees**, and **no warranty** (see [`LICENSE`](LICENSE)). **You use them at your own risk.**

Although every `--apply` is plan-first, approval-gated, journalled, and reversible (soft-delete
trash + `--undo` + an encrypted pre-mutation baseline of each vault), you remain responsible for:

- Taking your own `bw export` backup of every vault before the first `--apply`.
- Reviewing the `--plan` output before approving any change.
- Verifying your vault(s) behave as expected afterwards.

Always test with non-critical data if you're unsure. When in doubt, reverse the run with `--undo`
or restore items from the 30-day trash.

## License

This project is licensed under the [MIT License](LICENSE).

You are free to:
- Use this script in personal or commercial projects
- Modify and redistribute it
- Adapt it to your needs

The license also includes a liability disclaimer:
**You use this script at your own risk.**
