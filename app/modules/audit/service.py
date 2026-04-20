"""Reusable audit logging service.

Provides ``log_action`` (general) and ``log_admin_action`` (admin-only).

Logging standards enforced here:
* before/after values are stored as JSON (None allowed).
* Timestamps are UTC (DB ``timestamptz`` with ``now()`` default).
* ``actor_id`` is required for non-system events; system events use
  ``actor_type='system'`` and may pass ``actor_id=None``.
* The helper never commits — callers control transaction boundaries.
"""
from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.admin_action_log import AdminActionLog
from app.db.models.audit_log import ActorType, AuditLog

logger = get_logger(__name__)


def _coerce_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _coerce_actor_type(value: Any) -> ActorType:
    """Map free-form strings ("tenant"/"admin"/"user"/"system") to ActorType.

    The DB enum currently supports user/system; admin and tenant actors are
    persisted as ``user`` because they are real user rows. The original
    intent is preserved in the audit ``action`` and in admin_action_logs.
    """
    if isinstance(value, ActorType):
        return value
    if value is None:
        return ActorType.system
    text = str(value).strip().lower()
    if text in {"system", "service", "worker"}:
        return ActorType.system
    return ActorType.user


def _json_safe(value: Any) -> Any:
    """Best-effort coercion of arbitrary values into JSON-serialisable form."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # noqa: BLE001
            return str(value)
    if hasattr(value, "__dict__"):
        return {k: _json_safe(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def log_action(
    db: Session,
    *,
    actor_id: Any,
    actor_type: Any,
    action: str,
    entity: str,
    entity_id: Any = None,
    before_value: Any = None,
    after_value: Any = None,
    ip_address: str | None = None,
    request: Request | None = None,
    flush: bool = True,
) -> AuditLog:
    """Insert an ``audit_logs`` row.

    Caller is responsible for committing the surrounding transaction.

    ``request`` is optional; when provided, ``ip_address`` defaults to
    ``request.state.ip_address`` populated by ``RequestContextMiddleware``.
    """
    if request is not None and ip_address is None:
        ip_address = getattr(request.state, "ip_address", None)

    entry = AuditLog(
        actor_id=_coerce_uuid(actor_id),
        actor_type=_coerce_actor_type(actor_type),
        action=action,
        entity=entity,
        entity_id=_coerce_uuid(entity_id),
        before_value=_json_safe(before_value),
        after_value=_json_safe(after_value),
        ip_address=ip_address,
    )
    db.add(entry)
    if flush:
        try:
            db.flush()
        except Exception:  # noqa: BLE001 - best effort
            logger.exception("audit_log_flush_failed action=%s entity=%s", action, entity)
            raise
    logger.info(
        "audit action=%s entity=%s entity_id=%s actor_id=%s actor_type=%s ip=%s",
        action,
        entity,
        entity_id,
        actor_id,
        entry.actor_type.value,
        ip_address,
    )
    return entry


def log_admin_action(
    db: Session,
    *,
    admin_id: Any,
    action: str,
    target_entity: str | None = None,
    target_id: Any = None,
    target_tenant_id: Any = None,
    before_value: Any = None,
    after_value: Any = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_path: str | None = None,
    extra: Any = None,
    request: Request | None = None,
    flush: bool = True,
    mirror_to_audit_log: bool = True,
) -> AdminActionLog:
    """Insert an ``admin_action_logs`` row and optionally mirror to ``audit_logs``.

    Use this for: impersonation, billing changes, tenant suspension,
    data deletion, and any other privileged admin action.
    """
    if request is not None:
        if ip_address is None:
            ip_address = getattr(request.state, "ip_address", None)
        if user_agent is None:
            user_agent = getattr(request.state, "user_agent", None)
        if request_path is None:
            request_path = getattr(request.state, "request_path", None)

    coerced_admin_id = _coerce_uuid(admin_id)
    if coerced_admin_id is None:
        # Admin actions MUST have an actor; refuse silently-but-loudly.
        raise ValueError("log_admin_action requires a valid admin_id (UUID)")

    entry = AdminActionLog(
        admin_id=coerced_admin_id,
        action=action,
        target_entity=target_entity,
        target_id=_coerce_uuid(target_id),
        target_tenant_id=_coerce_uuid(target_tenant_id),
        before_value=_json_safe(before_value),
        after_value=_json_safe(after_value),
        ip_address=ip_address,
        user_agent=user_agent,
        request_path=request_path,
        extra=_json_safe(extra),
    )
    db.add(entry)

    if mirror_to_audit_log:
        log_action(
            db,
            actor_id=coerced_admin_id,
            actor_type="user",
            action=f"admin.{action}",
            entity=target_entity or "admin",
            entity_id=target_id,
            before_value=before_value,
            after_value=after_value,
            ip_address=ip_address,
            flush=False,
        )

    if flush:
        try:
            db.flush()
        except Exception:  # noqa: BLE001
            logger.exception("admin_action_log_flush_failed action=%s", action)
            raise

    logger.info(
        "admin_action action=%s admin_id=%s target_entity=%s target_id=%s ip=%s",
        action,
        admin_id,
        target_entity,
        target_id,
        ip_address,
    )
    return entry
