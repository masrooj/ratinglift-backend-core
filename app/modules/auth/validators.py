"""Shared validators for auth schemas.

These functions raise ``ValueError`` so they can be used as Pydantic v2
``field_validator`` / ``AfterValidator`` hooks and surface as 422 responses
with a clear message.
"""
from __future__ import annotations

import re


# ---------------- password ----------------

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
_PASSWORD_UPPER = re.compile(r"[A-Z]")
_PASSWORD_LOWER = re.compile(r"[a-z]")
_PASSWORD_DIGIT = re.compile(r"\d")
# Symbol encouraged but not required so existing accounts are not broken.


def validate_password_strength(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Password must be a string")
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(value) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")
    if " " in value:
        raise ValueError("Password must not contain spaces")
    if not _PASSWORD_UPPER.search(value):
        raise ValueError("Password must contain at least one uppercase letter")
    if not _PASSWORD_LOWER.search(value):
        raise ValueError("Password must contain at least one lowercase letter")
    if not _PASSWORD_DIGIT.search(value):
        raise ValueError("Password must contain at least one digit")
    return value


# ---------------- email ----------------

# Small but effective block-list of disposable-email providers.
DISPOSABLE_EMAIL_DOMAINS = frozenset(
    {
        "mailinator.com",
        "tempmail.com",
        "10minutemail.com",
        "guerrillamail.com",
        "trashmail.com",
        "yopmail.com",
        "dispostable.com",
        "throwawaymail.com",
        "getnada.com",
        "mailnesia.com",
        "sharklasers.com",
        "maildrop.cc",
        "fakeinbox.com",
        "temp-mail.org",
    }
)


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Email must be a string")
    email = value.strip().lower()
    if "@" not in email:
        raise ValueError("Email is not valid")
    local, _, domain = email.rpartition("@")
    if not local or not domain:
        raise ValueError("Email is not valid")
    if "+" in local:
        raise ValueError("Plus-aliased email addresses are not allowed")
    if domain in DISPOSABLE_EMAIL_DOMAINS:
        raise ValueError("Disposable email providers are not allowed")
    return email


# ---------------- phone (E.164) ----------------

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def validate_phone_e164(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Phone must be a string")
    phone = value.strip().replace(" ", "").replace("-", "")
    if not _E164_RE.match(phone):
        raise ValueError("Phone must be in E.164 format, e.g. +14155551234")
    return phone


# ---------------- tenant slug ----------------

_TENANT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def normalize_tenant_name(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    slug = raw.lower().replace("_", "-").replace(" ", "-")
    # Drop characters that aren't valid in a slug (keeps a-z, 0-9, and hyphens).
    slug = re.sub(r"[^a-z0-9-]+", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug or not _TENANT_SLUG_RE.match(slug):
        raise ValueError(
            "Tenant name must start with a letter or digit and contain only a-z, 0-9, and hyphens (2-64 chars)."
        )
    return slug


# ---------------- OTP / TOTP ----------------

_SIX_DIGIT_RE = re.compile(r"^\d{6}$")


def validate_six_digit_code(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Code must be a string")
    code = value.strip().replace(" ", "")
    if not _SIX_DIGIT_RE.match(code):
        raise ValueError("Code must be exactly 6 digits")
    return code
