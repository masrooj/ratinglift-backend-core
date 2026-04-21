"""Pydantic schemas for the admin cross-tenant property-connector view.

Credentials are NEVER serialised here. The shape mirrors a flat report row
so an admin grid can render tenant, property, and connector context in a
single table without follow-up requests.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AdminPropertyConnectorRow(BaseModel):
    """One binding row enriched with tenant + property + connector context."""

    model_config = ConfigDict(from_attributes=True)

    # Binding row
    id: UUID
    is_active: bool
    created_at: datetime
    scopes: list[str] | None = None
    config: dict | None = None
    base_url: str | None = None

    # Tenant
    tenant_id: UUID
    tenant_name: str

    # Property
    property_id: UUID
    property_name: str

    # Connector
    connector_id: UUID
    connector_name: str
    connector_logo_url: str | None = None


class AdminPropertyConnectorList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AdminPropertyConnectorRow]
