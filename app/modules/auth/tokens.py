"""Refresh-token + JWT jti helpers.

Refresh tokens are opaque base64url strings. Only their SHA-256 hash is
persisted (on ``LoginSession.refresh_token_hash``). The plaintext is only
shown to the client once (at login) and again on rotation.
"""
from __future__ import annotations

import hashlib
import secrets
from uuid import uuid4


def new_jti() -> str:
    return uuid4().hex


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
