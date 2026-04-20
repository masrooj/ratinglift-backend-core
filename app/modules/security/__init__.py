"""Security tracking primitives: failed-login tracking and IP blocking."""
from app.modules.security.ip_blocking import (
    BLOCK_DURATION_SECONDS,
    DEFAULT_FAILURE_THRESHOLD,
    block_ip,
    ensure_ip_allowed,
    is_ip_blocked,
    register_failed_attempt_for_ip,
    unblock_ip,
)
from app.modules.security.login_tracking import (
    record_login_attempt,
    recent_failures_for_email,
    recent_failures_for_ip,
)

__all__ = [
    "BLOCK_DURATION_SECONDS",
    "DEFAULT_FAILURE_THRESHOLD",
    "block_ip",
    "ensure_ip_allowed",
    "is_ip_blocked",
    "register_failed_attempt_for_ip",
    "unblock_ip",
    "record_login_attempt",
    "recent_failures_for_email",
    "recent_failures_for_ip",
]
