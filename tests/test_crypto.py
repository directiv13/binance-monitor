import pytest
from cryptography.fernet import Fernet

from binance_monitor.crypto import Crypto, DecryptionError, mask


def test_round_trip() -> None:
    c = Crypto(Fernet.generate_key().decode())
    token = c.encrypt("s3cret-value")
    assert token != "s3cret-value" and "s3cret" not in token
    assert c.decrypt(token) == "s3cret-value"


def test_wrong_key_fails() -> None:
    token = Crypto(Fernet.generate_key().decode()).encrypt("x")
    with pytest.raises(DecryptionError):
        Crypto(Fernet.generate_key().decode()).decrypt(token)


def test_mask() -> None:
    assert mask("abcdefghijklmnopqrstuvwxyz") == "abcd…wxyz"
    assert mask("short") == "*****"
