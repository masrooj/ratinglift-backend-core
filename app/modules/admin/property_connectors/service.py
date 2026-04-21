"""Service layer for the admin cross-tenant property-connector view.

Pure read-only — no mutation paths live here. Every query is a single
join across ``property_connectors``, ``properties``, ``tenants``, and
``connectors`` so the admin grid can render the full report in one round-
trip without N+1.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import RequestContext
from app.db.models.connector import Connector
from app.db.models.property import Property
from app.db.models.property_connector import PropertyConnector
from app.db.models.tenant import Tenant


def _base_query(db: Session):
    return (
        db.query(
            PropertyConnector.id.label("id"),
            PropertyConnector.is_active.label("is_active"),
            PropertyConnector.created_at.label("created_at"),
            PropertyConnector.scopes.label("scopes"),
            PropertyConnector.config.label("config"),
            PropertyConnector.base_url.label("base_url"),
            Tenant.id.label("tenant_id"),
            Tenant.name.label("tenant_name"),
            Property.id.label("property_id"),
            Property.name.label("property_name"),
            Connector.id.label("connector_id"),
            Connector.name.label("connector_name"),
            Connector.logo_url.label("connector_logo_url"),
        )
        .join(Property, Property.id == PropertyConnector.property_id)
        .join(Tenant, Tenant.id == Property.tenant_id)
        .join(Connector, Connector.id == PropertyConnector.connector_id)
    )


def list_bindings(
    db: Session,
    *,
    tenant_id: UUID | None = None,
    property_id: UUID | None = None,
    connector_id: UUID | None = None,
    is_active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List property-connector bindings across all tenants.

    All filters are optional and combine with AND semantics. ``tenant_id``
    and ``connector_id`` are the two main pivots: use the former to see
    everything one tenant has wired up, the latter to see every property
    connected to a given source.

    Returns ``(rows, total)`` where ``rows`` is a list of mapping-style
    objects (one per binding) ready to feed into
    :class:`AdminPropertyConnectorRow`.
    """
    q = _base_query(db)

    if tenant_id is not None:
        q = q.filter(Tenant.id == tenant_id)
    if property_id is not None:
        q = q.filter(Property.id == property_id)
    if connector_id is not None:
        q = q.filter(Connector.id == connector_id)
    if is_active is not None:
        q = q.filter(PropertyConnector.is_active.is_(bool(is_active)))

    # Total count BEFORE pagination, against the same filtered set.
    total = (
        q.with_entities(func.count(PropertyConnector.id))
        .order_by(None)
        .scalar()
        or 0
    )

    rows = (
        q.order_by(
            Tenant.name.asc(),
            Property.name.asc(),
            Connector.name.asc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Each row is a SQLAlchemy ``Row`` — convert to a plain mapping so
    # Pydantic's ``from_attributes`` can pick up every labelled column.
    return [dict(r._mapping) for r in rows], int(total)


# ---------------------------------------------------------------------------
# Admin impersonation helpers
# ---------------------------------------------------------------------------
#
# The admin mutation routes reuse the tenant-scoped service in
# :mod:`app.modules.property_connector.service` so we don't fork the
# activation / deactivation / rotate logic. To make that work without
# weakening tenant isolation, we look up the target property's actual
# ``tenant_id`` here and build a ``RequestContext`` that the tenant service
# will accept.


def _coerce_uuid(value: str | UUID, *, detail: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from exc


def admin_context_for_property(
    db: Session, *, admin_user_id: str | UUID, property_id: str | UUID
) -> tuple[RequestContext, UUID, UUID]:
    """Build a tenant-scoped context for an admin acting on a property.

    Returns ``(ctx, property_uuid, tenant_uuid)``. The context carries the
    *property's* tenant_id so the existing ``filter_by_tenant`` checks pass
    cleanly. ``is_admin=True`` is preserved on the context for downstream
    auditors that care about the actor type.

    Raises 404 when the property does not exist.
    """
    pid = _coerce_uuid(property_id, detail="Invalid property id")
    prop = db.query(Property).filter(Property.id == pid).first()
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Property not found"
        )
    ctx = RequestContext(
        user_id=str(admin_user_id),
        tenant_id=str(prop.tenant_id),
        role="SUPER_ADMIN",
        is_admin=True,
    )
    return ctx, prop.id, prop.tenant_id


def admin_context_for_binding(
    db: Session,
    *,
    admin_user_id: str | UUID,
    property_connector_id: str | UUID,
) -> tuple[RequestContext, UUID, UUID]:
    """Resolve a binding to ``(ctx, property_id, tenant_id)`` for admin use.

    Useful for routes (PUT / DELETE / re-activate) that take only the
    binding id — the admin shouldn't have to also pass the owning
    property_id.

    Raises 404 when the binding does not exist.
    """
    pc_id = _coerce_uuid(
        property_connector_id, detail="Invalid property_connector id"
    )
    row = (
        db.query(PropertyConnector, Property)
        .join(Property, Property.id == PropertyConnector.property_id)
        .filter(PropertyConnector.id == pc_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property connector not found",
        )
    pc, prop = row
    ctx = RequestContext(
        user_id=str(admin_user_id),
        tenant_id=str(prop.tenant_id),
        role="SUPER_ADMIN",
        is_admin=True,
    )
    return ctx, pc.property_id, prop.tenant_id


__all__ = [
    "admin_context_for_binding",
    "admin_context_for_property",
    "list_bindings",
]
