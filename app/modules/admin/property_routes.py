"""Admin read APIs for tenants and their properties.

Mounted under ``/api/v1/admin``. Read-only; gated by ``READ_ROLES`` defined
in :mod:`app.modules.admin.audit_routes`. Mutations belong on the regular
tenant property routes (which already use ``log_action``).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models.property import Property
from app.db.models.tenant import Tenant
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.modules.admin.schemas import (
    AdminPropertyItem,
    AdminPropertyList,
    AdminTenantItem,
    AdminTenantList,
)
from app.modules.auth.service import oauth2_scheme, require_role
from app.modules.property import service as property_service

READ_ROLES = [
    UserRole.SUPER_ADMIN.value,
    UserRole.COMPLIANCE_ADMIN.value,
    UserRole.SUPPORT_ADMIN.value,
]

admin_property_router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-tenants"],
    dependencies=[Depends(oauth2_scheme)],
)


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _to_tenant_item(tenant: Tenant, property_count: int) -> AdminTenantItem:
    return AdminTenantItem(
        id=tenant.id,
        name=tenant.name,
        plan=_enum_value(tenant.plan),
        status=_enum_value(tenant.status),
        created_at=tenant.created_at,
        property_count=int(property_count or 0),
    )


def _to_property_item(prop: Property) -> AdminPropertyItem:
    return AdminPropertyItem(
        id=prop.id,
        tenant_id=prop.tenant_id,
        name=prop.name,
        google_place_id=prop.google_place_id,
        is_active=prop.is_active,
        created_at=prop.created_at,
        updated_at=getattr(prop, "updated_at", None),
    )


# --------------------------- tenants ---------------------------


@admin_property_router.get("/tenants", response_model=AdminTenantList)
def list_tenants(
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(READ_ROLES)),
) -> AdminTenantList:
    base = db.query(Tenant)
    if q:
        base = base.filter(Tenant.name.ilike(f"%{q.strip()}%"))

    total = base.with_entities(func.count(Tenant.id)).scalar() or 0
    tenants = (
        base.order_by(Tenant.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Bulk count properties per tenant in the page (avoids N+1).
    tenant_ids = [t.id for t in tenants]
    counts: dict = {}
    if tenant_ids:
        rows = (
            db.query(Property.tenant_id, func.count(Property.id))
            .filter(Property.tenant_id.in_(tenant_ids))
            .group_by(Property.tenant_id)
            .all()
        )
        counts = {tid: cnt for tid, cnt in rows}

    items = [_to_tenant_item(t, counts.get(t.id, 0)) for t in tenants]
    return AdminTenantList(total=int(total), limit=limit, offset=offset, items=items)


@admin_property_router.get("/tenants/{tenant_id}", response_model=AdminTenantItem)
def get_tenant(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(READ_ROLES)),
) -> AdminTenantItem:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
    count = (
        db.query(func.count(Property.id))
        .filter(Property.tenant_id == tenant_id)
        .scalar()
        or 0
    )
    return _to_tenant_item(tenant, count)


@admin_property_router.get(
    "/tenants/{tenant_id}/properties", response_model=AdminPropertyList
)
def list_tenant_properties(
    tenant_id: UUID,
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(READ_ROLES)),
) -> AdminPropertyList:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )
    rows, total = property_service.admin_list_tenant_properties(
        db,
        tenant_id=tenant_id,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return AdminPropertyList(
        total=total,
        limit=limit,
        offset=offset,
        items=[_to_property_item(r) for r in rows],
    )


# --------------------------- cross-tenant property search ---------------------------


@admin_property_router.get("/properties", response_model=AdminPropertyList)
def search_properties(
    tenant_id: UUID | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(READ_ROLES)),
) -> AdminPropertyList:
    rows, total = property_service.admin_search_properties(
        db,
        tenant_id=tenant_id,
        is_active=is_active,
        q=q,
        limit=limit,
        offset=offset,
    )
    return AdminPropertyList(
        total=total,
        limit=limit,
        offset=offset,
        items=[_to_property_item(r) for r in rows],
    )
