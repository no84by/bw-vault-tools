"""bw-totp entrypoint: import Google-Authenticator TOTP seeds into a vault.

Decode a GA export screenshot (or a pasted migration URI), match each account to a vault login,
and set the seed where it's missing — skipping identical ones, never overwriting a different one
(creates a duplicate instead), and surfacing anything ambiguous for the operator to resolve.
Every apply is journalled and reversible with --undo.
"""
import sys
from collections import Counter
from dataclasses import dataclass

from . import checkpoint, keyprovider, totp_match, totp_source


@dataclass
class TotpResult:
    set: int = 0
    created: int = 0
    skipped: int = 0
    ambiguous: int = 0


def tty_picker(op):
    """Resolve an Ambiguous op interactively. Returns ('set', item_id) | ('create', (name,user,uri))
    | None (skip). Non-interactive -> None (skipped, reported)."""
    if not sys.stdin.isatty():
        return None
    a = op.account
    print(f"\nResolve TOTP for: {a.get('issuer')!r} / {a.get('name')!r}")
    for i, (_id, nm, us) in enumerate(op.candidates):
        print(f"  [{i}] {nm}  (user={us})")
    print("  [c] create a new login    [s] skip")
    ans = input("pick: ").strip().lower()
    if ans == "c":
        nm = input("  name: ").strip() or (a.get("issuer") or a.get("name"))
        uri = input("  uri: ").strip()
        return ("create", (nm, a.get("name"), uri))
    if ans.isdigit() and int(ans) < len(op.candidates):
        return ("set", op.candidates[int(ans)][0])
    return None


def _set_totp(prof, run, item, seed):
    login = dict(item.get("login") or {})
    if login.get("totp"):                              # safety: never overwrite -> duplicate instead
        uri = (login.get("uris") or [{}])[0].get("uri") or ""
        return _create_totp(prof, run, item.get("name"), login.get("username"), uri, seed)
    new = dict(item)
    login["totp"] = seed
    new["login"] = login
    run.record(item["id"], {"action": "edit", "item_id": item["id"], "item": item})
    prof.edit(item["id"], new)


def _create_totp(prof, run, name, username, uri, seed):
    item = {"type": 1, "name": name, "notes": None, "fields": [],
            "login": {"uris": [{"uri": uri, "match": None}] if uri else None,
                      "username": username or None, "password": None, "totp": seed,
                      "fido2Credentials": []}}
    created = prof.create(item)
    run.record(created["id"], {"action": "delete", "item_id": created["id"]})


def run_totp(prof, accounts, run_dir="/dev/shm/bwvt-totp", apply=True, key_provider=None, picker=None):
    if apply and key_provider is None:
        raise ValueError("key_provider required when apply=True (reversibility)")
    items = prof.export().get("items", [])
    by_id = {it["id"]: it for it in items}
    plan = totp_match.build_totp_plan(accounts, items)
    c = Counter(op.kind for op in plan)
    print(f"totp plan: {c['set']} set, {c['skip']} already-present, {c['duplicate']} duplicate, "
          f"{c['ambiguous']} need-decision (of {len(plan)} account(s)).")
    for op in plan:
        if op.kind == "set":
            print(f"  SET   {op.item_name}")
        elif op.kind == "duplicate":
            print(f"  DUP   {op.name} (existing TOTP differs -> new item, never overwritten)")
        elif op.kind == "ambiguous":
            tag = "no login match" if not op.candidates else f"{len(op.candidates)} candidates"
            print(f"  ?     {op.account.get('issuer') or op.account.get('name')!r} "
                  f"/ {op.account.get('name')!r}  [{tag}]")
    res = TotpResult(skipped=c["skip"])
    if not apply:
        print("[--plan] dry-run; no changes.")
        return res
    run = checkpoint.RunDir(run_dir, key_provider)
    for op in plan:
        if op.kind == "set":
            _set_totp(prof, run, by_id[op.item_id], op.seed)
            res.set += 1
        elif op.kind == "duplicate":
            _create_totp(prof, run, op.name, op.username, op.uri, op.seed)
            res.created += 1
        elif op.kind == "ambiguous":
            choice = picker(op) if picker else None
            if choice and choice[0] == "set":
                _set_totp(prof, run, by_id[choice[1]], op.account["seed"])
                res.set += 1
            elif choice and choice[0] == "create":
                nm, us, uri = choice[1]
                _create_totp(prof, run, nm, us, uri, op.account["seed"])
                res.created += 1
            else:
                res.ambiguous += 1
    return res


def undo(prof, run_dir, key_provider) -> int:
    run = checkpoint.RunDir(run_dir, key_provider)
    n = 0
    for inv in run.undo_plan():
        if inv["action"] == "delete":
            prof.delete(inv["item_id"])
        elif inv["action"] == "edit":
            prof.edit(inv["item_id"], inv["item"])
        n += 1
    return n


def main() -> int:
    import argparse
    import getpass
    import os
    from . import bootstrap, bw_adapter
    bootstrap.ensure_cryptography()
    bootstrap.ensure_bw()
    ap = argparse.ArgumentParser(prog="bw-totp")
    ap.add_argument("--vault", required=True, help="profile label (for prompts)")
    ap.add_argument("--appdata", required=True, help="BITWARDENCLI_APPDATA_DIR for the profile")
    ap.add_argument("--image", action="append", default=[], metavar="PNG",
                    help="Google Authenticator export QR screenshot (repeatable)")
    ap.add_argument("--uri", action="append", default=[], metavar="OTPAUTH_MIGRATION",
                    help="otpauth-migration:// text, e.g. from decoding the QR yourself (repeatable)")
    ap.add_argument("--uri-file", action="append", default=[], metavar="PATH",
                    help="file containing a migration URI (repeatable)")
    ap.add_argument("--apply", action="store_true", help="apply (default: --plan dry-run)")
    ap.add_argument("--undo", metavar="RUN_DIR", help="reverse a prior import from its journal")
    args = ap.parse_args()

    session = os.environ.get("BW_SESSION") or _unlock(args.appdata)
    prof = bw_adapter.BwProfile(args.appdata, session)

    if args.undo:
        n = undo(prof, args.undo,
                 keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: ")))
        print(f"undo: reversed {n} op(s).")
        return 0

    uris = list(args.uri) + [open(p).read() for p in args.uri_file]
    accounts = totp_source.accounts_from(images=args.image, uris=uris)
    if not accounts:
        print("No TOTP accounts found. Pass an export screenshot (--image) or a migration URI (--uri).")
        return 1

    if args.apply:
        kp = keyprovider.PassphraseProvider(getpass.getpass("snapshot passphrase: "))
        rd = checkpoint.new_run_dir("totp")
        res = run_totp(prof, accounts, run_dir=rd, apply=True, key_provider=kp, picker=tty_picker)
        print(f"done: {res.set} set, {res.created} created, {res.skipped} already-present, "
              f"{res.ambiguous} unresolved.")
        print(f'reversible: bw-totp --undo "{rd}"')
    else:
        run_totp(prof, accounts, apply=False)
    return 0


def _unlock(appdata):
    import getpass
    import os
    import subprocess
    env = dict(os.environ, BITWARDENCLI_APPDATA_DIR=appdata)
    pw = getpass.getpass("bw master password: ")
    return subprocess.run(["bw", "unlock", "--raw"], input=pw + "\n", text=True,
                          capture_output=True, check=True, env=env).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
