import pytest
from cryptography.fernet import Fernet

from src.config import secret_crypto


def test_encrypt_decrypt_round_trip(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: key)
    token = secret_crypto.encrypt_secret("sk-test-123")
    assert token != "sk-test-123"  # ciphertext, not plaintext
    assert secret_crypto.decrypt_secret(token) == "sk-test-123"


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(secret_crypto, "_config_key", lambda: "")
    with pytest.raises(RuntimeError, match="MULDRO_CONFIG_ENCRYPTION_KEY"):
        secret_crypto.encrypt_secret("x")
