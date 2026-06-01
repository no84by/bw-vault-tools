"""Read-only probe of the vault's organization surface + (Phase B) capacity detection."""


def org_reference_items(prof) -> list:
    """All items belonging to any organization the user can access. READ-ONLY reference set --
    these items are never edited, deleted, or relocated; they only tell dedup which PERSONAL
    items are redundant."""
    return [it for it in prof.list_items() if it.get("organizationId")]


# --- Phase B: capability/limit detection (for the sync-mirror target) ---

# Known free-tier-and-up collection caps. None == unlimited.
KNOWN_TIER_MAX_COLLECTIONS = {"free": 2, "families": None, "teams": None, "enterprise": None}


def server_type(server_url) -> str:
    """'cloud' for bitwarden.com/.eu, else 'selfhosted' (Vaultwarden = no plan enforcement)."""
    url = (server_url or "").lower()
    if not url or "bitwarden.com" in url or "bitwarden.eu" in url:
        return "cloud"
    return "selfhosted"


def detect_capacity(server_url, api_plan=None, assume_tier="free"):
    """Resolve the target org's collection capacity. Returns (max_collections|None, source).
    None max == unlimited. Layers: self-hosted -> unlimited; API plan -> its maxCollections;
    else the known-tier table (default most-restrictive 'free'), labelled so the caller can warn."""
    if server_type(server_url) == "selfhosted":
        return None, "selfhosted"
    if api_plan is not None:
        return api_plan.get("maxCollections"), "api"          # None -> unlimited
    return KNOWN_TIER_MAX_COLLECTIONS.get(assume_tier, 2), "table"
