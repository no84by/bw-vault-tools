"""Helpers for reading Bitwarden export item dicts (no mutation)."""


def item_type(item: dict) -> int | None:
    t = item.get("type")
    return t if isinstance(t, int) else None


def is_login(item: dict) -> bool:
    return isinstance(item.get("login"), dict)


def _login(item: dict) -> dict:
    lg = item.get("login")
    return lg if isinstance(lg, dict) else {}


def has_passkey(item: dict) -> bool:
    return bool(_login(item).get("fido2Credentials"))


def has_uris(item: dict) -> bool:
    return bool(_login(item).get("uris"))


def first_uri(item: dict) -> str | None:
    uris = _login(item).get("uris") or []
    return uris[0]["uri"] if uris else None
