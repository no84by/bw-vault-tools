import base64
import urllib.parse

from bw_vault_tools import totp_source


def _ev(v):                                  # encode a protobuf varint
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | 0x80 if v else b)
        if not v:
            return bytes(out)


def _fld(num, wt, payload):
    return _ev(num << 3 | wt) + payload


def _otp(secret, name, issuer, typ=2):       # one OtpParameters message
    return (_fld(1, 2, _ev(len(secret)) + secret) + _fld(2, 2, _ev(len(name)) + name.encode())
            + _fld(3, 2, _ev(len(issuer)) + issuer.encode()) + _fld(6, 0, _ev(typ)))


def _migration(otps):
    blob = b"".join(_fld(1, 2, _ev(len(o)) + o) for o in otps)
    return "otpauth-migration://offline?data=" + urllib.parse.quote(base64.b64encode(blob).decode())


def test_decode_migration_totp():
    secret = b"\x12\x34\x56\x78\x9a\xbc\xde\xf0"
    accts = totp_source.decode_migration_uri(_migration([_otp(secret, "alice@example.com", "Example")]))
    assert len(accts) == 1
    a = accts[0]
    assert a["issuer"] == "Example" and a["name"] == "alice@example.com"
    assert a["seed"] == base64.b32encode(secret).decode().rstrip("=")


def test_decode_skips_hotp_entries():
    assert totp_source.decode_migration_uri(_migration([_otp(b"AAAA", "x", "Y", typ=1)])) == []


def test_decode_handles_non_migration_text():
    assert totp_source.decode_migration_uri("not a migration uri") == []


def test_accounts_from_uri_needs_no_decoder():
    uri = _migration([_otp(b"\x01\x02\x03\x04\x05", "u", "Svc")])
    out = totp_source.accounts_from(uris=[uri])
    assert [a["issuer"] for a in out] == ["Svc"]


def test_read_qr_uses_injected_runner():
    got = totp_source.read_qr("img.png", runner=lambda a: "otpauth-migration://offline?data=XXX\n")
    assert got == "otpauth-migration://offline?data=XXX"
