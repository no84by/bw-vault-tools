"""Browser discovery + user-exported CSV ingest. Ported from bitwarden-vault-cleanup (MIT).
Presence-only detection: never opens/reads/decrypts any browser credential store."""
import csv
import os
import platform
import time
import uuid

from . import identity

BROWSERS = {
    "firefox":  {"linux": ".mozilla/firefox", "darwin": "Library/Application Support/Firefox",
                 "windows": "AppData/Roaming/Mozilla/Firefox"},
    "chrome":   {"linux": ".config/google-chrome", "darwin": "Library/Application Support/Google/Chrome",
                 "windows": "AppData/Local/Google/Chrome/User Data"},
    "edge":     {"linux": ".config/microsoft-edge", "darwin": "Library/Application Support/Microsoft Edge",
                 "windows": "AppData/Local/Microsoft/Edge/User Data"},
    "brave":    {"linux": ".config/BraveSoftware/Brave-Browser",
                 "darwin": "Library/Application Support/BraveSoftware/Brave-Browser",
                 "windows": "AppData/Local/BraveSoftware/Brave-Browser/User Data"},
    "opera":    {"linux": ".config/opera", "darwin": "Library/Application Support/com.operasoftware.Opera",
                 "windows": "AppData/Roaming/Opera Software/Opera Stable"},
    "vivaldi":  {"linux": ".config/vivaldi", "darwin": "Library/Application Support/Vivaldi",
                 "windows": "AppData/Local/Vivaldi/User Data"},
    "safari":   {"darwin": "Library/Safari"},
}
_OS_KEY = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}
_BROWSER_CSV_KIND = {"chrome": "chromium_csv", "edge": "chromium_csv", "brave": "chromium_csv",
                     "opera": "chromium_csv", "vivaldi": "chromium_csv",
                     "firefox": "firefox_csv", "safari": "safari_csv"}
_CSV_SCHEMA = {
    "chromium_csv": ("name", "url", "username", "password", "note", "chromium"),
    "firefox_csv": (None, "url", "username", "password", None, "firefox"),
    "safari_csv": ("title", "url", "username", "password", "notes", "safari"),
}
_EXPORT_STEPS = {
    "chrome": "Chrome: Settings -> Autofill and passwords -> Google Password Manager -> Settings "
              "-> Export passwords. Save the CSV to your Downloads folder.",
    "edge": "Edge: Settings -> Profiles -> Passwords -> (...) -> Export passwords. Save to Downloads.",
    "brave": "Brave: Settings -> Passwords and autofill -> Password Manager -> Settings -> Export "
             "passwords. Save to Downloads.",
    "opera": "Opera: Settings -> Privacy & security -> Passwords -> (...) -> Export passwords. Save to Downloads.",
    "vivaldi": "Vivaldi: Settings -> Passwords -> Export passwords. Save to Downloads.",
    "firefox": "Firefox: menu -> Passwords -> (...) -> Export Logins. Save the CSV to Downloads.",
    "safari": "Safari/macOS: Passwords app -> File -> Export Passwords. Save to Downloads. "
              "(No automatic detection on Safari.)",
}


def detect_browsers(home=None):
    """Installed browsers by profile-directory EXISTENCE only. Never reads inside them."""
    home = home or os.path.expanduser("~")
    osk = _OS_KEY.get(platform.system())
    found = set()
    if not osk:
        return found
    for name, paths in BROWSERS.items():
        rel = paths.get(osk)
        if rel and os.path.isdir(os.path.join(home, rel)):
            found.add(name)
    return found


def downloads_dir():
    cand = os.path.join(os.path.expanduser("~"), "Downloads")
    return cand if os.path.isdir(cand) else os.getcwd()


def classify_export(path):
    """Sniff kind by content (utf-8-sig handles BOM). bitwarden_json / *_csv / None."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            head = f.read(4096)
            if head.lstrip().startswith("{"):
                if '"items"' not in head:
                    head += f.read(1_000_000)
                return "bitwarden_json" if '"items"' in head else None
    except OSError:
        return None
    first = head.splitlines()[0].lower() if head.splitlines() else ""
    cols = {c.strip().strip('"') for c in first.split(",")}
    if {"url", "username", "password", "httprealm"} <= cols:
        return "firefox_csv"
    if {"title", "url", "username", "password"} <= cols:
        return "safari_csv"
    if {"name", "url", "username", "password"} <= cols:
        return "chromium_csv"
    return None


def scan_for_exports(dirs):
    seen, out = set(), []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.lower().endswith((".csv", ".json")):
                continue
            p = os.path.realpath(os.path.join(d, name))
            if p in seen:
                continue
            seen.add(p)
            kind = classify_export(p)
            if kind:
                out.append((p, kind))
    return out


def csv_to_items(path, kind):
    """Map a browser CSV to Bitwarden login items (one per row, fresh uuid id)."""
    name_c, url_c, user_c, pass_c, note_c, source = _CSV_SCHEMA[kind]
    items = []
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        by_lower = {(fn or "").strip().lower(): fn for fn in (reader.fieldnames or [])}

        def get(row, col):
            if not col:
                return ""
            orig = by_lower.get(col)
            return (row.get(orig) or "").strip() if orig else ""

        for row in reader:
            url = get(row, url_c)
            name = get(row, name_c) or identity.normalize_uri(url) or "(imported)"
            note = get(row, note_c)
            tag = f"[source: {source}]"
            items.append({
                "id": str(uuid.uuid4()), "organizationId": None, "folderId": None, "type": 1,
                "name": name, "notes": (note + "\n" + tag).strip() if note else tag,
                "login": {"uris": [{"uri": url, "match": None}] if url else None,
                          "username": get(row, user_c) or None, "password": get(row, pass_c) or None,
                          "totp": None, "fido2Credentials": []},
            })
    return items


def export_instructions(browser):
    return _EXPORT_STEPS.get(browser, f"{browser}: use its built-in 'Export passwords' to CSV.")


def browser_csv_kind(browser):
    return _BROWSER_CSV_KIND.get(browser)


def collect_source(browser, expected_kinds, watch, ask, now, timeout=180.0, poll=1.0, info=print):
    """Guide the user to export `browser`; return the produced file path or None.
    watch(kinds, since)->path|None, ask(prompt)->str, now()->float are injected for tests."""
    info("\n" + export_instructions(browser))
    start = now()
    while now() - start < timeout:
        hit = watch(expected_kinds, start)
        if hit:
            info(f"  detected export: {os.path.basename(hit)}")
            return hit
        if poll:
            time.sleep(poll)
    resp = ask(f"  No {browser} export detected. Paste a path, or press Enter to skip: ").strip()
    return resp if resp and os.path.isfile(resp) else None


def real_watch(expected_kinds, since):
    """Newest CSV in Downloads/CWD modified after `since` whose kind matches, else None."""
    candidates = []
    for d in (downloads_dir(), os.getcwd()):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.lower().endswith(".csv"):
                continue
            p = os.path.join(d, name)
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            if mtime >= since and classify_export(p) in expected_kinds:
                candidates.append((mtime, os.path.realpath(p)))
    return max(candidates)[1] if candidates else None
