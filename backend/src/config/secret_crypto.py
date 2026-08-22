"""Fernet encryption for UI-entered provider credentials at rest.

Mirrors the Fernet convention in src/services/oauth_manager.py, but keyed by
MULDRO_CONFIG_ENCRYPTION_KEY so provider-credential secrets rotate independently
of the OAuth-token key.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from src.config.settings import get_settings


def _config_key() -> str:
    return get_settings().config_encryption_key


def _fernet() -> Fernet:
    key = _config_key()
    if not key:
        raise RuntimeError(
            "MULDRO_CONFIG_ENCRYPTION_KEY not set. Generate one: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def encrypt_secret(plaintext: str) -> str:
    """Return a Fernet ciphertext string for *plaintext*."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Return the plaintext for a Fernet *ciphertext* string."""
    return _fernet().decrypt(ciphertext.encode()).decode()


def try_decrypt_secret(ciphertext: str) -> str | None:
    """Decrypt, or None if *ciphertext* is not valid under the current master key.

    Distinguishes ONE unusable row (``InvalidToken`` -> None) from a missing or
    malformed MASTER key (``RuntimeError`` / ``ValueError``, which propagate). The
    second is a deployment-wide misconfiguration that app.py's boot guard is meant to
    catch loudly, and swallowing it here would remove the turn-time backstop that
    guard's own comment relies on when the DB is unreachable at startup.
    """
    try:
        return decrypt_secret(ciphertext)
    except InvalidToken:
        return None
