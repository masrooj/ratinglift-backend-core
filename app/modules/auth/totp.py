"""TOTP (RFC 6238) helpers using pyotp."""
from __future__ import annotations

import pyotp


DEFAULT_ISSUER = "RatingLift"


def create_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(user_email: str, secret: str, issuer: str = DEFAULT_ISSUER) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=user_email, issuer_name=issuer)


def verify(secret: str, code: str, valid_window: int = 1) -> bool:
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=valid_window)
    except Exception:  # noqa: BLE001
        return False
