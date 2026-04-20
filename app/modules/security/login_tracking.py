"""Persistent tracking of every login attempt (success/failure)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.login_attempt import LoginAttempt

logger = get_logger(__name__)


def record_login_attempt(
    db: Session,
    *,
    email: str,
    ip_address: str | None,
    success: bool,
    reason: str | None = None,
    user_agent: str | None = None,
    flush: bool = True,
) -> LoginAttempt:
    """Insert a row into ``login_attempts``.

    Caller controls commit. Email is stored lower-cased to make lookups
    deterministic.
    """
    attempt = LoginAttempt(
        email=(email or "").lower(),
        ip_address=ip_address,
        user_agent=user_agent,
        success=success,
        reason=reason,
    )
    db.add(attempt)
    if flush:
        try:
            db.flush()
        except Exception:  # noqa: BLE001
            logger.exception("login_attempt_flush_failed email=%s", email)
            raise
    logger.info(
        "login_attempt email=%s ip=%s success=%s reason=%s",
        attempt.email,
        ip_address,
        success,
        reason,
    )
    return attempt


def _since(window_minutes: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=window_minutes)


def recent_failures_for_email(
    db: Session, email: str, *, window_minutes: int = 15
) -> int:
    return (
        db.query(func.count(LoginAttempt.id))
        .filter(
            LoginAttempt.email == (email or "").lower(),
            LoginAttempt.success.is_(False),
            LoginAttempt.timestamp >= _since(window_minutes),
        )
        .scalar()
        or 0
    )


def recent_failures_for_ip(
    db: Session, ip_address: str, *, window_minutes: int = 15
) -> int:
    if not ip_address:
        return 0
    return (
        db.query(func.count(LoginAttempt.id))
        .filter(
            LoginAttempt.ip_address == ip_address,
            LoginAttempt.success.is_(False),
            LoginAttempt.timestamp >= _since(window_minutes),
        )
        .scalar()
        or 0
    )
