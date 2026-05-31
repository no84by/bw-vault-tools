# `bw` CLI compatibility matrix — and freezing `bitwarden-vault-cleanup` in time

**Scope:** Document what changed in the Bitwarden `bw` CLI and its JSON export schema between
the era the original `no84by/bitwarden-vault-cleanup` v1.9 script was written for (~April 2025,
`bw` 2025.x) and now (`bw` 2026.5.x upstream; **2026.4.2 installed locally**), so that:

1. the original script is **frozen against a known-good `bw` revision** (the "lock in time"
   artifact), and
2. the new `bw-vault-tools` (`bw-dedup` + `bw-sync`) accounts for every schema/behaviour delta.

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

None of this is a bug in v1.9 — it is a tool pinned to a schema. The fix is to **freeze it**,
not patch it.

## "Lock in time" actions (the artifact)

1. **Tag** the old repo `v1.9-bw2025` at its current commit — the canonical frozen reference.
2. **Banner** at the top of the old `README.md` and a header comment in the script:
   > ⚠️ Frozen. Targets the Bitwarden `bw` export schema of ~2025.x (item types 1–4; no
   > passkeys, SSH keys, or attachments). Running against a 2026+ vault — especially the
   > purge+reimport step — can lose passkeys. Superseded by **`bw-vault-tools`** (`bw-dedup`
   > applies in-place deltas via the `bw` CLI and never purges).
3. **`COMPATIBILITY.md`** in the old repo = this matrix (the version table + "cannot see" list).
4. The new repo's README links back: "`bw-dedup` is the maintained successor to
   `bitwarden-vault-cleanup`; the algorithm lives on in `identity.py` (MIT, credited)."

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
