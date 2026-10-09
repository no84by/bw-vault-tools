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


# --- Contextual dedup identity engine (ported verbatim from bitwarden-vault-cleanup v2.3, MIT) --
# A deduplication KEY must reflect reality, and reality differs by context: a real email identity
# ("silviusandulache@gmail.com") is ONE person across a site's sub-domains and URL forms
# (account.x.com / www.x.com / http://), so those collapse to the registrable domain; a role /
# generic username ("admin", "support") is NOT a reliable identity (multi-tenant estates like
# anneberg.net treat support.x.net and careers.x.net as separate deployments), so those stay pinned
# to the exact host. There is no universal rule; the key adapts to the user. The public-suffix
# subset below also stops us by accident grouping by a bare TLD (.co.uk, .com.au ...), which would
# fuse unrelated sites that merely share a suffix (fielddoctor.co.uk vs welcomebreak.co.uk).
_SLD = {"co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "nat.uk", "net.uk", "ltd.uk", "plc.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.jp", "com.br", "com.cn", "com.mx",
        "co.nz", "co.za", "com.tr", "com.sg", "com.hk", "com.tw", "com.ar", "co.in", "co.kr"}
_ROLE_USER = {"admin", "administrator", "user", "users", "test", "testing", "root", "info",
              "support", "sales", "hello", "mail", "webmaster", "postmaster", "noreply",
              "no-reply", "contact", "account", "service", "security", "it", "sysadmin",
              "login", "signin", "wp-admin"}


def _host(uri):
    """scheme/www/numeric-port stripped host, so http vs https, www. and :port never split one site
    (IPv6 hosts keep their colons because the post-':' part is not a bare integer)."""
    uri = (uri or "").lower()
    host = re.sub(r"^https?://", "", uri).split("/", 1)[0].split("#")[0].split("?")[0]
    if ":" in host and host.rsplit(":", 1)[-1].isdigit():
        host = host.rsplit(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip("/")


def _registrable_host(host):
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _canon_user(name):
    """Lower-case a username; for emails also drop Gmail/Google '+' aliasing and '.' (Gmail ignores
    them) so case and address variants collapse to one identity. Everything else is only lower-cased,
    so the ONLY thing that can distinguish accounts after canon is genuine username/whitespace content."""
    name = (name or "").strip()
    if "@" in name:
        local, _, dom = name.lower().partition("@")
        if dom in ("gmail.com", "googlemail.com"):
            local = local.replace(".", "").split("+")[0]
        elif dom in ("outlook.com", "hotmail.com", "live.com"):
            local = local.split("+")[0]
        return local + "@" + dom
    return name.lower()


def _is_identity_user(name):
    """True when the username looks like a specific person's email (long local-part, not a role)."""
    name = (name or "").strip()
    if "@" not in name:
        return False
    local = name.split("@", 1)[0].lower()
    return len(local) >= 4 and local not in _ROLE_USER


def dedup_key(item: dict) -> tuple:
    """Contextual dedup key. A specific email identity collapses across the site's sub-domains and
    URL forms to its registrable domain; a role/generic username stays pinned to the exact host.
    NOTE: this is a dedup-only grouping (bw-vault-tools splits dedup from sync). The sync pairing
    still uses fingerprint() with include_password=False, which is intentionally left unchanged."""
    login = item.get("login", {})
    uris = login.get("uris") or []
    first = uris[0].get("uri") if uris else None
    host = _host(first)
    username = login.get("username")
    user = _canon_user(username)
    key_host = (_registrable_host(host) if _is_identity_user(username) else host)
    return (key_host, user)


def group_logins_dedup(items: list[dict]) -> dict[tuple, list[dict]]:
    """Group URI-bearing logins by the contextual dedup key. Items without URIs are NOT grouped
    (cannot be matched) — the caller preserves them. Mirrors bitwarden-vault-cleanup v2.3 grouping."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for it in items:
        if models.is_login(it) and models.has_uris(it):
            groups[dedup_key(it)].append(it)
    return dict(groups)
