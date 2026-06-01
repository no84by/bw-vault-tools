"""Plaintext-safe scratch with secure delete on exit. Cross-platform:
RAM-backed /dev/shm on Linux; the OS temp dir on Windows/macOS. Uses `shred` where available,
else a best-effort overwrite-then-remove."""
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager


def scratch_root() -> str:
    """tmpfs (RAM) on Linux if present, otherwise the OS temp directory."""
    return "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()


def _shred(path: str) -> None:
    if shutil.which("shred"):
        try:
            subprocess.run(["shred", "-u", path], check=False)
        except OSError:
            pass
    if os.path.exists(path):
        try:                                  # best-effort overwrite where shred is unavailable
            size = os.path.getsize(path)
            with open(path, "r+b") as f:
                f.write(os.urandom(size))
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass
        try:
            os.remove(path)
        except OSError:
            pass


@contextmanager
def tmpfs_file(suffix: str = ""):
    fd, path = tempfile.mkstemp(prefix="bwvt-", suffix=suffix, dir=scratch_root())
    os.close(fd)
    try:
        yield path
    finally:
        _shred(path)


@contextmanager
def tmpfs_dir():
    path = tempfile.mkdtemp(prefix="bwvt-", dir=scratch_root())
    try:
        yield path
    finally:
        for root, _, files in os.walk(path):
            for fn in files:
                _shred(os.path.join(root, fn))
        shutil.rmtree(path, ignore_errors=True)
