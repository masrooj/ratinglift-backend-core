"""Service layer for tenant property management.

All operations are tenant-scoped; cross-tenant access is impossible by
construction because every query is funnelled through ``filter_by_tenant``.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import RequestContext, filter_by_tenant
from app.db.models.audit_log import AuditLog
from app.db.models.property import Property
from app.modules.property.schemas import PropertyCreate, PropertyUpdate


_DUPLICATE_PLACE_ID_DETAIL = (
    "A property with this google_place_id already exists for this tenant"
)


def _flush_or_conflict(db: Session) -> None:
    """Flush pending changes; convert place-id unique violation to HTTP 409."""
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        msg = str(getattr(exc, "orig", exc))
        if "ux_properties_tenant_place" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_DUPLICATE_PLACE_ID_DETAIL,
            ) from exc
        raise


def _snapshot(prop: Property) -> dict[str, Any]:
    """Return a JSON-safe dict representing the property's current state."""
    return {
        "id": str(prop.id) if prop.id is not None else None,
        "tenant_id": str(prop.tenant_id) if prop.tenant_id is not None else None,
        "name": prop.name,
        "google_place_id": prop.google_place_id,
        "google_maps_url": prop.google_maps_url,
        "is_active": prop.is_active,
    }


def _coerce_uuid(value: str | UUID) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid property id",
        ) from exc


def create_property(
    db: Session, *, ctx: RequestContext, payload: PropertyCreate
) -> Property:
    prop = Property(
        tenant_id=_coerce_uuid(ctx.tenant_id),
        name=payload.name,
        google_place_id=payload.google_place_id,
        google_maps_url=payload.google_maps_url,
        is_active=True,
    )
    db.add(prop)
    _flush_or_conflict(db)
    db.refresh(prop)
    return prop


def get_properties(
    db: Session,
    *,
    ctx: RequestContext,
    is_active: bool | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Property], int]:
    """Return ``(rows, total)`` for the current tenant.

    ``is_active`` filters the result set when supplied; ``None`` returns
    both active and inactive rows. ``q`` performs a case-insensitive
    substring search over ``name`` and ``google_place_id``.
    """
    base = filter_by_tenant(db.query(Property), Property, ctx.tenant_id)
    if is_active is not None:
        base = base.filter(Property.is_active.is_(bool(is_active)))
    if q:
        like = f"%{q.strip()}%"
        base = base.filter(
            or_(Property.name.ilike(like), Property.google_place_id.ilike(like))
        )

    total = base.with_entities(func.count(Property.id)).scalar() or 0
    rows = (
        base.order_by(Property.created_at.desc())
        .offset(max(offset, 0))
        .limit(max(min(limit, 500), 1))
        .all()
    )
    return rows, int(total)


def get_property_by_id(
    db: Session, *, ctx: RequestContext, property_id: str | UUID
) -> Property:
    pid = _coerce_uuid(property_id)
    prop = (
        filter_by_tenant(db.query(Property), Property, ctx.tenant_id)
        .filter(Property.id == pid)
        .first()
    )
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    return prop


def update_property(
    db: Session,
    *,
    ctx: RequestContext,
    property_id: str | UUID,
    payload: PropertyUpdate,
) -> tuple[Property, dict[str, Any], dict[str, Any]]:
    """Apply a partial update; returns ``(property, before, after)`` snapshots."""
    prop = get_property_by_id(db, ctx=ctx, property_id=property_id)
    before = _snapshot(prop)

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided to update",
        )

    if "name" in data and data["name"] is not None:
        prop.name = data["name"]
    if "google_place_id" in data:
        prop.google_place_id = data["google_place_id"]
    if "google_maps_url" in data:
        prop.google_maps_url = data["google_maps_url"]
    if "is_active" in data and data["is_active"] is not None:
        prop.is_active = bool(data["is_active"])

    db.add(prop)
    _flush_or_conflict(db)
    db.refresh(prop)
    after = _snapshot(prop)
    return prop, before, after


def deactivate_property(
    db: Session, *, ctx: RequestContext, property_id: str | UUID
) -> tuple[Property, dict[str, Any], dict[str, Any], bool]:
    """Soft-delete by flipping ``is_active`` to False.

    Returns ``(property, before, after, changed)``. ``changed`` is False when
    the property was already inactive — callers should skip audit logging in
    that case to keep DELETE idempotent without log noise.
    """
    prop = get_property_by_id(db, ctx=ctx, property_id=property_id)
    before = _snapshot(prop)
    if not prop.is_active:
        return prop, before, before, False
    prop.is_active = False
    db.add(prop)
    db.flush()
    db.refresh(prop)
    after = _snapshot(prop)
    return prop, before, after, True


def activate_property(
    db: Session, *, ctx: RequestContext, property_id: str | UUID
) -> tuple[Property, dict[str, Any], dict[str, Any], bool]:
    """Re-activate a soft-deleted property.

    Returns ``(property, before, after, changed)``. ``changed`` is False when
    the property was already active.
    """
    prop = get_property_by_id(db, ctx=ctx, property_id=property_id)
    before = _snapshot(prop)
    if prop.is_active:
        return prop, before, before, False

    prop.is_active = True
    db.add(prop)
    db.flush()
    db.refresh(prop)
    after = _snapshot(prop)
    return prop, before, after, True


# ---------------------------------------------------------------------------
# Admin (cross-tenant) read helpers — no tenant filter, admin-only callers.
# ---------------------------------------------------------------------------


def admin_search_properties(
    db: Session,
    *,
    tenant_id: str | UUID | None = None,
    is_active: bool | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Property], int]:
    """Cross-tenant property search for admin endpoints.

    Callers are responsible for ensuring the requester is an admin (the
    surrounding route does this via ``require_role``).
    """
    base = db.query(Property)
    if tenant_id is not None:
        base = base.filter(Property.tenant_id == _coerce_uuid(tenant_id))
    if is_active is not None:
        base = base.filter(Property.is_active.is_(bool(is_active)))
    if q:
        like = f"%{q.strip()}%"
        base = base.filter(
            or_(Property.name.ilike(like), Property.google_place_id.ilike(like))
        )
    total = base.with_entities(func.count(Property.id)).scalar() or 0
    rows = (
        base.order_by(Property.created_at.desc())
        .offset(max(offset, 0))
        .limit(max(min(limit, 500), 1))
        .all()
    )
    return rows, int(total)


def admin_list_tenant_properties(
    db: Session,
    *,
    tenant_id: str | UUID,
    is_active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Property], int]:
    return admin_search_properties(
        db,
        tenant_id=tenant_id,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Bulk operations (tenant-scoped). Each helper returns per-item results so
# the route layer can audit individual successes/failures.
# ---------------------------------------------------------------------------


def bulk_create_properties(
    db: Session,
    *,
    ctx: RequestContext,
    payloads: list[PropertyCreate],
) -> list[dict[str, Any]]:
    """Create properties one at a time, isolating each in a SAVEPOINT.

    Returns one result dict per input, in order:
      ``{"index": i, "ok": True, "property": Property, "after": dict}``
      ``{"index": i, "ok": False, "error": str, "status": int}``
    """
    results: list[dict[str, Any]] = []
    tenant_uuid = _coerce_uuid(ctx.tenant_id)
    for idx, payload in enumerate(payloads):
        sp = db.begin_nested()
        try:
            prop = Property(
                tenant_id=tenant_uuid,
                name=payload.name,
                google_place_id=payload.google_place_id,
                is_active=True,
            )
            db.add(prop)
            db.flush()
            db.refresh(prop)
            results.append(
                {"index": idx, "ok": True, "property": prop, "after": _snapshot(prop)}
            )
        except IntegrityError as exc:
            sp.rollback()
            msg = str(getattr(exc, "orig", exc))
            if "ux_properties_tenant_place" in msg:
                results.append(
                    {
                        "index": idx,
                        "ok": False,
                        "error": _DUPLICATE_PLACE_ID_DETAIL,
                        "status": status.HTTP_409_CONFLICT,
                    }
                )
            else:
                results.append(
                    {
                        "index": idx,
                        "ok": False,
                        "error": "integrity_error",
                        "status": status.HTTP_400_BAD_REQUEST,
                    }
                )
    return results


def bulk_deactivate_properties(
    db: Session,
    *,
    ctx: RequestContext,
    property_ids: list[str | UUID],
) -> list[dict[str, Any]]:
    """Soft-delete a batch of properties.

    Per-item result shape:
      ``{"id": str, "ok": True, "changed": bool, "property": Property,
         "before": dict, "after": dict}``
      ``{"id": str, "ok": False, "error": str, "status": int}``
    """
    results: list[dict[str, Any]] = []
    for raw_id in property_ids:
        try:
            pid = _coerce_uuid(raw_id)
        except HTTPException as exc:
            results.append(
                {
                    "id": str(raw_id),
                    "ok": False,
                    "error": exc.detail,
                    "status": exc.status_code,
                }
            )
            continue
        prop = (
            filter_by_tenant(db.query(Property), Property, ctx.tenant_id)
            .filter(Property.id == pid)
            .first()
        )
        if prop is None:
            results.append(
                {
                    "id": str(pid),
                    "ok": False,
                    "error": "Property not found",
                    "status": status.HTTP_404_NOT_FOUND,
                }
            )
            continue
        before = _snapshot(prop)
        if not prop.is_active:
            results.append(
                {
                    "id": str(pid),
                    "ok": True,
                    "changed": False,
                    "property": prop,
                    "before": before,
                    "after": before,
                }
            )
            continue
        prop.is_active = False
        db.add(prop)
        db.flush()
        db.refresh(prop)
        results.append(
            {
                "id": str(pid),
                "ok": True,
                "changed": True,
                "property": prop,
                "before": before,
                "after": _snapshot(prop),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Per-property audit-log retrieval (tenant-scoped).
# ---------------------------------------------------------------------------


def get_property_audit_logs(
    db: Session,
    *,
    ctx: RequestContext,
    property_id: str | UUID,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """Return ``(rows, total)`` of audit entries for one property.

    Performs a tenant-scoped existence check first (raises 404 otherwise)
    so callers cannot probe other tenants' property ids.
    """
    # Existence + tenant check.
    get_property_by_id(db, ctx=ctx, property_id=property_id)
    pid = _coerce_uuid(property_id)

    base = db.query(AuditLog).filter(
        AuditLog.entity == "property",
        AuditLog.entity_id == pid,
    )
    total = base.with_entities(func.count(AuditLog.id)).scalar() or 0
    rows = (
        base.order_by(AuditLog.timestamp.desc())
        .offset(max(offset, 0))
        .limit(max(min(limit, 500), 1))
        .all()
    )
    return rows, int(total)
