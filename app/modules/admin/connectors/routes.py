"""Admin Connector Master CRUD endpoints.

Mounted under ``/api/v1/admin/connectors``. Restricted to SUPER_ADMIN and
OPS_ADMIN roles. All mutations are audit-logged via ``log_admin_action``.

Security note: this module deliberately does NOT accept or persist API
keys / credentials. Per-tenant credentials live on ``property_connectors``.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.modules.admin.connectors import service as connector_service
from app.modules.admin.connectors.schemas import (
    ConnectorList,
    ConnectorReorder,
    ConnectorResponse,
    ConnectorUpdate,
)
from app.modules.audit import log_admin_action
from app.modules.auth.service import oauth2_scheme, require_role

ADMIN_ROLES = [
    UserRole.SUPER_ADMIN.value,
    UserRole.OPS_ADMIN.value,
]

# Activation toggles are SUPER_ADMIN only — they decide which connectors
# are visible to tenants on the property-side picker.
ACTIVATION_ROLES = ADMIN_ROLES

admin_connector_router = APIRouter(
    prefix="/api/v1/admin/connectors",
    tags=["admin-connectors"],
    dependencies=[Depends(oauth2_scheme)],
)


@admin_connector_router.get("", response_model=ConnectorList)
def list_connectors(
    response: Response,
    is_active: bool | None = Query(default=None),
    include_deleted: bool = Query(
        default=False,
        description="Include soft-deleted connectors. Off by default.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorList:
    rows, total = connector_service.list_connectors(
        db,
        is_active=is_active,
        include_deleted=include_deleted,
        limit=limit,
        offset=offset,
    )
    # Mirror the body's ``total`` in a header so grid components that read
    # X-Total-Count (AG Grid, refine, react-admin) work without parsing JSON.
    response.headers["X-Total-Count"] = str(total)
    return ConnectorList(
        total=total,
        limit=limit,
        offset=offset,
        items=[ConnectorResponse.model_validate(r, from_attributes=True) for r in rows],
    )


@admin_connector_router.get("/{connector_id}", response_model=ConnectorResponse)
def get_connector(
    connector_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    row = connector_service.get_connector_or_404(db, connector_id)
    return ConnectorResponse.model_validate(row, from_attributes=True)


@admin_connector_router.post(
    "",
    response_model=ConnectorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connector(
    request: Request,
    name: str = Form(..., min_length=1, max_length=255),
    file: UploadFile | None = File(
        default=None,
        description="Optional logo image (png/jpg/svg/webp, ≤2 MB).",
    ),
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    """Create a connector. The logo can be uploaded in the same request.

    The endpoint is transactional: if the file fails to persist, the
    connector row is rolled back so the catalog never has a half-created
    record without its logo.
    """
    row = connector_service.create_connector(
        db,
        name=name,
        logo_url=None,
    )

    if file is not None and file.filename:
        try:
            file_bytes = await file.read()
            connector_service.save_connector_logo(
                db,
                connector=row,
                file_bytes=file_bytes,
                filename=file.filename,
                content_type=file.content_type,
            )
        except HTTPException:
            db.rollback()
            raise

    after = connector_service.snapshot(row)
    log_admin_action(
        db,
        admin_id=admin.id,
        action="connector_created",
        target_entity="connector",
        target_id=row.id,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ConnectorResponse.model_validate(row, from_attributes=True)


@admin_connector_router.put("/{connector_id}", response_model=ConnectorResponse)
def update_connector(
    connector_id: UUID,
    payload: ConnectorUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    row = connector_service.get_connector_or_404(db, connector_id)
    before = connector_service.snapshot(row)
    connector_service.update_connector(
        db,
        connector=row,
        name=payload.name,
        logo_url=None,
        is_active=payload.is_active,
        display_order=payload.display_order,
    )
    after = connector_service.snapshot(row)
    log_admin_action(
        db,
        admin_id=admin.id,
        action="connector_updated",
        target_entity="connector",
        target_id=row.id,
        before_value=before,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ConnectorResponse.model_validate(row, from_attributes=True)


@admin_connector_router.delete("/{connector_id}", response_model=ConnectorResponse)
def delete_connector(
    connector_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    row = connector_service.get_connector_or_404(db, connector_id)
    before = connector_service.snapshot(row)
    connector_service.soft_delete_connector(db, connector=row)
    after = connector_service.snapshot(row)
    log_admin_action(
        db,
        admin_id=admin.id,
        action="connector_deleted",
        target_entity="connector",
        target_id=row.id,
        before_value=before,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ConnectorResponse.model_validate(row, from_attributes=True)


@admin_connector_router.post(
    "/{connector_id}/restore", response_model=ConnectorResponse
)
def restore_connector(
    connector_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    row = connector_service.get_connector_including_deleted_or_404(
        db, connector_id
    )
    before = connector_service.snapshot(row)
    connector_service.restore_connector(db, connector=row)
    after = connector_service.snapshot(row)
    log_admin_action(
        db,
        admin_id=admin.id,
        action="connector_restored",
        target_entity="connector",
        target_id=row.id,
        before_value=before,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ConnectorResponse.model_validate(row, from_attributes=True)


def _set_active(
    *,
    db: Session,
    request: Request,
    admin: User,
    connector_id: UUID,
    activate: bool,
) -> ConnectorResponse:
    row = connector_service.get_connector_or_404(db, connector_id)
    if row.is_active == activate:
        # Idempotent: nothing to do, no audit row written.
        return ConnectorResponse.model_validate(row, from_attributes=True)
    before = connector_service.snapshot(row)
    connector_service.update_connector(
        db, connector=row, name=None, logo_url=None, is_active=activate
    )
    after = connector_service.snapshot(row)
    log_admin_action(
        db,
        admin_id=admin.id,
        action="connector_activated" if activate else "connector_deactivated",
        target_entity="connector",
        target_id=row.id,
        before_value=before,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ConnectorResponse.model_validate(row, from_attributes=True)


@admin_connector_router.post(
    "/{connector_id}/activate", response_model=ConnectorResponse
)
def activate_connector(
    connector_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ACTIVATION_ROLES)),
) -> ConnectorResponse:
    """Make a connector visible to tenants on the property-side picker.

    SUPER_ADMIN only.
    """
    return _set_active(
        db=db, request=request, admin=admin, connector_id=connector_id, activate=True
    )


@admin_connector_router.post(
    "/{connector_id}/deactivate", response_model=ConnectorResponse
)
def deactivate_connector(
    connector_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ACTIVATION_ROLES)),
) -> ConnectorResponse:
    """Hide a connector from the tenant property-side picker.

    SUPER_ADMIN only. The connector still appears in the admin catalog
    (rendered with reduced visibility in the UI).
    """
    return _set_active(
        db=db, request=request, admin=admin, connector_id=connector_id, activate=False
    )


@admin_connector_router.post(
    "/reorder",
    response_model=ConnectorList,
    status_code=status.HTTP_200_OK,
)
def reorder_connectors_endpoint(
    payload: ConnectorReorder,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorList:
    """Bulk-update ``display_order`` on a set of connectors.

    The whole batch is atomic: any unknown id rolls back the transaction
    (404). Soft-deleted connectors cannot be reordered.
    """
    items = [(item.id, item.display_order) for item in payload.items]
    try:
        rows = connector_service.reorder_connectors(db, items=items)
    except HTTPException:
        db.rollback()
        raise

    log_admin_action(
        db,
        admin_id=admin.id,
        action="connector_reordered",
        target_entity="connector",
        target_id=None,
        after_value={"order": [
            {"id": str(item.id), "display_order": item.display_order}
            for item in payload.items
        ]},
        request=request,
    )
    db.commit()
    for r in rows:
        db.refresh(r)
    return ConnectorList(
        total=len(rows),
        limit=len(rows),
        offset=0,
        items=[ConnectorResponse.model_validate(r, from_attributes=True) for r in rows],
    )


@admin_connector_router.post(
    "/{connector_id}/logo", response_model=ConnectorResponse
)
async def upload_connector_logo(
    connector_id: UUID,
    request: Request,
    file: UploadFile = File(..., description="Logo image (png/jpg/svg/webp, ≤2 MB)"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    row = connector_service.get_connector_or_404(db, connector_id)
    file_bytes = await file.read()
    before = connector_service.snapshot(row)
    connector_service.save_connector_logo(
        db,
        connector=row,
        file_bytes=file_bytes,
        filename=file.filename,
        content_type=file.content_type,
    )
    after = connector_service.snapshot(row)
    log_admin_action(
        db,
        admin_id=admin.id,
        action="connector_logo_uploaded",
        target_entity="connector",
        target_id=row.id,
        before_value=before,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ConnectorResponse.model_validate(row, from_attributes=True)


@admin_connector_router.delete(
    "/{connector_id}/logo", response_model=ConnectorResponse
)
def delete_connector_logo(
    connector_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_role(ADMIN_ROLES)),
) -> ConnectorResponse:
    row = connector_service.get_connector_or_404(db, connector_id)
    before = connector_service.snapshot(row)
    connector_service.clear_connector_logo(db, connector=row)
    after = connector_service.snapshot(row)
    log_admin_action(
        db,
        admin_id=admin.id,
        action="connector_logo_cleared",
        target_entity="connector",
        target_id=row.id,
        before_value=before,
        after_value=after,
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ConnectorResponse.model_validate(row, from_attributes=True)
