"""Browser discovery + user-exported CSV ingest. Ported from bitwarden-vault-cleanup (MIT).
Detection is by installed executable, never by reading/decrypting any browser credential store."""
import csv
import os
import shutil
import sys
import time
import uuid

from . import identity

# Detect a browser by its executable on PATH, not by guessing profile-folder locations. This is
# channel- (stable/dev/beta), XDG- (~/.config/mozilla vs ~/.mozilla), snap- and flatpak-agnostic,
# works the same on Linux/macOS/Windows (shutil.which honours PATHEXT), and naturally excludes
# automation builds (chrome-for-testing, *-cdp) which expose no browser-named binary on PATH.
_BROWSER_BINARIES = {
    "firefox": ["firefox", "firefox-developer-edition", "firefox-esr", "firefox-bin", "firefox-nightly"],
    "chrome":  ["google-chrome", "google-chrome-stable", "google-chrome-beta", "google-chrome-unstable",
                "chromium", "chromium-browser", "chrome"],
    "edge":    ["microsoft-edge", "microsoft-edge-stable", "microsoft-edge-dev", "microsoft-edge-beta",
                "msedge"],
    "brave":   ["brave-browser", "brave-browser-stable", "brave"],
    "opera":   ["opera", "opera-stable"],
    "vivaldi": ["vivaldi", "vivaldi-stable"],
}
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


# Windows does not put browser executables on PATH; they register under the App Paths registry
# key instead. This is the Windows-native "is this app installed" lookup, the analogue of which().
_BROWSER_WIN_EXES = {
    "firefox": ["firefox.exe"], "chrome": ["chrome.exe"], "edge": ["msedge.exe"],
    "brave": ["brave.exe"], "opera": ["opera.exe", "launcher.exe"], "vivaldi": ["vivaldi.exe"],
}


def _win_registered_browsers():
    """Browser families registered in the Windows App Paths registry. Empty off-Windows."""
    if sys.platform != "win32":
        return set()
    import winreg
    found = set()
    for family, exes in _BROWSER_WIN_EXES.items():
        for exe in exes:
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + exe).Close()
                    found.add(family)
                    break
                except OSError:
                    continue
    return found


def detect_browsers():
    """Installed browser families. Linux/macOS: executable on PATH (shutil.which). Windows: the
    App Paths registry (exes aren't on PATH). Never reads any credential store. Best-effort hint
    for export guidance — the authoritative input is scan_for_exports; detection never gates an
    import. Automation builds (chrome-for-testing, *-cdp) appear in neither and stay excluded."""
    found = {fam for fam, binaries in _BROWSER_BINARIES.items()
             if any(shutil.which(b) for b in binaries)}
    return found | _win_registered_browsers()


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
