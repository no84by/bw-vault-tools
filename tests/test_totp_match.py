from bw_vault_tools import cli_totp, keyprovider
from bw_vault_tools import totp_match as tm


def login(id, name, user, uri, totp=None):
    return {"id": id, "type": 1, "name": name,
            "login": {"username": user, "uris": [{"uri": uri}], "totp": totp}}


def test_set_when_login_has_no_totp():
    plan = tm.build_totp_plan([{"issuer": "Discord", "name": "u@x.com", "seed": "ABCD"}],
                              [login("1", "discord.com", "u@x.com", "https://discord.com")])
    assert isinstance(plan[0], tm.SetTotp) and plan[0].item_id == "1"


def test_skip_when_seed_identical():
    plan = tm.build_totp_plan([{"issuer": "Discord", "name": "u@x.com", "seed": "ABCD"}],
                              [login("1", "discord.com", "u@x.com", "https://discord.com", totp="abcd")])
    assert isinstance(plan[0], tm.Skip)


def test_duplicate_when_seed_differs_never_overwrite():
    plan = tm.build_totp_plan([{"issuer": "Discord", "name": "u@x.com", "seed": "NEWSEED"}],
                              [login("1", "discord.com", "u@x.com", "https://discord.com", totp="OLDSEED")])
    assert isinstance(plan[0], tm.Duplicate) and plan[0].seed == "NEWSEED"


def test_ambiguous_when_multiple_or_no_match():
    items = [login("1", "auth.openai.com", "a@x.com", "https://auth.openai.com"),
             login("2", "auth0.openai.com", "a@x.com", "https://auth0.openai.com")]
    multi = tm.build_totp_plan([{"issuer": "OpenAI", "name": "a@x.com", "seed": "S"}], items)
    assert isinstance(multi[0], tm.Ambiguous) and len(multi[0].candidates) == 2
    none = tm.build_totp_plan([{"issuer": "Nope", "name": "x", "seed": "S"}], items)
    assert isinstance(none[0], tm.Ambiguous) and none[0].candidates == []


def test_normalize_seed_accepts_uri_and_spaces():
    assert tm.normalize_seed("otpauth://totp/X?secret=abcd&issuer=Y") == "ABCD"
    assert tm.normalize_seed("ab cd") == "ABCD"


class FakeProfile:
    def __init__(self, items):
        self._items = {i["id"]: i for i in items}
        self.created = []

    def export(self):
        return {"items": list(self._items.values())}

    def edit(self, item_id, item):
        self._items[item_id] = item

    def create(self, item):
        new = dict(item, id="srv-" + str(len(self.created)))
        self._items[new["id"]] = new
        self.created.append(new)
        return new

    def delete(self, item_id, permanent=False):
        self._items.pop(item_id, None)


def test_run_totp_sets_missing_and_skips_unmatched(tmp_path):
    accts = [{"issuer": "Discord", "name": "u@x.com", "seed": "AAAA"},   # -> set
             {"issuer": "Ghost", "name": "g@x.com", "seed": "BBBB"}]     # -> ambiguous (no match)
    prof = FakeProfile([login("1", "discord.com", "u@x.com", "https://discord.com")])
    res = cli_totp.run_totp(prof, accts, run_dir=str(tmp_path / "r"), apply=True,
                            key_provider=keyprovider.PassphraseProvider("p"), picker=None)
    assert res.set == 1 and res.ambiguous == 1
    assert prof._items["1"]["login"]["totp"] == "AAAA"
