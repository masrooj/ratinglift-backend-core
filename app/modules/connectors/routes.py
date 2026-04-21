"""Tenant-facing connector catalog endpoint.

Mounted under ``/api/v1/tenant/connectors``. Returns ONLY active, non-deleted
connectors so the property-side picker never offers an unavailable
integration. The response payload is a slim ``TenantConnectorResponse`` that
omits soft-delete bookkeeping (those fields would always be false/null
behind the filter and add noise).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.dependencies import RequestContext, require_tenant_context
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
    db: Session = Depends(get_db),
    _ctx: RequestContext = Depends(require_tenant_context),
) -> TenantConnectorList:
    """List active connectors available for property attachment."""
    rows, total = connector_service.list_connectors(
        db, is_active=True, limit=limit, offset=offset
    )
    response.headers["X-Total-Count"] = str(total)
    return TenantConnectorList(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            TenantConnectorResponse.model_validate(r, from_attributes=True)
            for r in rows
        ],
    )
