"""Self-bootstrap: install the lone dependency on demand, then version-gate bw."""
import re
import shutil
import subprocess
import sys

TESTED_MIN, TESTED_MAX = (2026, 4), (2026, 5)


def ensure_cryptography() -> None:
    """Import cryptography; if missing, pip-install it into the active interpreter and retry."""
    try:
        import cryptography  # noqa: F401
        return
    except ImportError:
        print("[bootstrap] installing cryptography...", file=sys.stderr)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "cryptography>=42,<45"])
        import cryptography  # noqa: F401,F811


def parse_bw_version(text: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    if not m:
        raise ValueError(f"unparseable bw version: {text!r}")
    return tuple(int(x) for x in m.groups())


def version_supported(v: tuple) -> bool:
    return TESTED_MIN <= (v[0], v[1]) <= TESTED_MAX


def ensure_bw() -> tuple:
    if not shutil.which("bw"):
        sys.exit("bw CLI not found on PATH. Install: npm i -g @bitwarden/cli "
                 "(or the native binary from bitwarden.com/help/cli).")
    out = subprocess.run(["bw", "--version"], capture_output=True, text=True, check=True).stdout
    v = parse_bw_version(out)
    if not version_supported(v):
        print(f"[warn] bw {v[0]}.{v[1]}.{v[2]} outside tested band "
              f"{TESTED_MIN[0]}.{TESTED_MIN[1]}-{TESTED_MAX[0]}.{TESTED_MAX[1]}; proceeding.",
              file=sys.stderr)
    return v
