"""Tenant-facing connector catalog endpoint.

Mounted under ``/api/v1/tenant/connectors``. Returns ONLY active, non-deleted
connectors so the property-side picker never offers an unavailable
integration. The response payload is a slim ``TenantConnectorResponse`` that
omits soft-delete bookkeeping (those fields would always be false/null
behind the filter and add noise).

When called with ``?property_id=<uuid>`` each item is enriched with the
per-property binding state (``property_connector_id`` + ``is_connected``)
so the property UI can render Connect / Disconnect / Reactivate without a
second round-trip per row.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import RequestContext, filter_by_tenant, require_tenant_context
from app.db.models.property import Property
from app.db.models.property_connector import PropertyConnector
from app.db.session import get_db
from app.modules.admin.connectors import service as connector_service
from app.modules.admin.connectors.schemas import (
    TenantConnectorList,
    TenantConnectorResponse,
)
from app.modules.auth.service import oauth2_scheme

tenant_connector_router = APIRouter(
    prefix="/api/v1/tenant/connectors",
    tags=["tenant-connectors"],
    dependencies=[Depends(oauth2_scheme)],
)


@tenant_connector_router.get("", response_model=TenantConnectorList)
def list_active_connectors(
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    property_id: UUID | None = Query(
        default=None,
        description=(
            "Optional. When supplied, each item carries the "
            "per-property binding state so the UI can render the right CTA."
        ),
    ),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> TenantConnectorList:
    """List active connectors available for property attachment."""
    rows, total = connector_service.list_connectors(
        db, is_active=True, limit=limit, offset=offset
    )

    bindings: dict[UUID, PropertyConnector] = {}
    if property_id is not None:
        # Tenant-scope the lookup: refuse to leak binding state for a
        # property the caller doesn't own.
        owned = (
            filter_by_tenant(db.query(Property), Property, ctx.tenant_id)
            .filter(Property.id == property_id)
            .first()
        )
        if owned is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Property not found",
            )
        connector_ids = [r.id for r in rows]
        if connector_ids:
            pcs = (
                db.query(PropertyConnector)
                .filter(
                    PropertyConnector.property_id == property_id,
                    PropertyConnector.connector_id.in_(connector_ids),
                )
                .all()
            )
            bindings = {pc.connector_id: pc for pc in pcs}

    items: list[TenantConnectorResponse] = []
    for r in rows:
        item = TenantConnectorResponse.model_validate(r, from_attributes=True)
        pc = bindings.get(r.id)
        if pc is not None:
            item = item.model_copy(
                update={
                    "property_connector_id": pc.id,
                    "is_connected": bool(pc.is_active),
                }
            )
        items.append(item)

    response.headers["X-Total-Count"] = str(total)
    return TenantConnectorList(
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )
