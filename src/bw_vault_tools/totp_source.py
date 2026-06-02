"""Decode a Google Authenticator export into TOTP accounts.

GA "Transfer accounts -> Export" produces QR code(s) encoding
`otpauth-migration://offline?data=<base64>`, where the payload is a protobuf. The QR image is
decoded with `zbar` (only non-pure step); the protobuf is hand-parsed (no protobuf dependency).
We never read the Authenticator app's storage — only an export image YOU provide.

Credit: the payload is Google Authenticator's `MigrationPayload` protobuf
(github.com/google/google-authenticator-android, Apache-2.0); the field layout decoded here
follows the community reverse-engineering of that format (notably Alexander Bakker's writeup,
"Decoding the Google Authenticator export QR code"). QR decoding uses zbar via `pyzbar`/`zbarimg`.
"""
import base64
import subprocess
import urllib.parse


def _varint(b, i):
    shift = val = 0
    while True:
        c = b[i]
        i += 1
        val |= (c & 0x7F) << shift
        if not c & 0x80:
            return val, i
        shift += 7


def _fields(b):
    """Minimal protobuf reader -> {field_number: [values]}."""
    f, i = {}, 0
    while i < len(b):
        tag, i = _varint(b, i)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            ln, i = _varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        else:
            break
        f.setdefault(fn, []).append(v)
    return f


def decode_migration_uri(uri: str) -> list[dict]:
    """Parse one `otpauth-migration://...?data=` URI into TOTP accounts.

    Each account: {issuer, name, seed} where seed is an un-padded base32 string (the form
    Bitwarden's `login.totp` accepts). HOTP entries (type != 2) are skipped.
    """
    if "data=" not in uri:
        return []
    data = urllib.parse.unquote(uri.split("data=", 1)[1])
    blob = base64.b64decode(data + "=" * (-len(data) % 4))
    out = []
    for msg in _fields(blob).get(1, []):                # field 1 = repeated OtpParameters
        p = _fields(msg)
        if p.get(6, [2])[0] != 2:                       # field 6 = type; 2 = TOTP
            continue
        out.append({
            "issuer": p.get(3, [b""])[0].decode("utf-8", "replace"),   # field 3 = issuer
            "name": p.get(2, [b""])[0].decode("utf-8", "replace"),     # field 2 = name (username)
            "seed": base64.b32encode(p.get(1, [b""])[0]).decode().rstrip("="),  # field 1 = secret
        })
    return out


def read_qr(image_path: str, runner=None) -> str:
    """Decode the QR in an image file to its raw text, cross-platform. Tries, in order:
    pyzbar (pip; bundles zbar on Windows, uses libzbar on Linux/macOS), then the zbarimg CLI.
    `runner` (injectable for tests) forces the CLI path. Raises with install hints if neither
    is available — in which case use the migration URI directly (see accounts_from)."""
    if runner is None:
        try:
            from PIL import Image                       # noqa: PLC0415
            from pyzbar.pyzbar import decode as _decode  # noqa: PLC0415
            res = _decode(Image.open(image_path))
            if res:
                return res[0].data.decode("utf-8", "replace").strip()
        except ImportError:
            pass
    import shutil
    if runner or shutil.which("zbarimg"):
        run = runner or (lambda a: subprocess.run(a, capture_output=True, text=True, check=True).stdout)
        return run(["zbarimg", "--raw", "-q", image_path]).strip()
    raise RuntimeError(
        "No QR decoder available. Install one of:\n"
        "  pip install 'bw-vault-tools[totp]'   (cross-platform; bundles zbar on Windows)\n"
        "  dnf/apt/brew install zbar            (provides the zbarimg CLI)\n"
        "Or skip image decoding and pass the export's otpauth-migration:// text via --uri/--uri-file.")


def accounts_from(images=(), uris=(), runner=None) -> list[dict]:
    """Decode every export screenshot AND/OR raw migration URI into one de-duplicated account list.
    The `uris` path needs no decoder, so it works on every OS with zero extra dependencies."""
    texts = [read_qr(p, runner) for p in images] + list(uris)
    out, seen = [], set()
    for t in texts:
        for a in decode_migration_uri(t):
            key = (a["issuer"], a["name"], a["seed"])
            if key not in seen:
                seen.add(key)
                out.append(a)
    return out
