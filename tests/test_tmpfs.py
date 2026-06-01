import os

from bw_vault_tools import tmpfs


def test_tmpfs_file_under_scratch_root_and_shreds():
    seen = {}
    with tmpfs.tmpfs_file(suffix=".json") as path:
        assert os.path.dirname(path) == tmpfs.scratch_root()   # /dev/shm on Linux, OS temp elsewhere
        with open(path, "w") as f:
            f.write("secret")
        seen["p"] = path
        assert os.path.exists(path)
    assert not os.path.exists(seen["p"])


def test_scratch_root_is_a_dir():
    assert os.path.isdir(tmpfs.scratch_root())
