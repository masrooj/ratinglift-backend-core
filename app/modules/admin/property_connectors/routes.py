"""Super-admin cross-tenant property-connector view.

Mounted under ``/api/v1/admin/property-connectors``. Read-only — mutations
still go through the tenant-scoped routes so audit semantics remain clean.
Restricted to the same admin role set used elsewhere in the admin module.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.db.models.user import User
from app.db.session import get_db
from app.modules.admin.connectors.routes import ADMIN_ROLES
from app.modules.admin.property_connectors import service as pc_admin_service
from app.modules.admin.property_connectors.schemas import (
    AdminPropertyConnectorList,
    AdminPropertyConnectorRow,
)
from app.modules.audit.service import log_admin_action
from app.modules.auth.service import oauth2_scheme, require_role
from app.modules.property_connector import service as pc_service
from app.modules.property_connector.schemas import (
    ActivateConnector,
    ConnectorResponse,
    UpdateConnector,
)


admin_property_connector_router = APIRouter(
    prefix="/api/v1/admin/property-connectors",
    tags=["admin-property-connectors"],
    dependencies=[Depends(oauth2_scheme)],
)


@admin_property_connector_router.get("", response_model=AdminPropertyConnectorList)
def list_property_connectors(
    response: Response,
    tenant_id: UUID | None = Query(
        default=None,
        description="Filter to bindings owned by a specific tenant.",
    ),
    property_id: UUID | None = Query(
        default=None,
        description="Filter to bindings on a specific property.",
    ),
    connector_id: UUID | None = Query(
        default=None,
        description=(
            "Filter to bindings using a specific connector. "
            "Use this to answer 'which properties are connected to <X>?'."
        ),
    ),
    is_active: bool | None = Query(
        default=None,
        description="Optional active/inactive filter.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(ADMIN_ROLES)),
) -> AdminPropertyConnectorList:
    """Cross-tenant binding report for the super-admin console.

    The single endpoint covers both views by combining filters:

    * ``?connector_id=<cid>`` → "which properties (and tenants) are connected
      to this source?"
    * ``?property_id=<pid>`` or ``?tenant_id=<tid>`` → drilldown the other way.
    * No filters → full report (paginated).
    """
    rows, total = pc_admin_service.list_bindings(
        db,
        tenant_id=tenant_id,
        property_id=property_id,
        connector_id=connector_id,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return AdminPropertyConnectorList(
        total=total,
        limit=limit,
        offset=offset,
        items=[AdminPropertyConnectorRow.model_validate(r) for r in rows],
    )


# ---------------------------------------------------------------------------
# Cross-tenant mutations
# ---------------------------------------------------------------------------
#
# These endpoints let a SUPER_ADMIN / OPS_ADMIN flip activation state and
# rotate credentials on behalf of any tenant — useful for support and for
# remediating misconfigurations without having to impersonate the customer.
# Every action funnels through the existing tenant-scoped service so the
# duplicate-prevention, validation, and audit-snapshot logic stay in one
# place. Each mutation is recorded into ``admin_action_logs`` (with an
# automatic mirror into the canonical ``audit_logs`` stream) so privileged
# activity has its own queryable trail.


@admin_property_connector_router.post(
    "/properties/{property_id}/connectors",
    response_model=ConnectorResponse,
    status_code=status.HTTP_201_CREATED,
)
def admin_activate_connector(
    property_id: UUID,
    payload: ActivateConnector,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    """Activate a connector for any tenant's property (super-admin only)."""
    ctx, pid, tenant_id = pc_admin_service.admin_context_for_property(
        db, admin_user_id=admin.id, property_id=property_id
    )
    pc, before, after = pc_service.activate_connector(
        db, ctx=ctx, property_id=pid, payload=payload
    )
    log_admin_action(
        db,
        admin_id=admin.id,
        action="connector_activated",
        target_entity="property_connector",
        target_id=pc.id,
        target_tenant_id=tenant_id,
        before_value=before,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(pc)
    return ConnectorResponse.model_validate(pc)


@admin_property_connector_router.put(
    "/{property_connector_id}", response_model=ConnectorResponse
)
def admin_update_connector(
    property_connector_id: UUID,
    payload: UpdateConnector,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    """Rotate credentials / update config for any binding (super-admin only)."""
    ctx, pid, tenant_id = pc_admin_service.admin_context_for_binding(
        db,
        admin_user_id=admin.id,
        property_connector_id=property_connector_id,
    )
    pc, before, after, changed = pc_service.update_connector(
        db,
        ctx=ctx,
        property_id=pid,
        property_connector_id=property_connector_id,
        payload=payload,
    )
    if changed:
        log_admin_action(
            db,
            admin_id=admin.id,
            action="connector_updated",
            target_entity="property_connector",
            target_id=pc.id,
            target_tenant_id=tenant_id,
            before_value=before,
            after_value=after,
            request=request,
        )
        db.commit()
        db.refresh(pc)
    return ConnectorResponse.model_validate(pc)


@admin_property_connector_router.delete(
    "/{property_connector_id}", response_model=ConnectorResponse
)
def admin_deactivate_connector(
    property_connector_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    """Deactivate any binding (super-admin only)."""
    ctx, pid, tenant_id = pc_admin_service.admin_context_for_binding(
        db,
        admin_user_id=admin.id,
        property_connector_id=property_connector_id,
    )
    pc, before, after, changed = pc_service.deactivate_connector(
        db,
        ctx=ctx,
        property_id=pid,
        property_connector_id=property_connector_id,
    )
    if changed:
        log_admin_action(
            db,
            admin_id=admin.id,
            action="connector_deactivated",
            target_entity="property_connector",
            target_id=pc.id,
            target_tenant_id=tenant_id,
            before_value=before,
            after_value=after,
            request=request,
        )
        db.commit()
        db.refresh(pc)
    return ConnectorResponse.model_validate(pc)


@admin_property_connector_router.post(
    "/{property_connector_id}/activate", response_model=ConnectorResponse
)
def admin_reactivate_connector(
    property_connector_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    """Re-enable a previously deactivated binding (super-admin only).

    Reuses stored credentials — admin doesn't need the tenant's secrets to
    flip the integration back on.
    """
    ctx, pid, tenant_id = pc_admin_service.admin_context_for_binding(
        db,
        admin_user_id=admin.id,
        property_connector_id=property_connector_id,
    )
    pc, before, after, changed = pc_service.reactivate_connector(
        db,
        ctx=ctx,
        property_id=pid,
        property_connector_id=property_connector_id,
    )
    if changed:
        log_admin_action(
            db,
            admin_id=admin.id,
            action="connector_activated",
            target_entity="property_connector",
            target_id=pc.id,
            target_tenant_id=tenant_id,
            before_value=before,
            after_value=after,
            request=request,
        )
        db.commit()
        db.refresh(pc)
    return ConnectorResponse.model_validate(pc)
