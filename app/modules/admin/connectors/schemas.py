"""Pydantic schemas for the admin connector master endpoints."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from app.core.config import settings


class ConnectorUpdate(BaseModel):
    """JSON body for ``PUT /admin/connectors/{id}``.

    Logo changes go through the dedicated ``POST/DELETE /{id}/logo`` endpoints
    (or are uploaded as part of ``POST /admin/connectors``).
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None
    display_order: int | None = Field(default=None, ge=0)


class ConnectorReorderItem(BaseModel):
    id: UUID
    display_order: int = Field(ge=0)


class ConnectorReorder(BaseModel):
    """Bulk reorder payload: list of (id, display_order) pairs."""

    items: list[ConnectorReorderItem] = Field(min_length=1, max_length=500)


class _LogoSerializer(BaseModel):
    """Mixin providing absolute-URL serialization for ``logo_url``."""

    @field_serializer("logo_url", check_fields=False)
    def _abs_logo_url(self, value: str | None) -> str | None:
        if not value:
            return None
        if value.startswith(("http://", "https://")):
            return value
        if value.startswith(settings.media_url_prefix):
            base = settings.app_public_url.rstrip("/")
            return f"{base}{value}"
        return value


class ConnectorResponse(_LogoSerializer):
    """Admin-side connector payload. Includes soft-delete bookkeeping."""

    id: UUID
    name: str
    logo_url: str | None = None
    is_active: bool
    is_deleted: bool = False
    deleted_at: datetime | None = None
    display_order: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True, "use_enum_values": True}


class ConnectorList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ConnectorResponse]


class TenantConnectorResponse(_LogoSerializer):
    """Tenant-side connector payload.

    Deliberately omits ``is_deleted`` / ``deleted_at`` — tenants only see
    live, active connectors so those fields would always be false/null
    and only add noise.
    """

    id: UUID
    name: str
    logo_url: str | None = None
    display_order: int = 0

    model_config = {"from_attributes": True}


class TenantConnectorList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[TenantConnectorResponse]
