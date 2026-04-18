"""Redis-backed single-use tokens for password reset and email verification."""
from __future__ import annotations

import secrets
from uuid import UUID

import redis


PASSWORD_RESET_PREFIX = "auth:pwreset"
EMAIL_VERIFY_PREFIX = "auth:email-verify"


def _key(prefix: str, token: str) -> str:
    return f"{prefix}:{token}"


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def issue_token(redis_client: redis.Redis, prefix: str, user_id: UUID, ttl_seconds: int) -> str:
    token = _generate_token()
    redis_client.setex(_key(prefix, token), ttl_seconds, str(user_id))
    return token


def consume_token(redis_client: redis.Redis, prefix: str, token: str) -> str | None:
    """Return the stored user_id (str) and delete the key. None if missing."""
    key = _key(prefix, token)
    value = redis_client.get(key)
    if value is None:
        return None
    redis_client.delete(key)
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def peek_token(redis_client: redis.Redis, prefix: str, token: str) -> str | None:
    value = redis_client.get(_key(prefix, token))
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)
