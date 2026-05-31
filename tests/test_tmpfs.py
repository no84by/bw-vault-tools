import os

from bw_vault_tools import tmpfs


def test_tmpfs_file_in_shm_and_shreds():
    seen = {}
    with tmpfs.tmpfs_file(suffix=".json") as path:
        assert path.startswith("/dev/shm/")
        with open(path, "w") as f:
            f.write("secret")
        seen["p"] = path
        assert os.path.exists(path)
    assert not os.path.exists(seen["p"])
