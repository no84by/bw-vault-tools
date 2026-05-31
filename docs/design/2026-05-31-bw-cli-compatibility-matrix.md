# `bw` CLI compatibility matrix — and freezing `bitwarden-vault-cleanup` in time

**Scope:** Document what changed in the Bitwarden `bw` CLI and its JSON export schema between
the era the original `no84by/bitwarden-vault-cleanup` v1.9 script was written for (~April 2025,
`bw` 2025.x) and now (`bw` 2026.5.x upstream; **2026.4.2 installed locally**), so that:

1. the original script is **brought current with a minimal safety patch** (v2.0) rather than
   frozen — it remains the simple file-based public tool, now safe against a 2026 vault, and
2. the new `bw-vault-tools` (`bw-dedup` + `bw-sync`) accounts for every schema/behaviour delta.

> **Decision change (2026-05-31):** the original plan was to *freeze* v1.9 untouched. The
> operator chose instead to ship a **v2.0 safety+compat patch** to the old repo (keep it a
> single-file, file-based tool; add type-5 labelling/pass-through + passkey detection/guard +
> a loud purge warning), so the still-used public tool is not left unsafe against 2026 vaults.
> The sections below reflect that outcome.

This doc is the source of truth for the version banner copied into the old repo and the new
repo's README. Companion to the two design specs of the same date.

---

## Version delta

| | Old script era | Current |
|---|---|---|
| `bw` CLI | ~2025.x (April 2025) | 2026.5.x upstream / **2026.4.2 local** (`/usr/local/bin/bw`) |
| Export schema | types 1–4, no passkeys, no SSH keys | types 1–**5**, `login.fido2Credentials[]`, attachment-bearing `.zip` |

## Feature / schema matrix

| Area | Old-script assumption (2025) | Current `bw` (2026.4–2026.5) | Consequence |
|---|---|---|---|
| **Item types** | 1 login · 2 note · 3 card · 4 identity | **+ 5 = SSH Key** | Old `type_labels` map (script ~line 405) has no key `5` → SSH keys fall to "Unknown Type" and are never grouped. New tools count + preserve type 5. |
| **Passkeys** | did not exist | `login.fido2Credentials[]` on logins | **Safety-critical.** `bw export --format json` emits an **empty** `fido2Credentials` array (GitHub `bitwarden/clients#6925`, closed; fix-version unconfirmed). Any purge+reimport — or destructive delta — **silently destroys passkeys**. New tools **guard** such items out of all destructive ops. |
| **Export formats** | `.json`, `.csv`, encrypted `.json` | **+ `.zip` with attachments**; `--password` encrypted JSON | Attachments now exportable; baseline reversibility snapshots can be more complete. New tools snapshot attachment metadata (not bodies — YAGNI). |
| **Delete semantics** | hard delete | `bw delete` → **30-day trash**; `bw delete --permanent` → hard; **`bw restore <id>`** | Free reversal layer. New tools soft-delete by default; `undo` of a delete is a `restore`. |
| **Commands** | create / edit / delete / get / list / export / import | same **+ `restore`** | Enables safe per-item delta application with an undo path; no purge+reimport needed. |

Sources: `bitwarden.com/help/cli`, `bitwarden.com/help/export-your-data`,
`github.com/bitwarden/clients` issue #6925 + releases (cli-v2026.x), `community.bitwarden.com`
release notes 2026.3–2026.5.

## What the old script *cannot* see (the freeze boundary)

`bitwarden-vault-cleanup` v1.9 is correct **only** for a `bw` ≤ ~2025.x export:
- It buckets item types 1–4 and labels anything else "Unknown Type" — **type-5 SSH keys are
  invisible** to its grouping and folder logic.
- It has **no concept of passkeys** — and because its intended workflow ends in
  **purge + reimport**, running it against a 2026 vault risks **passkey loss** at the reimport
  step (independent of the export bug).
- It dedups **only logins that carry a URI**; notes/cards/identities/SSH/URI-less logins pass
  through untouched (by design — but now there's a whole type it can't even label).

None of this was a bug in v1.9 — it was a tool pinned to a schema. Rather than freeze it, v2.0
brings it current with the minimum safe change.

## v2.0 safety+compat patch (shipped 2026-05-31)

Applied to `no84by/bitwarden-vault-cleanup` (commit on `main`, tag **`v2.0`**); single-file,
file-based workflow unchanged:

1. **SSH keys (type 5)** — labelled, counted, passed through untouched (were "Unknown Type").
2. **Passkey logins (`login.fido2Credentials`)** — detected via `has_passkey()`, **excluded
   from deduplication** (so a merge can never drop a passkey) and passed through untouched.
3. **Loud passkey warning** in the summary before the purge+reimport recommendation, citing
   clients#6925 — purge can lose passkeys the export never captured.
4. **`COMPATIBILITY.md`** added to the old repo; **README** gains a v2.0 banner + successor
   link. The new repo's README/NOTICE credit the old repo; the algorithm lives on as
   `identity.py` (MIT).

Validated: synthetic vault with duplicate logins + a type-5 SSH key + a passkey login carrying
a credential-twin → dup removed, and passkey + twin + SSH key all preserved in the output JSON.

## Forward compatibility posture for `bw-vault-tools`

- **Version-gate at bootstrap:** detect `bw --version`; warn (don't hard-fail) outside the
  tested `2026.4–2026.5` band, so a future `bw` that fixes the passkey export doesn't silently
  change behaviour unnoticed.
- **Schema-additive parsing:** treat unknown item types and unknown fields as opaque
  pass-through — never drop or "normalise" a field the tool doesn't recognise (the v1.9 lesson
  generalised). Unknown type ⇒ guarded non-destructive by default.
- **Re-test the passkey guard** whenever the `bw` band is bumped: if a future `bw` exports
  `fido2Credentials` faithfully, the guard can relax — but only behind a fresh fixture proving
  round-trip fidelity.
