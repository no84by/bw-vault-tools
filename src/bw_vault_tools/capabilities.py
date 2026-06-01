"""Read-only probe of the vault's organization surface.

Phase A: enumerate org items as a read-only reference for dedup. Full plan/collection-limit
detection (API / empirical / known-tier) is Phase B (sync-mirror), not needed here."""


def org_reference_items(prof) -> list:
    """All items belonging to any organization the user can access. READ-ONLY reference set --
    these items are never edited, deleted, or relocated; they only tell dedup which PERSONAL
    items are redundant."""
    return [it for it in prof.list_items() if it.get("organizationId")]
