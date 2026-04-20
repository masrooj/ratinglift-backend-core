"""Pydantic schemas for the admin audit/security read APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AuditLogItem(BaseModel):
    id: UUID
    actor_id: UUID | None
    actor_type: str
    action: str
    entity: str
    entity_id: UUID | None
    before_value: Any | None
    after_value: Any | None
    ip_address: str | None
    timestamp: datetime


class AuditLogList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AuditLogItem]


class AdminActionItem(BaseModel):
    id: UUID
    admin_id: UUID
    action: str
    target_entity: str | None
    target_id: UUID | None
    target_tenant_id: UUID | None
    before_value: Any | None
    after_value: Any | None
    ip_address: str | None
    user_agent: str | None
    request_path: str | None
    extra: Any | None
    timestamp: datetime


class AdminActionList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AdminActionItem]


class LoginAttemptItem(BaseModel):
    id: UUID
    email: str
    ip_address: str | None
    user_agent: str | None
    success: bool
    reason: str | None
    timestamp: datetime


class LoginAttemptList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[LoginAttemptItem]


class IpBlocklistItem(BaseModel):
    id: UUID
    ip_address: str
    reason: str | None
    failed_attempts: int
    blocked_at: datetime
    expires_at: datetime | None


class IpBlocklistList(BaseModel):
    total: int
    items: list[IpBlocklistItem]


class IpBlockCreateRequest(BaseModel):
    ip_address: str = Field(min_length=2, max_length=64)
    reason: str | None = Field(default=None, max_length=255)
    duration_seconds: int = Field(default=3600, ge=60, le=60 * 60 * 24 * 30)


class SimpleMessage(BaseModel):
    message: str


# --------------------------- tenants & properties (admin views) ---------------------------


class AdminTenantItem(BaseModel):
    id: UUID
    name: str
    plan: str
    status: str
    created_at: datetime
    property_count: int = 0


class AdminTenantList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AdminTenantItem]


class AdminPropertyItem(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    google_place_id: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None


class AdminPropertyList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AdminPropertyItem]
