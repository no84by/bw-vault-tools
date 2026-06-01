"""Builders for Bitwarden export item dicts used in tests."""


def login(id, name="site", uri="https://site.com", username="u", password="p",
          fido2=None, folder_id=None, revision="2026-01-01T00:00:00.000Z",
          creation="2026-01-01T00:00:00.000Z", notes=None, organization_id=None):
    uris = [{"uri": uri, "match": None}] if uri is not None else None
    return {"id": id, "organizationId": organization_id, "folderId": folder_id, "type": 1,
            "name": name, "notes": notes, "revisionDate": revision, "creationDate": creation,
            "login": {"uris": uris, "username": username, "password": password,
                      "totp": None, "fido2Credentials": fido2 or []}}


def passkey_login(id, **kw):
    return login(id, fido2=[{"credentialId": "c-" + id, "rpId": "site.com"}], **kw)


def no_uri_login(id, **kw):
    return login(id, uri=None, **kw)


def note(id, name="note", text="secret"):
    return {"id": id, "organizationId": None, "folderId": None, "type": 2,
            "name": name, "notes": text, "secureNote": {"type": 0}}


def ssh_key(id, name="key"):
    return {"id": id, "organizationId": None, "folderId": None, "type": 5, "name": name,
            "notes": None, "sshKey": {"privateKey": "PRIV", "publicKey": "ssh-ed25519 AAAA",
                                       "keyFingerprint": "SHA256:x"}}


def unknown_type(id, type=99, name="weird"):
    return {"id": id, "organizationId": None, "folderId": None, "type": type, "name": name}


def vault(items, folders=None):
    return {"encrypted": False, "folders": folders or [], "items": items}
