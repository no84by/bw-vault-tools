# Org-aware vault tooling — design

**Scope:** Make `bw-vault-tools` operate on the user's **whole** vault surface — personal vault
**plus every organization** they can access — not just the personal vault. This is one cohesive
capability shared across the tools, built from four parts: a **CapabilityProbe** (detect each
vault's server type + plan/collection limits, all tiers), a **Report + Pathways** UX layer
(surface findings, let the user choose a strategy interactively or via config), **org-aware
dedup** (orgs are read-only reference; clear personal items duplicated in an org), and
**org-aware sync-mirror** (replicate Vaultwarden orgs into a target org's collections,
adapting to detected capacity). Companion: fix `bitwarden-vault-cleanup`'s `exclude_org_dupes`
to the same content-fingerprint rule.

Background: the tools previously read only `bw export` (personal vault), silently excluding org
items (the 1251-vs-1284 gap = 33 org items in two orgs the user owns: **Familion**,
**Sandulache 2.0**). Org handling is core to the original promise (clean+sync the whole vault),
so it is in-scope everywhere it applies.

## Cornerstones (operator-imposed)

> **1. Orgs are read-only for dedup; never relocate vault data.** Dedup never edits, deletes,
> or moves an org item, and never moves items between vaults. Org items exist only as a
> *reference set*. The sole cross-boundary action is **clearing a personal item that is
> identical to an org item** (the org is authoritative; the personal duplicate is removed).

> **2. No login is ever lost.** Items/logins are unlimited on every Bitwarden tier, so
> aggregation/mirroring never drops a credential. Under a collection *cap*, only collection
> *structure* flattens — never the items themselves.

> **3. The user decides the trade-offs (programmable pathways).** When detected capacity can't
> hold the source structure 1:1, the tool reports the gap and offers concrete pathways; it
> never silently consolidates or drops. The choice is made interactively (inline-TTY / rich) or
> pre-set via flag/config for repeatable, scriptable runs. Nothing writes until the chosen plan
> is approved.

> **4. Reversible + permission-aware.** Every write (sync-mirror creates) is journalled and
> undoable. Org writes happen only where the user has the role to make them; lacking rights →
> read-and-report, never attempt.

## §1 — CapabilityProbe (runs first, read-only)

Per vault/profile, before any dedup or sync:

1. **Server type** — self-hosted **Vaultwarden** → treat as **unlimited** (no plan enforcement).
   **bitwarden.com** → apply tier logic. (Detect via the profile's configured `serverUrl`.)
2. **Org enumeration** — `bw list organizations` → the orgs the user can access, with their
   role (`type`: 0=Owner … so we know where writes are permitted). Per org, current collections
   via `bw list org-collections --organizationid <id>`.
3. **Plan/collection capacity (cloud only)** — three layers, best-available wins:
   - **API** (accurate): `GET /api/organizations/{id}` with an org API key → `planType`,
     `maxCollections`, `seats`, `use*`. The `bw` CLI does **not** expose these (its org object
     is only name/enabled/status/type — verified), so the API is the precise source.
   - **Empirical** (no API key): use the current collection count; on a create that would
     exceed the cap, catch the limit error and back off — discover the ceiling by probing.
   - **Known-tier table** (fallback): Free = 2 collections / 2 users; Families = unlimited /
     6 users; Teams/Enterprise = unlimited. If unresolved, assume **most-restrictive** + warn.

Output: a `Capabilities` record per vault — `{server_type, unlimited: bool, max_collections,
orgs: [{id, name, role, collections:[...]}]}`.

## §2 — Report + Pathways (the UX/decision layer)

After the probe, **report** the reality and the gap, e.g.:
```
Sources: personal (1251) + org "Familion" (N items / C collections)
                          + org "Sandulache 2.0" (M items / D collections)
Target:  bitwarden.com  ->  Free org  ->  capacity: 2 collections, 2 users
Gap:     2 source orgs vs 2-collection cap  ->  fits as 1 collection per org
```
Then offer **pathways** (each a concrete executable plan):
- **Per-org → one collection** (source org flattens to a single target collection; fits the
  user's 2-org / 2-collection case exactly)
- **Full structural mirror** (offered only when `max_collections >= source collections`)
- **Select collections to mirror** (pick which, drop the rest — explicit)
- **Personal-only** (skip org sync)
- **Advisory** (non-acting): "Families/Teams would lift the cap"

**Programmable:** the same choice resolves from either an **interactive** prompt (inline-TTY
menu; rich in the file tool) **or** a **flag/config** (`--org-strategy per-org-collection`, or a
mapping file `org-id -> target-collection`). Interactive when a TTY and no config; config wins
when present (repeatable/automatable). The resolved pathway becomes the plan.

## §3 — Org-aware dedup (orgs read-only)

Extends `bw-dedup` (and via the companion, the file tool):
- Population = personal (read-write) + all org items (read-only reference, from the probe).
- **Within personal:** existing algorithm, unchanged.
- **Within an org:** nothing (orgs rarely have internal dupes; orgs are read-only regardless).
- **Cross-boundary:** a personal login whose `identity.fingerprint` (`uri+username+password`)
  matches **any** org item → emit a `ClearPersonalDupOp` (soft-delete the personal copy; org
  untouched). Same-login-in-two-orgs → flag only (shared-vs-shared, never auto-touched).
- The `ClearPersonalDupOp` is a destructive op on a *personal* item → gated + journalled +
  30-day trash, exactly like existing deletes.

## §4 — Org-aware sync-mirror (`bw-sync`, capacity-adaptive)

Replicate Vaultwarden orgs into a target org's collections per the chosen pathway:
- **Vaultwarden is authoritative** (source of truth); the target org is the mirror.
- Build the mirror plan from the pathway + capacity: which source org/collection → which target
  collection; which items to `bw create` into each (additive; dedup against the target so
  re-runs are idempotent — reuse `import_plan`'s fingerprint-vs-target logic).
- Create target collections as needed (within cap); assign created items to the mapped
  collection. Journalled: undo deletes created items (and, optionally, created collections).
- Items unlimited → all org logins mirror; only structure flattens under a cap (Cornerstone 2).

## Components

| Module | Responsibility | New/reused |
|---|---|---|
| `capabilities.py` | CapabilityProbe: server type, org enumeration, plan/limit detection (API/empirical/table) → `Capabilities` | **new** |
| `pathways.py` | render report; enumerate viable pathways for a `Capabilities`; resolve choice (interactive or config) → a mirror/strategy plan | **new** |
| `org_dedup` (in `plan.py`) | `ClearPersonalDupOp` + extend `build_dedup_plan` to take org reference items and clear personal dupes | extend existing |
| `sync_mirror` (Plan-2 `merge.py`/new) | build + apply the capacity-adaptive mirror from the chosen pathway | sync work |
| `bw_adapter.py` | add `list_organizations`, `list_org_collections`, `create_collection`, org-scoped export | extend |
| `identity` / `checkpoint` / `keyprovider` / `tmpfs` | fingerprint match / journal-undo / encryption / shred | reused |

## Data flow

```
CapabilityProbe(profile) -> Capabilities
  -> (dedup)  build_dedup_plan(personal_items, org_reference_items) -> + ClearPersonalDupOp
  -> (sync)   Report+Pathways(Capabilities) -> chosen pathway
              -> build_mirror_plan(source orgs, target capacity, pathway)
              -> approve -> bw create (collections + items), journalled
```

## Error handling

- No API key + cloud → empirical/known-table capacity, clearly labelled "estimated".
- Lacking org write role → that org is read-only-reported for sync; never attempted.
- Collection-create hits the cap mid-run → stop, report, fall back to the consolidate pathway
  (or abort with the partial journal intact for undo).
- Non-TTY + no config → dedup proceeds (org reference is read-only); sync-mirror is **not**
  auto-run (needs a pathway choice) — it reports and exits asking for `--org-strategy`.

## Testing

- `capabilities.py`: server-type detection from URL; org enumeration from a fake adapter;
  capacity resolution across the three layers (API JSON fixture, empirical-count, table
  fallback) and the most-restrictive default.
- `pathways.py`: pure — given a `Capabilities`, the correct pathway set is offered; config
  resolution (`--org-strategy`, mapping file) selects without prompting; interactive path tested
  with an injected chooser.
- `plan.py` org dedup: personal item duplicated in an org → `ClearPersonalDupOp`; not in any org
  → untouched; same-login-in-two-orgs → flagged, no op; org items never appear in any write op.
- `sync_mirror`: 2 orgs + 2-collection cap → per-org mapping creates 2 collections + items;
  re-run idempotent; undo deletes created items/collections; over-cap → consolidate path.
- Live (test substrate): probe both real vaults read-only (already done — 2 orgs, free=2-coll);
  a controlled mirror of a handful of disposable items into a throwaway collection + undo.

## Non-goals

Within-org dedup (orgs rarely have dupes; read-only anyway). Relocating items between vaults
(hard no). Editing/deleting existing org items (read-only; sync only *creates* the mirror).
Auto-upgrading a plan (advisory only). Preserving nested collection structure beyond a tier's
cap (flatten, never drop items).

## Sequencing

- **Phase A — CapabilityProbe + org-aware dedup + the file-tool `exclude_org_dupes` fix.**
  Self-contained, high value, no shared-data writes. Ships on top of the merged `bw-dedup`.
- **Phase B — Report + Pathways UX + sync-mirror.** Builds on Plan 2 (`bw-sync`, not yet
  implemented) and Phase A's probe. This is the "smarter sync" with the capacity-adaptive
  mirror.

Each phase is its own implementation plan. Phase A can start immediately; Phase B follows
`bw-sync`'s core.

## Risks & assumptions

- **CLI hides plan/limits** → the API key path is the only *precise* capacity source; without
  it the tool estimates and labels it so. Validated against the real free org (2 collections).
- **Org API endpoints** assumed stable for `GET /api/organizations/{id}`; behind the same
  version-gate as `bw`.
- **bw create into a collection** assigns `collectionIds`; relied on for the mirror — validated
  in the live adapter test before any `--apply`.
- **Free-tier caps** (2 users / 2 collections) confirmed current (2026); the strategy table is
  data, easily updated if tiers change.
