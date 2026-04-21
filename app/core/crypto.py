"""Symmetric encryption helpers for sensitive at-rest secrets.

Currently used by the property-connector module to protect third-party
``api_secret`` credentials before persisting them. The cipher is Fernet
(AES-128-CBC + HMAC-SHA256) keyed off a 32-byte value derived from
``settings.connector_secret_key`` (or, in dev, the JWT secret).

Production deployments MUST set ``CONNECTOR_SECRET_KEY`` to a stable,
high-entropy value; rotating it without re-encrypting existing rows will
make stored secrets undecryptable.
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _derive_key(material: str) -> bytes:
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    material = (
        getattr(settings, "connector_secret_key", None)
        or settings.jwt_secret
    )
    return Fernet(_derive_key(material))


def encrypt_secret(plaintext: str) -> str:
    """Return a URL-safe base64 ciphertext for ``plaintext``."""
    if plaintext is None:
        raise ValueError("plaintext is required")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Reverse of :func:`encrypt_secret`. Raises ``InvalidToken`` on tamper."""
    if ciphertext is None:
        raise ValueError("ciphertext is required")
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


__all__ = ["encrypt_secret", "decrypt_secret", "InvalidToken"]
