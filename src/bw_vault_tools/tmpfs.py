"""Plaintext-safe scratch in /dev/shm with shred-on-exit (mirrors with-creds.sh)."""
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager

SHM = "/dev/shm"


def _shred(path: str) -> None:
    try:
        subprocess.run(["shred", "-u", path], check=False)
    except FileNotFoundError:
        pass
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


@contextmanager
def tmpfs_file(suffix: str = ""):
    fd, path = tempfile.mkstemp(prefix="bwvt-", suffix=suffix, dir=SHM)
    os.close(fd)
    try:
        yield path
    finally:
        _shred(path)


@contextmanager
def tmpfs_dir():
    path = tempfile.mkdtemp(prefix="bwvt-", dir=SHM)
    try:
        yield path
    finally:
        for root, _, files in os.walk(path):
            for fn in files:
                _shred(os.path.join(root, fn))
        shutil.rmtree(path, ignore_errors=True)
