from bw_vault_tools import keyprovider, snapshot


def kp():
    return keyprovider.PassphraseProvider("s")


def test_empty_snapshot_roundtrip(tmp_path):
    s = snapshot.Snapshot()
    path = str(tmp_path / "S.enc")
    snapshot.save(s, path, kp())
    assert snapshot.load(path, kp()).entries == {}


def test_record_and_lookup_pairing(tmp_path):
    s = snapshot.Snapshot()
    s.record(link_id="L1", id_on_a="a1", id_on_b="b1", content=("x",), canonical={"id": "a1"})
    assert s.by_a("a1").id_on_b == "b1"
    assert s.by_b("b1").id_on_a == "a1"
    path = str(tmp_path / "S.enc")
    snapshot.save(s, path, kp())
    raw = open(path, "rb").read()
    assert b'"a1"' not in raw
    assert snapshot.load(path, kp()).by_a("a1").id_on_b == "b1"
