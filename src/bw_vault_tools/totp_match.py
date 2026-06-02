"""Pure matching of Google-Authenticator TOTP accounts to vault logins -> typed plan ops.

Deterministic and side-effect-free. The CLI decides what to do with each op (apply, or, for an
Ambiguous op, prompt). Match-confidently-or-ask: a single confident login match resolves
automatically; anything ambiguous or unmatched is surfaced, never guessed.
"""
import urllib.parse
from dataclasses import dataclass


@dataclass
class SetTotp:
    """Login has no TOTP -> set this seed on it."""
    item_id: str
    item_name: str
    seed: str
    kind: str = "set"


@dataclass
class Skip:
    """Login already holds the identical seed -> nothing to do."""
    account: dict
    item_name: str
    kind: str = "skip"


@dataclass
class Duplicate:
    """Login already holds a DIFFERENT seed -> never overwrite; create a duplicate item instead."""
    name: str
    username: str
    uri: str
    seed: str
    kind: str = "duplicate"


@dataclass
class Ambiguous:
    """No single confident match (zero or several candidate logins) -> the user must decide."""
    account: dict
    candidates: list      # list of (item_id, item_name, username)
    kind: str = "ambiguous"


def normalize_seed(totp) -> str:
    """Canonical base32 seed for comparison: accepts a raw seed or an otpauth:// URI."""
    if not totp:
        return ""
    if totp.startswith("otpauth://"):
        totp = urllib.parse.parse_qs(urllib.parse.urlparse(totp).query).get("secret", [""])[0]
    return totp.replace(" ", "").rstrip("=").upper()


def _domains(item) -> list[str]:
    out = []
    for u in ((item.get("login") or {}).get("uris") or []):
        uri = u.get("uri") or ""
        host = urllib.parse.urlparse(uri if "//" in uri else "http://" + uri).hostname or ""
        out.append(host[4:] if host.startswith("www.") else host)
    return out


def build_totp_plan(accounts, items) -> list:
    """Map each account to exactly one of Set / Skip / Duplicate / Ambiguous."""
    logins = [it for it in items if it.get("type") == 1]
    low = lambda s: (s or "").lower().strip()
    plan = []
    for a in accounts:
        token = low(a.get("issuer")) or low(a.get("name"))
        uname = low(a.get("name"))
        seed = (a["seed"] or "").rstrip("=").upper()
        cands = [it for it in logins
                 if token and (token in low(it.get("name")) or any(token in d for d in _domains(it)))]
        by_user = [it for it in cands if low((it.get("login") or {}).get("username")) == uname]
        if by_user:
            cands = by_user
        if len(cands) == 1:
            it = cands[0]
            cur = normalize_seed((it.get("login") or {}).get("totp"))
            if not cur:
                plan.append(SetTotp(it["id"], it.get("name"), seed))
            elif cur == seed:
                plan.append(Skip(a, it.get("name")))
            else:
                uri = ((it.get("login") or {}).get("uris") or [{}])[0].get("uri") or ""
                plan.append(Duplicate(it.get("name"), a.get("name"), uri, seed))
        else:
            plan.append(Ambiguous(a, [(it["id"], it.get("name"),
                                       (it.get("login") or {}).get("username")) for it in cands]))
    return plan
