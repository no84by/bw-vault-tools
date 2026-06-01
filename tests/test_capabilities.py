from bw_vault_tools import capabilities


class FakeProfile:
    def __init__(self, items):
        self._items = items

    def list_items(self):
        return list(self._items)


def test_org_reference_items_returns_only_org_items():
    prof = FakeProfile([
        {"id": "p1", "organizationId": None, "type": 1},
        {"id": "o1", "organizationId": "org-a", "type": 1},
        {"id": "o2", "organizationId": "org-b", "type": 1},
    ])
    ref = capabilities.org_reference_items(prof)
    assert {i["id"] for i in ref} == {"o1", "o2"}


def test_org_reference_items_empty_when_no_orgs():
    prof = FakeProfile([{"id": "p1", "organizationId": None}])
    assert capabilities.org_reference_items(prof) == []
