"""Pure identity/dedup functions. Algorithm ported from bitwarden-vault-cleanup (MIT)."""
import re
from collections import defaultdict

from . import models


def normalize_uri(uri: str | None) -> str:
    if not uri:                                        # tolerate {"uri": null} / empty (v2.1 carry)
        return ""
    if uri.startswith("android://") or uri.startswith("androidapp://"):
        m = re.search(r"@(.+?)(?:/|$)", uri)          # tolerate missing trailing slash
        return m.group(1) if m else uri.split("://")[-1]
    uri = re.sub(r"^https?://", "", uri).lower().rstrip("/")
    return uri.split("/")[0]


def fingerprint(item: dict, include_password: bool = True) -> tuple:
    """Stable key for 'same logical login'.

    Dedup (one vault): include_password=True -> (normalized-uri, username, password).
    Sync first-run pairing (two vaults, password may differ): include_password=False ->
        (normalized-uri, username, type).
    """
    uri = models.first_uri(item)
    login = item.get("login", {})
    base = (normalize_uri(uri) if uri else None, login.get("username"))
    if include_password:
        pw = login.get("password") or ""
        if not isinstance(pw, str):
            pw = str(pw)
        return base + (pw.strip(),)
    return base + (models.item_type(item),)


def group_logins(items: list[dict]) -> dict[tuple, list[dict]]:
    """Group URI-bearing logins by dedup fingerprint. Items without URIs are NOT grouped
    (cannot be matched) — the caller preserves them."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for it in items:
        if models.is_login(it) and models.has_uris(it):
            groups[fingerprint(it)].append(it)
    return dict(groups)


def _last_used(item: dict) -> str:
    return max((h.get("lastUsedDate", "") for h in (item.get("passwordHistory") or [])), default="")


def score_entry(item: dict, reused: set[str]) -> tuple:
    """Sort key; MAX entry is the one to keep (mirrors v2.0):
    lastUsedDate, revisionDate, password-uniqueness, creationDate, id."""
    login = item.get("login", {})
    return (_last_used(item), item.get("revisionDate", ""),
            (login.get("password") or "") not in reused,
            item.get("creationDate", ""), item.get("id", ""))


def pick_best(group: list[dict], reused: set[str]) -> dict:
    return sorted(group, key=lambda e: score_entry(e, reused))[-1]


def merge_uris(group: list[dict]) -> list[str]:
    # filter falsy uris (v2.1 carry): a {"uri": null} entry would otherwise make sorted() raise
    return sorted({u["uri"] for e in group for u in (e.get("login", {}).get("uris") or []) if u.get("uri")})
