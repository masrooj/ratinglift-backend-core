"""OTP storage in Redis, scoped by purpose.

Purposes:
- "login"          — login-time MFA challenge
- "channel:email"  — verifying a newly added MFA email
- "channel:phone"  — verifying a newly added MFA phone
"""
from __future__ import annotations

import random
from uuid import UUID

import redis

LOGIN_PURPOSE = "login"
CHANNEL_EMAIL_PURPOSE = "channel:email"
CHANNEL_PHONE_PURPOSE = "channel:phone"


def _otp_key(user_id: UUID, purpose: str) -> str:
    return f"auth:mfa:otp:{purpose}:{user_id}"


def generate_otp_code() -> str:
    return f"{random.SystemRandom().randint(0, 999999):06d}"


def generate_and_store_otp(
    redis_client: redis.Redis,
    user_id: UUID,
    ttl_seconds: int,
    purpose: str = LOGIN_PURPOSE,
) -> str:
    otp = generate_otp_code()
    redis_client.setex(_otp_key(user_id, purpose), ttl_seconds, otp)
    return otp


def verify_stored_otp(
    redis_client: redis.Redis,
    user_id: UUID,
    otp: str,
    purpose: str = LOGIN_PURPOSE,
) -> bool:
    key = _otp_key(user_id, purpose)
    value = redis_client.get(key)
    if value is None:
        return False

    stored = value.decode("utf-8") if isinstance(value, bytes) else str(value)
    if stored != otp:
        return False

    redis_client.delete(key)
    return True
