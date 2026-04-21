"""HTTP routes for tenant property-connector activation.

Mounted under ``/api/v1/tenant/properties/{property_id}/connectors``. All
endpoints require a tenant context and emit audit log entries for
activation/deactivation. Credentials are NEVER serialized into responses.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import RequestContext, require_tenant_context
from app.db.session import get_db
from app.modules.audit import log_action
from app.modules.auth.service import oauth2_scheme
from app.modules.property_connector import service as pc_service
from app.modules.property_connector.schemas import (
    ActivateConnector,
    ConnectorList,
    ConnectorResponse,
    UpdateConnector,
)

property_connector_router = APIRouter(
    prefix="/api/v1/tenant/properties/{property_id}/connectors",
    tags=["property-connectors"],
    dependencies=[Depends(oauth2_scheme)],
)


@property_connector_router.post(
    "",
    response_model=ConnectorResponse,
    status_code=status.HTTP_201_CREATED,
)
def activate_connector_endpoint(
    property_id: UUID,
    payload: ActivateConnector,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> ConnectorResponse:
    pc, before, after = pc_service.activate_connector(
        db, ctx=ctx, property_id=property_id, payload=payload
    )
    log_action(
        db,
        actor_id=ctx.user_id,
        actor_type="tenant",
        action="connector_activated",
        entity="property_connector",
        entity_id=pc.id,
        before_value=before,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(pc)
    return ConnectorResponse.model_validate(pc)


@property_connector_router.get("", response_model=ConnectorList)
def list_connectors_endpoint(
    property_id: UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> ConnectorList:
    rows = pc_service.list_connectors(db, ctx=ctx, property_id=property_id)
    items = [ConnectorResponse.model_validate(r) for r in rows]
    return ConnectorList(total=len(items), items=items)


@property_connector_router.put(
    "/{property_connector_id}", response_model=ConnectorResponse
)
def update_connector_endpoint(
    property_id: UUID,
    property_connector_id: UUID,
    payload: UpdateConnector,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> ConnectorResponse:
    """Rotate credentials / update config or base_url for an existing binding.

    Partial update — only fields explicitly set on the payload are applied.
    Use the activate / deactivate endpoints to flip ``is_active``; this
    route never changes that flag.
    """
    pc, before, after, changed = pc_service.update_connector(
        db,
        ctx=ctx,
        property_id=property_id,
        property_connector_id=property_connector_id,
        payload=payload,
    )
    if changed:
        log_action(
            db,
            actor_id=ctx.user_id,
            actor_type="tenant",
            action="connector_updated",
            entity="property_connector",
            entity_id=pc.id,
            before_value=before,
            after_value=after,
            request=request,
        )
        db.commit()
        db.refresh(pc)
    return ConnectorResponse.model_validate(pc)


@property_connector_router.delete(
    "/{property_connector_id}", response_model=ConnectorResponse
)
def deactivate_connector_endpoint(
    property_id: UUID,
    property_connector_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> ConnectorResponse:
    pc, before, after, changed = pc_service.deactivate_connector(
        db,
        ctx=ctx,
        property_id=property_id,
        property_connector_id=property_connector_id,
    )
    if changed:
        log_action(
            db,
            actor_id=ctx.user_id,
            actor_type="tenant",
            action="connector_deactivated",
            entity="property_connector",
            entity_id=pc.id,
            before_value=before,
            after_value=after,
            request=request,
        )
        db.commit()
        db.refresh(pc)
    return ConnectorResponse.model_validate(pc)


@property_connector_router.post(
    "/{property_connector_id}/activate", response_model=ConnectorResponse
)
def reactivate_connector_endpoint(
    property_id: UUID,
    property_connector_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> ConnectorResponse:
    """Re-enable a previously deactivated connector binding.

    Reuses the credentials already stored on the row, so the tenant doesn't
    have to re-enter api_key/api_secret to toggle the integration back on.
    """
    pc, before, after, changed = pc_service.reactivate_connector(
        db,
        ctx=ctx,
        property_id=property_id,
        property_connector_id=property_connector_id,
    )
    if changed:
        log_action(
            db,
            actor_id=ctx.user_id,
            actor_type="tenant",
            action="connector_activated",
            entity="property_connector",
            entity_id=pc.id,
            before_value=before,
            after_value=after,
            request=request,
        )
        db.commit()
        db.refresh(pc)
    return ConnectorResponse.model_validate(pc)
