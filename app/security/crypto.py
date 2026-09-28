"""Symmetric encryption for secrets at rest (third-party API keys, alert destinations)."""
from __future__ import annotations

import base64
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class EncryptionUnavailable(RuntimeError):
    pass


def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.encryption_key
    if not key:
        if settings.is_production:
            raise EncryptionUnavailable("ENCRYPTION_KEY is not configured")
        # Development only: derive a stable key from SECRET_KEY so local setups work.
        key = base64.urlsafe_b64encode(hashlib.sha256(("enc:" + settings.secret_key).encode()).digest()).decode()
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError):
        # Any other high-entropy string (e.g. a platform-generated secret) is stretched into a Fernet key.
        derived = base64.urlsafe_b64encode(hashlib.sha256(("fernet:" + str(key)).encode()).digest())
        return Fernet(derived)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # wrong key or tampered ciphertext
        raise EncryptionUnavailable("Unable to decrypt stored secret (key rotated or data tampered)") from exc


def hint(secret: str) -> str:
    """Safe display hint: never more than the last 4 characters."""
    return ("•••• " + secret[-4:]) if len(secret) > 8 else "••••"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
