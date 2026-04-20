"""HTTP routes for tenant property management.

Mounted under ``/api/v1/tenant/properties``. All endpoints require a tenant
context (enforced by ``require_tenant_context``) and emit audit log entries
for create/update/deactivate operations.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import RequestContext, require_tenant_context
from app.db.session import get_db
from app.modules.audit import log_action
from app.modules.auth.service import oauth2_scheme
from app.modules.property import service as property_service
from app.modules.property.schemas import (
    PropertyAuditEntry,
    PropertyAuditList,
    PropertyBulkCreate,
    PropertyBulkCreateResponse,
    PropertyBulkCreateResultItem,
    PropertyBulkDeactivate,
    PropertyBulkDeactivateResponse,
    PropertyBulkDeactivateResultItem,
    PropertyCreate,
    PropertyList,
    PropertyResponse,
    PropertyUpdate,
)

property_router = APIRouter(
    prefix="/api/v1/tenant/properties",
    tags=["property"],
    dependencies=[Depends(oauth2_scheme)],
)


@property_router.post(
    "",
    response_model=PropertyResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_property_endpoint(
    payload: PropertyCreate,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> PropertyResponse:
    prop = property_service.create_property(db, ctx=ctx, payload=payload)
    after = property_service._snapshot(prop)
    log_action(
        db,
        actor_id=ctx.user_id,
        actor_type="tenant",
        action="property.create",
        entity="property",
        entity_id=prop.id,
        before_value=None,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(prop)
    return PropertyResponse.model_validate(prop)


@property_router.get("", response_model=PropertyList)
def list_properties_endpoint(
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
    is_active: bool | None = None,
    q: str | None = Query(default=None, max_length=255, description="Case-insensitive substring match over name and google_place_id"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PropertyList:
    rows, total = property_service.get_properties(
        db, ctx=ctx, is_active=is_active, q=q, limit=limit, offset=offset
    )
    response.headers["X-Total-Count"] = str(total)
    return PropertyList(
        total=total,
        limit=limit,
        offset=offset,
        items=[PropertyResponse.model_validate(r) for r in rows],
    )


@property_router.get("/{property_id}", response_model=PropertyResponse)
def get_property_endpoint(
    property_id: UUID,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> PropertyResponse:
    prop = property_service.get_property_by_id(db, ctx=ctx, property_id=property_id)
    return PropertyResponse.model_validate(prop)


@property_router.put("/{property_id}", response_model=PropertyResponse)
def update_property_endpoint(
    property_id: UUID,
    payload: PropertyUpdate,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> PropertyResponse:
    prop, before, after = property_service.update_property(
        db, ctx=ctx, property_id=property_id, payload=payload
    )
    log_action(
        db,
        actor_id=ctx.user_id,
        actor_type="tenant",
        action="property.update",
        entity="property",
        entity_id=prop.id,
        before_value=before,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(prop)
    return PropertyResponse.model_validate(prop)


@property_router.delete("/{property_id}", response_model=PropertyResponse)
def deactivate_property_endpoint(
    property_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> PropertyResponse:
    prop, before, after, changed = property_service.deactivate_property(
        db, ctx=ctx, property_id=property_id
    )
    if changed:
        log_action(
            db,
            actor_id=ctx.user_id,
            actor_type="tenant",
            action="property.deactivate",
            entity="property",
            entity_id=prop.id,
            before_value=before,
            after_value=after,
            request=request,
        )
        db.commit()
        db.refresh(prop)
    return PropertyResponse.model_validate(prop)


@property_router.post(
    "/{property_id}/activate", response_model=PropertyResponse
)
def activate_property_endpoint(
    property_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> PropertyResponse:
    prop, before, after, changed = property_service.activate_property(
        db, ctx=ctx, property_id=property_id
    )
    if changed:
        log_action(
            db,
            actor_id=ctx.user_id,
            actor_type="tenant",
            action="property.activate",
            entity="property",
            entity_id=prop.id,
            before_value=before,
            after_value=after,
            request=request,
        )
        db.commit()
        db.refresh(prop)
    return PropertyResponse.model_validate(prop)


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


@property_router.post(
    "/bulk",
    response_model=PropertyBulkCreateResponse,
    status_code=status.HTTP_207_MULTI_STATUS,
)
def bulk_create_properties_endpoint(
    payload: PropertyBulkCreate,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> PropertyBulkCreateResponse:
    raw = property_service.bulk_create_properties(
        db, ctx=ctx, payloads=payload.items
    )

    items: list[PropertyBulkCreateResultItem] = []
    created = failed = 0
    for r in raw:
        if r["ok"]:
            created += 1
            prop = r["property"]
            log_action(
                db,
                actor_id=ctx.user_id,
                actor_type="tenant",
                action="property.create",
                entity="property",
                entity_id=prop.id,
                before_value=None,
                after_value=r["after"],
                request=request,
            )
            items.append(
                PropertyBulkCreateResultItem(
                    index=r["index"],
                    ok=True,
                    property=PropertyResponse.model_validate(prop),
                )
            )
        else:
            failed += 1
            items.append(
                PropertyBulkCreateResultItem(
                    index=r["index"],
                    ok=False,
                    error=r["error"],
                    status=r["status"],
                )
            )
    db.commit()
    return PropertyBulkCreateResponse(created=created, failed=failed, results=items)


@property_router.post(
    "/bulk-deactivate",
    response_model=PropertyBulkDeactivateResponse,
    status_code=status.HTTP_207_MULTI_STATUS,
)
def bulk_deactivate_properties_endpoint(
    payload: PropertyBulkDeactivate,
    request: Request,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
) -> PropertyBulkDeactivateResponse:
    raw = property_service.bulk_deactivate_properties(
        db, ctx=ctx, property_ids=[str(i) for i in payload.ids]
    )

    items: list[PropertyBulkDeactivateResultItem] = []
    deactivated = unchanged = failed = 0
    audited = False
    for r in raw:
        if not r["ok"]:
            failed += 1
            items.append(
                PropertyBulkDeactivateResultItem(
                    id=r["id"], ok=False, error=r["error"], status=r["status"]
                )
            )
            continue
        if r["changed"]:
            deactivated += 1
            audited = True
            log_action(
                db,
                actor_id=ctx.user_id,
                actor_type="tenant",
                action="property.deactivate",
                entity="property",
                entity_id=r["property"].id,
                before_value=r["before"],
                after_value=r["after"],
                request=request,
            )
        else:
            unchanged += 1
        items.append(
            PropertyBulkDeactivateResultItem(
                id=r["id"], ok=True, changed=bool(r["changed"])
            )
        )
    if audited or deactivated or unchanged:
        db.commit()
    return PropertyBulkDeactivateResponse(
        deactivated=deactivated,
        unchanged=unchanged,
        failed=failed,
        results=items,
    )


# ---------------------------------------------------------------------------
# Audit retrieval (per property)
# ---------------------------------------------------------------------------


@property_router.get(
    "/{property_id}/audit",
    response_model=PropertyAuditList,
)
def get_property_audit_endpoint(
    property_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(require_tenant_context),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PropertyAuditList:
    rows, total = property_service.get_property_audit_logs(
        db, ctx=ctx, property_id=property_id, limit=limit, offset=offset
    )
    response.headers["X-Total-Count"] = str(total)
    return PropertyAuditList(
        total=total,
        limit=limit,
        offset=offset,
        items=[PropertyAuditEntry.model_validate(r) for r in rows],
    )
