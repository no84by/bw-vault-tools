"""Pluggable encryption. Passphrase: scrypt -> AES-256-GCM (cryptography).
Blob: b"BWV1" | salt(16) | nonce(12) | ct. Tpm2 lands in Plan 2."""
import os
from abc import ABC, abstractmethod

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class KeyProvider(ABC):
    @abstractmethod
    def encrypt(self, data: bytes) -> bytes: ...
    @abstractmethod
    def decrypt(self, blob: bytes) -> bytes: ...


class PassphraseProvider(KeyProvider):
    MAGIC = b"BWV1"

    def __init__(self, passphrase: str):
        self._pw = passphrase.encode("utf-8")

    def _key(self, salt: bytes) -> bytes:
        return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(self._pw)

    def encrypt(self, data: bytes) -> bytes:
        salt, nonce = os.urandom(16), os.urandom(12)
        return self.MAGIC + salt + nonce + AESGCM(self._key(salt)).encrypt(nonce, data, None)

    def decrypt(self, blob: bytes) -> bytes:
        if blob[:4] != self.MAGIC:
            raise ValueError("not a bw-vault-tools blob")
        salt, nonce, ct = blob[4:20], blob[20:32], blob[32:]
        return AESGCM(self._key(salt)).decrypt(nonce, ct, None)


def from_config(mode: str, passphrase: str | None = None) -> KeyProvider:
    if mode == "passphrase":
        if not passphrase:
            raise ValueError("passphrase mode requires a passphrase")
        return PassphraseProvider(passphrase)
    if mode == "tpm2":
        raise NotImplementedError("tpm2 provider lands in Plan 2 (bw-sync)")
    raise ValueError(f"unknown key provider mode: {mode}")
