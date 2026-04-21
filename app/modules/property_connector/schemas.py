"""Pydantic schemas for the property-connector activation module.

Credentials (``api_key``/``api_secret``) are accepted on the activation
payload but NEVER returned in any response model.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


_BASE_URL_EXAMPLE = "https://places.googleapis.com/v1"


def _validate_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    lowered = value.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise ValueError("base_url must be an http(s) URL")
    return value.rstrip("/")


class ActivateConnector(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "connector_id": "1f3a4b6c-1234-4abc-9def-0123456789ab",
                    "api_key": "pk_live_xxx",
                    "api_secret": "sk_live_xxx",
                    "scopes": ["reviews:read"],
                    "config": {"location_id": "accounts/123/locations/456"},
                    "base_url": _BASE_URL_EXAMPLE,
                }
            ]
        }
    )

    connector_id: UUID
    api_key: str = Field(..., min_length=1, max_length=512)
    api_secret: str = Field(..., min_length=1, max_length=2048)
    scopes: list[str] | None = None
    # Free-form per-source settings. Each connector driver owns its own
    # sub-schema (e.g. Google Business Profile ``location_id``, Yelp
    # ``business_id``). Validation happens in the driver, not here.
    config: dict | None = None
    # Optional override of the API base URL the worker should hit when
    # fetching reviews for this binding. Drivers fall back to their built-in
    # default when omitted.
    base_url: str | None = Field(default=None, max_length=2048)

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, v: str | None) -> str | None:
        return _validate_base_url(v)


class UpdateConnector(BaseModel):
    """Partial-update payload for rotating credentials / config / base_url.

    Every field is optional; omitted fields are left unchanged. ``api_key``
    and ``api_secret`` may be rotated independently. Sending ``scopes``,
    ``config``, or ``base_url`` as ``null`` clears the column.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"api_secret": "sk_live_rotated"},
                {"config": {"location_id": "accounts/123/locations/789"}},
                {"base_url": "https://sandbox.example.com/v1"},
            ]
        }
    )

    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    api_secret: str | None = Field(default=None, min_length=1, max_length=2048)
    scopes: list[str] | None = None
    config: dict | None = None
    base_url: str | None = Field(default=None, max_length=2048)

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, v: str | None) -> str | None:
        return _validate_base_url(v)


class ConnectorResponse(BaseModel):
    """Public-safe view of a ``PropertyConnector`` row.

    Intentionally omits ``api_key`` / ``api_secret`` / ``scopes`` to prevent
    credential leakage through any API surface. ``connector_name`` and
    ``connector_logo_url`` are denormalised in so the tenant UI can render
    the property → connectors grid without a second round-trip per row.
    ``config`` and ``base_url`` are non-sensitive per-source settings so
    the UI can display / edit them.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    connector_id: UUID
    connector_name: str | None = None
    connector_logo_url: str | None = None
    config: dict | None = None
    base_url: str | None = None
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class ConnectorCredentials:
    """Internal-only carrier of decrypted credentials for worker use.

    Returned by :func:`app.modules.property_connector.service.get_credentials`
    and never serialised to a response. The plaintext ``api_secret`` lives
    only in memory for the duration of the worker call.
    """

    property_connector_id: UUID
    property_id: UUID
    tenant_id: UUID
    connector_id: UUID
    connector_name: str | None
    api_key: str
    api_secret: str | None
    scopes: list[str] | None
    config: dict | None
    base_url: str | None
    is_active: bool


class ConnectorList(BaseModel):
    total: int
    items: list[ConnectorResponse]
