from bw_vault_tools import capabilities, pathways, sync_mirror
from tests import fixtures as fx


# --- capabilities.detect_capacity ---

def test_selfhosted_is_unlimited():
    cap, src = capabilities.detect_capacity("https://vault.sandulache.net")
    assert cap is None and src == "selfhosted"


def test_cloud_free_table_fallback():
    cap, src = capabilities.detect_capacity("https://bitwarden.com")
    assert cap == 2 and src == "table"


def test_cloud_api_plan_wins():
    cap, src = capabilities.detect_capacity("https://bitwarden.com", api_plan={"maxCollections": None})
    assert cap is None and src == "api"


# --- pathways ---

def _orgs():
    return [{"id": "o1", "name": "Familion", "collections": ["a"], "item_count": 10},
            {"id": "o2", "name": "Sandulache 2.0", "collections": ["b", "c"], "item_count": 20}]


def test_two_orgs_fit_free_cap_per_org():
    ways = pathways.viable_pathways(_orgs(), max_collections=2)
    keys = {p.key for p in ways}
    assert "per-org-collection" in keys            # 2 orgs <= 2 collections
    assert "full-mirror" not in keys               # 3 total collections > 2 cap
    per = next(p for p in ways if p.key == "per-org-collection")
    assert per.mapping == {"o1": "Familion", "o2": "Sandulache 2.0"}


def test_unlimited_offers_full_mirror():
    ways = pathways.viable_pathways(_orgs(), max_collections=None)
    assert "full-mirror" in {p.key for p in ways}


def test_resolve_by_config_key():
    ways = pathways.viable_pathways(_orgs(), max_collections=2)
    chosen = pathways.resolve(ways, choice_key="per-org-collection")
    assert chosen.key == "per-org-collection"


# --- sync_mirror.build_mirror_plan ---

def test_mirror_creates_only_new_into_mapped_collection():
    src = {"o1": [fx.login("s1", uri="https://new.com", username="u", password="p")]}
    way = pathways.Pathway("per-org-collection", "x", {"o1": "Familion"})
    plan = sync_mirror.build_mirror_plan(src, way, target_items=[], target_collections=[])
    assert plan.collections_to_create == ["Familion"]
    assert len(plan.item_creates) == 1 and plan.item_creates[0][1] == "Familion"


def test_mirror_skips_items_already_in_target():
    src = {"o1": [fx.login("s1", uri="https://x.com", username="u", password="p")]}
    target = [fx.login("t1", uri="https://x.com", username="u", password="p")]   # same fingerprint
    way = pathways.Pathway("per-org-collection", "x", {"o1": "Familion"})
    plan = sync_mirror.build_mirror_plan(src, way, target_items=target, target_collections=["Familion"])
    assert plan.item_creates == [] and plan.skipped == 1
    assert plan.collections_to_create == []        # collection already exists


def test_mirror_guards_passkey():
    src = {"o1": [fx.passkey_login("s1", uri="https://x.com", username="u", password="p")]}
    way = pathways.Pathway("per-org-collection", "x", {"o1": "Familion"})
    plan = sync_mirror.build_mirror_plan(src, way, target_items=[], target_collections=[])
    assert plan.item_creates == [] and plan.guarded == 1
