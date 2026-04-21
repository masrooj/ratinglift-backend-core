"""Service layer for tenant property-connector activation.

Responsibilities:

* Tenant isolation — every property lookup is funneled through
  :func:`filter_by_tenant` so a tenant cannot touch another tenant's
  property/connector wiring.
* Connector validation — the referenced connector must exist and be active
  (and not soft-deleted) at activation time.
* Duplicate prevention — a property may have at most one *active* binding
  per connector. Re-activating after soft-delete is allowed and reuses the
  existing row with refreshed credentials.
* Secret protection — ``api_secret`` is symmetrically encrypted at rest via
  :mod:`app.core.crypto`. Plaintext never leaves the service boundary.

Review ingestion (worker dispatch, fetch scheduling) is intentionally NOT
handled here — see the review-fetching ticket.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.dependencies import RequestContext, filter_by_tenant
from app.core.logging import get_logger
from app.db.models.connector import Connector
from app.db.models.property import Property
from app.db.models.property_connector import PropertyConnector
from app.modules.property_connector.schemas import (
    ActivateConnector,
    ConnectorCredentials,
    UpdateConnector,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_uuid(value: str | UUID, *, detail: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=detail
        ) from exc


def _snapshot(pc: PropertyConnector) -> dict[str, Any]:
    """JSON-safe view of a PropertyConnector for audit logs.

    Credentials are intentionally excluded.
    """
    return {
        "id": str(pc.id) if pc.id is not None else None,
        "property_id": str(pc.property_id) if pc.property_id is not None else None,
        "connector_id": str(pc.connector_id) if pc.connector_id is not None else None,
        "is_active": bool(pc.is_active),
        "scopes": pc.scopes,
        "config": pc.config,
        "base_url": pc.base_url,
    }


def _get_owned_property(
    db: Session, *, ctx: RequestContext, property_id: str | UUID
) -> Property:
    pid = _coerce_uuid(property_id, detail="Invalid property id")
    prop = (
        filter_by_tenant(db.query(Property), Property, ctx.tenant_id)
        .filter(Property.id == pid)
        .first()
    )
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Property not found"
        )
    return prop


def _get_active_connector(db: Session, connector_id: UUID) -> Connector:
    connector = (
        db.query(Connector)
        .filter(Connector.id == connector_id, Connector.is_deleted.is_(False))
        .first()
    )
    if connector is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found"
        )
    if not connector.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connector is not active",
        )
    return connector


def _annotate(pc: PropertyConnector, connector: Connector | None) -> PropertyConnector:
    """Attach denormalised connector_name/logo for response serialisation.

    Pydantic's ``from_attributes=True`` will pick these up via getattr, so we
    avoid an N+1 query in routes without committing to a DB-level join view.
    """
    pc.connector_name = connector.name if connector is not None else None  # type: ignore[attr-defined]
    pc.connector_logo_url = connector.logo_url if connector is not None else None  # type: ignore[attr-defined]
    return pc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def activate_connector(
    db: Session,
    *,
    ctx: RequestContext,
    property_id: str | UUID,
    payload: ActivateConnector,
) -> tuple[PropertyConnector, dict[str, Any] | None, dict[str, Any]]:
    """Create (or refresh) a connector binding for a tenant property.

    Returns ``(property_connector, before, after)`` so the route layer can
    pass the snapshots straight into the audit logger. ``before`` is
    ``None`` when this is the first activation for this connector on the
    property; otherwise it captures the prior state (e.g. when reusing a
    previously deactivated row).
    """
    prop = _get_owned_property(db, ctx=ctx, property_id=property_id)
    connector = _get_active_connector(db, payload.connector_id)

    existing = (
        db.query(PropertyConnector)
        .filter(
            PropertyConnector.property_id == prop.id,
            PropertyConnector.connector_id == connector.id,
        )
        .first()
    )

    encrypted_secret = encrypt_secret(payload.api_secret)

    if existing is not None:
        if existing.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Connector is already active for this property",
            )
        before = _snapshot(existing)
        existing.api_key = payload.api_key
        existing.api_secret = encrypted_secret
        existing.scopes = payload.scopes
        existing.config = payload.config
        existing.base_url = payload.base_url
        existing.is_active = True
        db.add(existing)
        db.flush()
        db.refresh(existing)
        return _annotate(existing, connector), before, _snapshot(existing)

    pc = PropertyConnector(
        property_id=prop.id,
        connector_id=connector.id,
        api_key=payload.api_key,
        api_secret=encrypted_secret,
        scopes=payload.scopes,
        config=payload.config,
        base_url=payload.base_url,
        is_active=True,
    )
    db.add(pc)
    db.flush()
    db.refresh(pc)
    return _annotate(pc, connector), None, _snapshot(pc)


def list_connectors(
    db: Session, *, ctx: RequestContext, property_id: str | UUID
) -> list[PropertyConnector]:
    prop = _get_owned_property(db, ctx=ctx, property_id=property_id)
    rows = (
        db.query(PropertyConnector, Connector)
        .join(Connector, Connector.id == PropertyConnector.connector_id)
        .filter(PropertyConnector.property_id == prop.id)
        .order_by(PropertyConnector.created_at.desc())
        .all()
    )
    return [_annotate(pc, connector) for pc, connector in rows]


def _get_owned_property_connector(
    db: Session,
    *,
    ctx: RequestContext,
    property_id: str | UUID,
    property_connector_id: str | UUID,
) -> tuple[PropertyConnector, Connector | None]:
    prop = _get_owned_property(db, ctx=ctx, property_id=property_id)
    pc_id = _coerce_uuid(
        property_connector_id, detail="Invalid property_connector id"
    )
    row = (
        db.query(PropertyConnector, Connector)
        .outerjoin(Connector, Connector.id == PropertyConnector.connector_id)
        .filter(
            PropertyConnector.id == pc_id,
            PropertyConnector.property_id == prop.id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property connector not found",
        )
    return row[0], row[1]


def deactivate_connector(
    db: Session,
    *,
    ctx: RequestContext,
    property_id: str | UUID,
    property_connector_id: str | UUID,
) -> tuple[PropertyConnector, dict[str, Any], dict[str, Any], bool]:
    """Soft-deactivate a binding. Returns ``(pc, before, after, changed)``."""
    pc, connector = _get_owned_property_connector(
        db,
        ctx=ctx,
        property_id=property_id,
        property_connector_id=property_connector_id,
    )

    before = _snapshot(pc)
    if not pc.is_active:
        return _annotate(pc, connector), before, before, False

    pc.is_active = False
    db.add(pc)
    db.flush()
    db.refresh(pc)
    return _annotate(pc, connector), before, _snapshot(pc), True


def reactivate_connector(
    db: Session,
    *,
    ctx: RequestContext,
    property_id: str | UUID,
    property_connector_id: str | UUID,
) -> tuple[PropertyConnector, dict[str, Any], dict[str, Any], bool]:
    """Flip a previously-deactivated binding back to active.

    Reuses the encrypted credentials already stored on the row, so the
    tenant doesn't have to re-enter the api_key/api_secret to toggle a
    connector back on. The underlying connector must still be active in
    the catalog; otherwise we refuse with 400.

    Returns ``(pc, before, after, changed)``. ``changed`` is False when the
    binding was already active.
    """
    pc, connector = _get_owned_property_connector(
        db,
        ctx=ctx,
        property_id=property_id,
        property_connector_id=property_connector_id,
    )

    before = _snapshot(pc)
    if pc.is_active:
        return _annotate(pc, connector), before, before, False

    if connector is None or not connector.is_active or connector.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connector is no longer available",
        )

    pc.is_active = True
    db.add(pc)
    db.flush()
    db.refresh(pc)
    return _annotate(pc, connector), before, _snapshot(pc), True


def update_connector(
    db: Session,
    *,
    ctx: RequestContext,
    property_id: str | UUID,
    property_connector_id: str | UUID,
    payload: UpdateConnector,
) -> tuple[PropertyConnector, dict[str, Any], dict[str, Any], bool]:
    """Partial update for credential rotation / config / base_url tweaks.

    Only fields explicitly set on the payload are applied (Pydantic
    ``exclude_unset``). ``api_secret`` is re-encrypted on the way in. The
    ``is_active`` flag is intentionally NOT updatable here — use the
    activate/deactivate endpoints instead so audit semantics stay clean.

    Returns ``(pc, before, after, changed)``. ``changed`` is False when the
    payload was a no-op (every supplied field already matched the stored
    value, or only ``api_secret`` was rotated to itself — detected by
    comparing snapshots). Raises 400 if no fields were supplied.
    """
    pc, connector = _get_owned_property_connector(
        db,
        ctx=ctx,
        property_id=property_id,
        property_connector_id=property_connector_id,
    )

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided to update",
        )

    before = _snapshot(pc)

    if "api_key" in data and data["api_key"] is not None:
        pc.api_key = data["api_key"]
    if "api_secret" in data and data["api_secret"] is not None:
        pc.api_secret = encrypt_secret(data["api_secret"])
    if "scopes" in data:
        pc.scopes = data["scopes"]
    if "config" in data:
        pc.config = data["config"]
    if "base_url" in data:
        pc.base_url = data["base_url"]

    db.add(pc)
    db.flush()
    db.refresh(pc)
    after = _snapshot(pc)
    # Snapshot equality is the cleanest signal of a no-op for non-secret
    # fields. ``api_secret`` is excluded from the snapshot so we treat any
    # rotation as a change — callers want that audit trail even if the
    # plaintext happens to be identical (the ciphertext changes due to
    # Fernet's IV).
    changed = bool(
        ("api_secret" in data and data["api_secret"] is not None)
        or before != after
    )
    return _annotate(pc, connector), before, after, changed


def get_credentials(
    db: Session, *, property_connector_id: str | UUID
) -> ConnectorCredentials:
    """Internal accessor for workers — never exposed via HTTP.

    Returns the *decrypted* credentials plus everything a driver needs to
    fetch reviews (config, base_url, connector slug/name). Caller is
    responsible for tenant scoping when the call originates from a
    user-facing path; workers operate cross-tenant by design and pass the
    raw ``property_connector_id``.
    """
    pc_id = _coerce_uuid(
        property_connector_id, detail="Invalid property_connector id"
    )
    row = (
        db.query(PropertyConnector, Connector, Property)
        .join(Property, Property.id == PropertyConnector.property_id)
        .outerjoin(Connector, Connector.id == PropertyConnector.connector_id)
        .filter(
            PropertyConnector.id == pc_id,
            PropertyConnector.is_active.is_(True),
        )
        .first()
    )
    if row is None:
        # 404 covers both "row missing" and "row exists but deactivated" —
        # workers must not receive credentials for a binding the tenant has
        # explicitly turned off.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property connector not found or inactive",
        )
    pc, connector, prop = row

    plaintext_secret: str | None = None
    if pc.api_secret:
        try:
            plaintext_secret = decrypt_secret(pc.api_secret)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "connector_secret_decrypt_failed property_connector_id=%s error=%s",
                pc.id,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Stored connector secret cannot be decrypted",
            ) from exc

    return ConnectorCredentials(
        property_connector_id=pc.id,
        property_id=prop.id,
        tenant_id=prop.tenant_id,
        connector_id=pc.connector_id,
        connector_name=connector.name if connector is not None else None,
        api_key=pc.api_key,
        api_secret=plaintext_secret,
        scopes=pc.scopes,
        config=pc.config,
        base_url=pc.base_url,
        is_active=bool(pc.is_active),
    )


__all__ = [
    "activate_connector",
    "deactivate_connector",
    "get_credentials",
    "list_connectors",
    "reactivate_connector",
    "update_connector",
]
