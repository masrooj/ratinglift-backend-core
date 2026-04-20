"""Admin read APIs for audit logs, admin action logs, login attempts, and IP blocklist.

All routes are mounted under ``/api/v1/admin`` and gated by:
* The middleware (rejects non-admin tokens on ``/api/v1/admin/*``).
* A per-route ``require_role`` guard (defence in depth).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.admin_action_log import AdminActionLog
from app.db.models.audit_log import AuditLog
from app.db.models.ip_blocklist import IpBlocklist
from app.db.models.login_attempt import LoginAttempt
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.modules.admin.schemas import (
    AdminActionItem,
    AdminActionList,
    AuditLogItem,
    AuditLogList,
    IpBlockCreateRequest,
    IpBlocklistItem,
    IpBlocklistList,
    LoginAttemptItem,
    LoginAttemptList,
    SimpleMessage,
)
from app.modules.audit import log_admin_action
from app.modules.auth.service import oauth2_scheme, require_role
from app.modules.security import block_ip, unblock_ip

# Roles that can READ audit/security data.
READ_ROLES = [
    UserRole.SUPER_ADMIN.value,
    UserRole.COMPLIANCE_ADMIN.value,
    UserRole.SUPPORT_ADMIN.value,
]

# Roles that can MODIFY the IP blocklist (block/unblock).
MUTATE_ROLES = [
    UserRole.SUPER_ADMIN.value,
    UserRole.COMPLIANCE_ADMIN.value,
]

admin_audit_router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-audit"],
    dependencies=[Depends(oauth2_scheme)],
)


# --------------------------- audit_logs ---------------------------


@admin_audit_router.get("/audit-logs", response_model=AuditLogList)
def list_audit_logs(
    actor_id: UUID | None = Query(default=None),
    entity: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=128),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(READ_ROLES)),
):
    q = db.query(AuditLog)
    if actor_id is not None:
        q = q.filter(AuditLog.actor_id == actor_id)
    if entity:
        q = q.filter(AuditLog.entity == entity)
    if action:
        q = q.filter(AuditLog.action == action)
    if since is not None:
        q = q.filter(AuditLog.timestamp >= since)
    if until is not None:
        q = q.filter(AuditLog.timestamp <= until)

    total = q.with_entities(func.count(AuditLog.id)).scalar() or 0
    rows = (
        q.order_by(AuditLog.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [
        AuditLogItem(
            id=r.id,
            actor_id=r.actor_id,
            actor_type=r.actor_type.value if hasattr(r.actor_type, "value") else str(r.actor_type),
            action=r.action,
            entity=r.entity,
            entity_id=r.entity_id,
            before_value=r.before_value,
            after_value=r.after_value,
            ip_address=r.ip_address,
            timestamp=r.timestamp,
        )
        for r in rows
    ]
    return AuditLogList(total=total, limit=limit, offset=offset, items=items)


# --------------------------- admin_action_logs ---------------------------


@admin_audit_router.get("/admin-actions", response_model=AdminActionList)
def list_admin_actions(
    admin_id: UUID | None = Query(default=None),
    action: str | None = Query(default=None, max_length=128),
    target_entity: str | None = Query(default=None, max_length=64),
    target_tenant_id: UUID | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(READ_ROLES)),
):
    q = db.query(AdminActionLog)
    if admin_id is not None:
        q = q.filter(AdminActionLog.admin_id == admin_id)
    if action:
        q = q.filter(AdminActionLog.action == action)
    if target_entity:
        q = q.filter(AdminActionLog.target_entity == target_entity)
    if target_tenant_id is not None:
        q = q.filter(AdminActionLog.target_tenant_id == target_tenant_id)
    if since is not None:
        q = q.filter(AdminActionLog.timestamp >= since)
    if until is not None:
        q = q.filter(AdminActionLog.timestamp <= until)

    total = q.with_entities(func.count(AdminActionLog.id)).scalar() or 0
    rows = (
        q.order_by(AdminActionLog.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [AdminActionItem.model_validate(r, from_attributes=True) for r in rows]
    return AdminActionList(total=total, limit=limit, offset=offset, items=items)


# --------------------------- login_attempts ---------------------------


@admin_audit_router.get("/login-attempts", response_model=LoginAttemptList)
def list_login_attempts(
    email: str | None = Query(default=None, max_length=320),
    ip_address: str | None = Query(default=None, max_length=64),
    success: bool | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(READ_ROLES)),
):
    q = db.query(LoginAttempt)
    if email:
        q = q.filter(LoginAttempt.email == email.lower())
    if ip_address:
        q = q.filter(LoginAttempt.ip_address == ip_address)
    if success is not None:
        q = q.filter(LoginAttempt.success.is_(success))
    if since is not None:
        q = q.filter(LoginAttempt.timestamp >= since)
    if until is not None:
        q = q.filter(LoginAttempt.timestamp <= until)

    total = q.with_entities(func.count(LoginAttempt.id)).scalar() or 0
    rows = (
        q.order_by(LoginAttempt.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [LoginAttemptItem.model_validate(r, from_attributes=True) for r in rows]
    return LoginAttemptList(total=total, limit=limit, offset=offset, items=items)


# --------------------------- ip_blocklist ---------------------------


@admin_audit_router.get("/ip-blocklist", response_model=IpBlocklistList)
def list_ip_blocklist(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(READ_ROLES)),
):
    rows = (
        db.query(IpBlocklist).order_by(IpBlocklist.blocked_at.desc()).all()
    )
    items = [IpBlocklistItem.model_validate(r, from_attributes=True) for r in rows]
    return IpBlocklistList(total=len(items), items=items)


@admin_audit_router.post(
    "/ip-blocklist",
    response_model=IpBlocklistItem,
    status_code=status.HTTP_201_CREATED,
)
def manually_block_ip(
    payload: IpBlockCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(MUTATE_ROLES)),
):
    row = block_ip(
        db,
        payload.ip_address,
        reason=payload.reason or "manual_admin_block",
        duration_seconds=payload.duration_seconds,
    )
    log_admin_action(
        db,
        admin_id=admin.id,
        action="ip.block",
        target_entity="ip",
        after_value={
            "ip_address": payload.ip_address,
            "reason": payload.reason,
            "duration_seconds": payload.duration_seconds,
        },
        request=request,
    )
    db.commit()
    db.refresh(row)
    return IpBlocklistItem.model_validate(row, from_attributes=True)


@admin_audit_router.delete(
    "/ip-blocklist/{ip_address:path}",
    response_model=SimpleMessage,
)
def manually_unblock_ip(
    ip_address: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(MUTATE_ROLES)),
):
    if not ip_address:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ip_address required")
    removed = unblock_ip(db, ip_address)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IP is not blocked")
    log_admin_action(
        db,
        admin_id=admin.id,
        action="ip.unblock",
        target_entity="ip",
        before_value={"ip_address": ip_address},
        request=request,
    )
    db.commit()
    return SimpleMessage(message=f"IP {ip_address} unblocked.")
