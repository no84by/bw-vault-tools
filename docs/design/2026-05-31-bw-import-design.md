# bw-import — aggregate browser passwords into the live vault — design

**Scope:** Add `bw-import`, a third entrypoint in `bw-vault-tools` (alongside `bw-dedup` and
`bw-sync`) that imports passwords from locally-installed browsers into the user's **live**
Bitwarden/Vaultwarden vault — without creating duplicates of logins already present, and
reversibly. It is the live-vault counterpart of `bitwarden-vault-cleanup`'s `--aggregate`
feature: the entry-level tool can only merge export files into a JSON for manual re-import;
the CLI tool talks to the vault via `bw`, so it can dedup against what is already there and
create only the genuinely-new logins.

This is the **CLI/auto** branch of the family (`bitwarden-vault-cleanup` = manual). It reuses
the core already built for `bw-dedup`: `bw_adapter`, `checkpoint`, `identity`, `keyprovider`,
`tmpfs`, `bootstrap`. The browser-discovery/CSV-ingest code is **ported** (not shared) from the
entry-level tool, with the v2.1 + review hardening already baked in.

## Cornerstones (non-negotiable)

> **1. Additive-only — never edit or delete an existing vault item.** `bw-import` only ever
> `bw create`s new logins. It never modifies or removes anything already in the vault. The
> worst case is an extra login appears (recoverable by undo); there is no data-loss path. This
> is the strongest safety guarantee and the reason import is the safest application of the
> aggregation idea.

> **2. Presence-only browser detection; ingest only user-exported files.** Same as the
> entry-level tool: detect installed browsers by profile-directory existence only; never open,
> read, or decrypt any browser credential store. Ingest only the CSVs the user exported via
> each browser's own exporter. (The store-reading path was assessed and rejected 2026-05-31.)

> **3. Dedup against the live vault, verbatim algorithm.** A candidate browser login is created
> only if its `identity.fingerprint` (`normalized-uri + username + password`) is **not** already
> present in the live vault and not a duplicate of another candidate. Same login already there →
> skipped. Different password for a known site → a genuinely new value → created (never an
> overwrite — Cornerstone 1). No new conflict policy; the existing fingerprint is the policy.

> **4. Plan-then-approve, journalled, reversible.** `--plan` (default) shows what would be
> created + skip counts and writes nothing. `--apply` shows the plan, confirms, then `bw create`s
> each new item, recording each in the encrypted journal. `bw-import undo <run>` deletes exactly
> the created items. Idempotent: re-running skips everything now present.

## User flow

```
bw-import --vault <profile> --appdata <dir> [--plan | --apply]
  bootstrap (lazy cryptography) + ensure_bw (version-gate)
  unlock the profile (BW_SESSION)
  detect_browsers()                 presence-only
  scan_for_exports(Downloads, CWD)  content-sniff existing CSVs
  offer + guided export loop        collect_source per installed browser (watch Downloads)
  csv_to_items(...)                 CSVs -> candidate bw login items (fresh uuid each)
  bw export (live vault)            -> existing items
  build import plan:
     live_fp = { fingerprint(i) for i in live items }
     for each candidate (deduped among themselves):
        fingerprint in live_fp -> SKIP (already in vault)
        else                   -> CreateOp(item)
  --plan : print "N new logins to create, M already present (skipped)" + the new names; exit
  --apply: render plan -> confirm -> for each CreateOp: journal then bw create
           print created count; `bw-import undo <run>` available
```

If the user declines aggregation, or stdin is non-interactive, the guided export loop is
skipped and only already-present export files are considered (and on non-TTY, `--apply` refuses
just like `bw-dedup`).

## Components

Reuses the existing package; two new modules + one new entrypoint:

| Module | Responsibility | New / reused |
|---|---|---|
| `sources.py` | `detect_browsers` (presence-only), `scan_for_exports`, `classify_export` (utf-8-sig, large-JSON-safe), `csv_to_items` (uuid id, null-safe), `export_instructions`, `collect_source` (guided wait-for-export, injectable watch/ask/now) | **new** (ported from cleanup tool, hardened) |
| `import_plan.py` | `CreateOp` + `build_import_plan(candidates, live_items)` → list of CreateOps + skip count; dedup candidates against live via `identity.fingerprint`; passkey/SSH guard (browser CSVs carry neither, but guard defensively) | **new** (pure) |
| `cli_import.py` | `run_import(prof, sources_io, approver, run_dir, apply, key_provider)` + `main()` wiring | **new** |
| `identity.py` | `fingerprint` for the already-present check | reused |
| `bw_adapter.py` | `export` (live items), `create` (new items), `delete` (undo) | reused |
| `checkpoint.py` | journal each create (inverse = delete created id); `undo_plan` | reused |
| `keyprovider.py` / `tmpfs.py` / `bootstrap.py` | encryption / shred-staging / self-install + version-gate | reused |

`sources.py` is a port, not a shared library — the two repos stay independent (mirrors how the
dedup algorithm was credited-and-copied, not shared).

## Data flow & the create op

`CreateOp(item)` is **non-destructive** (`destructive=False`), so under the tool's safe/gated
model it is a *safe* op — but because it writes to the vault, `--apply` still renders the full
plan and takes one confirmation before creating (it is not silently auto-applied). Each create:
- pre-write: journal `{action: "delete", item_id: <the uuid we assigned>}` as the inverse.
  Note `bw create` returns the SERVER-assigned id; the journal records the returned id so undo
  deletes the right item.
- `bw create` the item; capture the returned id; update the journal entry's `item_id`.
`bw-import undo <run>` reads the journal and `bw delete`s each created id (soft-delete → trash).

## Error handling

- Malformed/unreadable CSV → report and skip that source (never abort the run).
- `bw create` failure on one item → log, continue with the rest; the journal reflects only what
  actually succeeded (id captured post-create), so undo stays accurate.
- Non-interactive stdin → no guided export loop; `--apply` refused (consistent with `bw-dedup`).
- Empty candidate set or all-already-present → "nothing new to import", exit 0.

## Testing

- `sources.py`: pure-function tests over CSV fixtures (chromium/firefox/safari + BOM + unknown);
  `detect_browsers` against a temp home tree; `collect_source` with injected watch/ask/now.
  (Ported tests from the cleanup tool, adapted.)
- `import_plan.build_import_plan`: pure tests — candidate already in live vault → skipped;
  new candidate → CreateOp; candidate with same site/username but different password → created
  (not skipped, never overwrites); duplicate candidates among themselves collapse.
- `cli_import.run_import`: injected fake `BwProfile` (export returns canned live items, records
  `create`/`delete` calls) + fake sources; assert only-new items created, journal records the
  returned ids, `--plan` writes nothing, non-TTY refuses `--apply`, undo deletes created ids.
- One live manual run against a **test** vault: export → import a small browser CSV → verify
  only-new created, re-run is a no-op, `undo` removes them.

## Non-goals

Reading/decrypting browser stores (rejected). Editing or deleting existing vault items (import
is additive-only). Two-vault sync (that's `bw-sync`). A `rich` UI (entry-level tool's flavor;
`bw-import` uses the inline-TTY style of `bw-dedup`). Pushing to a file (items go straight into
the vault).

## Sequencing

`bw-import` is its own plan (Plan 3) and depends on the core finished in Plan 1 (`bw_adapter`,
`checkpoint`, `identity`, `keyprovider`, `tmpfs`, `bootstrap` — built in Plan-1 Tasks 1–11;
`bw-dedup`'s CLI wiring is Tasks 12–13, still pending). Implement `bw-import` **after** Plan 1's
core is wrapped. `bw-sync` (Plan 2) is independent and can precede or follow.

## Risks & assumptions

- **`bw create` returns the new item's id** in its JSON — relied on for accurate undo. Validated
  in the live adapter test before any `--apply`.
- **Fingerprint match for "already present"** uses `(uri, username, password)`; a vault login
  whose password was rotated since the browser saved it will be created as a separate entry
  (correct — never overwrites; the user reconciles, same as dedup).
- Browser CSV schemas may drift; classification by header signature + skip-unknown contains it.
