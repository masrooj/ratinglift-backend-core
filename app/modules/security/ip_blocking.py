"""Basic IP blocking for authentication endpoints.

When an IP exceeds ``DEFAULT_FAILURE_THRESHOLD`` failed login attempts
within the configured tracking window, it is added to ``ip_blocklist`` and
subsequent authentication attempts from that IP are rejected with HTTP 403
until ``expires_at`` passes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.ip_blocklist import IpBlocklist
from app.modules.security.login_tracking import recent_failures_for_ip

logger = get_logger(__name__)

DEFAULT_FAILURE_THRESHOLD = 20
BLOCK_DURATION_SECONDS = 60 * 60  # 1 hour


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_ip_blocked(db: Session, ip_address: str | None) -> bool:
    if not ip_address:
        return False
    row = (
        db.query(IpBlocklist)
        .filter(IpBlocklist.ip_address == ip_address)
        .first()
    )
    if not row:
        return False
    if row.expires_at is None:
        return True
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        # Expired — clean it up so callers don't see stale blocks.
        db.delete(row)
        db.flush()
        return False
    return True


def ensure_ip_allowed(db: Session, ip_address: str | None) -> None:
    """Raise HTTP 403 if the IP is currently on the blocklist."""
    if is_ip_blocked(db, ip_address):
        logger.warning("ip_blocked_request ip=%s", ip_address)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your IP address has been temporarily blocked due to repeated "
                "failed sign-in attempts. Please wait and try again later, or "
                "contact support if you believe this is a mistake."
            ),
        )


def block_ip(
    db: Session,
    ip_address: str,
    *,
    reason: str | None = None,
    failed_attempts: int = 0,
    duration_seconds: int = BLOCK_DURATION_SECONDS,
    flush: bool = True,
) -> IpBlocklist:
    expires_at = _now() + timedelta(seconds=duration_seconds) if duration_seconds else None
    row = (
        db.query(IpBlocklist)
        .filter(IpBlocklist.ip_address == ip_address)
        .first()
    )
    if row:
        row.reason = reason or row.reason
        row.failed_attempts = max(row.failed_attempts or 0, failed_attempts)
        row.blocked_at = _now()
        row.expires_at = expires_at
    else:
        row = IpBlocklist(
            ip_address=ip_address,
            reason=reason,
            failed_attempts=failed_attempts,
            expires_at=expires_at,
        )
        db.add(row)
    if flush:
        db.flush()
    logger.warning(
        "ip_blocked ip=%s reason=%s failed_attempts=%s expires_at=%s",
        ip_address,
        reason,
        failed_attempts,
        expires_at,
    )
    return row


def unblock_ip(db: Session, ip_address: str, *, flush: bool = True) -> bool:
    row = (
        db.query(IpBlocklist)
        .filter(IpBlocklist.ip_address == ip_address)
        .first()
    )
    if not row:
        return False
    db.delete(row)
    if flush:
        db.flush()
    logger.info("ip_unblocked ip=%s", ip_address)
    return True


def register_failed_attempt_for_ip(
    db: Session,
    ip_address: str | None,
    *,
    threshold: int = DEFAULT_FAILURE_THRESHOLD,
    window_minutes: int = 15,
    reason: str = "failed_login_threshold_exceeded",
) -> bool:
    """If the IP exceeded ``threshold`` failures in the window, block it.

    Returns ``True`` when the IP was newly (or re-) blocked.
    """
    if not ip_address:
        return False
    failures = recent_failures_for_ip(db, ip_address, window_minutes=window_minutes)
    if failures < threshold:
        return False
    block_ip(
        db,
        ip_address,
        reason=reason,
        failed_attempts=failures,
    )
    return True
