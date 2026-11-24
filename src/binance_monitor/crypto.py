"""Fernet encryption of API credentials and secret masking helpers."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class DecryptionError(Exception):
    """Raised when a stored value cannot be decrypted (wrong ENCRYPTION_KEY?)."""


class Crypto:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise DecryptionError("cannot decrypt value; is ENCRYPTION_KEY correct?") from exc


def mask(value: str, keep: int = 4) -> str:
    """Mask a secret for display: ``abcd…wxyz``."""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"
