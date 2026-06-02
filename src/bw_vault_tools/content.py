"""Pure: a hashable key of an item's SYNCABLE content (ignores ids and volatile dates)."""
from . import identity, models


def content_key(item: dict) -> tuple:
    """Stable key for 'has this item's content changed'. Covers name, normalized uris,
    username, password, totp, notes, custom fields, type. Excludes id/revisionDate/creationDate."""
    login = item.get("login") or {}
    uris = tuple(sorted(
        identity.normalize_uri(u.get("uri")) for u in (login.get("uris") or []) if u.get("uri")))
    return (
        models.item_type(item),
        (item.get("name") or "").strip(),
        (item.get("notes") or "").strip(),
        uris,
        login.get("username"),
        login.get("password"),
        login.get("totp"),
        fields_key(item),
    )


def fields_key(item: dict) -> tuple:
    """Custom fields as a sorted, hashable set of (name, value, type)."""
    return tuple(sorted((f.get("name"), f.get("value"), f.get("type"))
                        for f in (item.get("fields") or [])))
