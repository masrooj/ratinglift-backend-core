"""Pydantic schemas for the Property module."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Google Place IDs are opaque tokens documented as URL-safe base64-ish
# characters. We accept the conservative superset ``[A-Za-z0-9_-]`` and
# require a minimum length of 10 to reject obvious garbage.
_GOOGLE_PLACE_ID_PATTERN = r"^[A-Za-z0-9_-]{10,255}$"

_EXAMPLE_PLACE_ID = "ChIJN1t_tDeuEmsRUsoyG83frY4"
_EXAMPLE_MAPS_URL = "https://maps.app.goo.gl/abcdef123456"


def _validate_place_id(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    import re

    if not re.fullmatch(_GOOGLE_PLACE_ID_PATTERN, value):
        raise ValueError(
            "google_place_id must be 10-255 chars of [A-Za-z0-9_-]"
        )
    return value


def _validate_maps_url(value: str | None) -> str | None:
    """Light validation — must be an http(s) URL pointing at Google Maps.

    We deliberately do not parse the route/query fragments because Google
    has many shapes (``maps.app.goo.gl`` short links, ``www.google.com/maps``
    canonical links, embed iframes...). Tenants paste whatever the share
    sheet gives them.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    lowered = value.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise ValueError("google_maps_url must be an http(s) URL")
    if (
        "google.com/maps" not in lowered
        and "goo.gl/maps" not in lowered
        and "maps.app.goo.gl" not in lowered
    ):
        raise ValueError(
            "google_maps_url must point at google.com/maps, goo.gl/maps, "
            "or maps.app.goo.gl"
        )
    return value


class PropertyCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "Acme Coffee \u2014 Downtown",
                    "google_place_id": _EXAMPLE_PLACE_ID,
                    "google_maps_url": _EXAMPLE_MAPS_URL,
                }
            ]
        }
    )

    name: str = Field(..., min_length=1, max_length=255)
    google_place_id: str | None = Field(default=None, max_length=255)
    google_maps_url: str | None = Field(default=None, max_length=2048)

    @field_validator("google_place_id")
    @classmethod
    def _check_place_id(cls, v: str | None) -> str | None:
        return _validate_place_id(v)

    @field_validator("google_maps_url")
    @classmethod
    def _check_maps_url(cls, v: str | None) -> str | None:
        return _validate_maps_url(v)


class PropertyUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "Acme Coffee \u2014 Renamed"},
                {"google_place_id": _EXAMPLE_PLACE_ID},
                {"google_maps_url": _EXAMPLE_MAPS_URL},
                {"is_active": False},
            ]
        }
    )

    name: str | None = Field(default=None, min_length=1, max_length=255)
    google_place_id: str | None = Field(default=None, max_length=255)
    google_maps_url: str | None = Field(default=None, max_length=2048)
    is_active: bool | None = None

    @field_validator("google_place_id")
    @classmethod
    def _check_place_id(cls, v: str | None) -> str | None:
        return _validate_place_id(v)

    @field_validator("google_maps_url")
    @classmethod
    def _check_maps_url(cls, v: str | None) -> str | None:
        return _validate_maps_url(v)


class PropertyResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "1f3a4b6c-1234-4abc-9def-0123456789ab",
                    "name": "Acme Coffee \u2014 Downtown",
                    "google_place_id": _EXAMPLE_PLACE_ID,
                    "google_maps_url": _EXAMPLE_MAPS_URL,
                    "is_active": True,
                    "created_at": "2026-04-20T10:00:00Z",
                    "updated_at": "2026-04-20T10:00:00Z",
                }
            ]
        },
    )

    id: UUID
    name: str
    google_place_id: str | None = None
    google_maps_url: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None


class PropertyList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[PropertyResponse]


# ---------------------------------------------------------------------------
# Bulk operations
# ---------------------------------------------------------------------------


class PropertyBulkCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "items": [
                        {"name": "Acme \u2014 Downtown", "google_place_id": _EXAMPLE_PLACE_ID},
                        {"name": "Acme \u2014 Airport"},
                    ]
                }
            ]
        }
    )

    items: list[PropertyCreate] = Field(..., min_length=1, max_length=100)


class PropertyBulkCreateResultItem(BaseModel):
    index: int
    ok: bool
    property: PropertyResponse | None = None
    error: str | None = None
    status: int | None = None


class PropertyBulkCreateResponse(BaseModel):
    created: int
    failed: int
    results: list[PropertyBulkCreateResultItem]


class PropertyBulkDeactivate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "ids": [
                        "1f3a4b6c-1234-4abc-9def-0123456789ab",
                        "2a4b6c8d-5678-4abc-9def-0123456789cd",
                    ]
                }
            ]
        }
    )

    ids: list[UUID] = Field(..., min_length=1, max_length=200)


class PropertyBulkDeactivateResultItem(BaseModel):
    id: str
    ok: bool
    changed: bool | None = None
    error: str | None = None
    status: int | None = None


class PropertyBulkDeactivateResponse(BaseModel):
    deactivated: int
    unchanged: int
    failed: int
    results: list[PropertyBulkDeactivateResultItem]


# ---------------------------------------------------------------------------
# Audit log retrieval
# ---------------------------------------------------------------------------


class PropertyAuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID | None = None
    actor_type: str
    action: str
    entity: str
    entity_id: UUID | None = None
    before_value: Any | None = None
    after_value: Any | None = None
    ip_address: str | None = None
    timestamp: datetime

    @field_validator("actor_type", mode="before")
    @classmethod
    def _enum_to_str(cls, v: Any) -> str:
        return getattr(v, "value", v)


class PropertyAuditList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[PropertyAuditEntry]
