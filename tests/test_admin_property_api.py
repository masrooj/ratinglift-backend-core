"""Tests for the admin tenants/properties read APIs."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import jwt
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.models.tenant import PlanType, TenantStatus
from app.db.models.user import UserRole
from app.db.session import get_db
from app.main import app
from app.modules.admin import property_routes as admin_property_routes
from app.modules.property import service as property_service


# --------------------------- helpers ---------------------------


def _admin_token(role: str = "SUPER_ADMIN", is_admin: bool = True) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": str(uuid4()),
        "tenant_id": None,
        "role": role,
        "is_admin": is_admin,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    secret = settings.admin_jwt_secret if is_admin else settings.jwt_secret
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _stub_admin(role: str = "SUPER_ADMIN"):
    return SimpleNamespace(
        id=uuid4(),
        email="admin@example.com",
        role=UserRole(role),
        is_admin=True,
        tenant_id=None,
    )


@dataclass
class _FakeTenant:
    name: str
    plan: PlanType = PlanType.starter
    status: TenantStatus = TenantStatus.active
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _FakeProp:
    name: str
    tenant_id: UUID
    google_place_id: str | None = None
    is_active: bool = True
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _override_user(admin):
    from app.modules.auth.service import get_current_user

    app.dependency_overrides[get_current_user] = lambda: admin


def _clear():
    app.dependency_overrides.clear()


# --------------------------- /admin/tenants ---------------------------


def test_list_tenants_returns_paginated_payload_with_property_counts(monkeypatch):
    tenant_a = _FakeTenant(name="Acme")
    tenant_b = _FakeTenant(name="Beta")

    class _TenantQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_a, **_kw):
            return self

        def order_by(self, *_a, **_kw):
            return self

        def offset(self, _n):
            return self

        def limit(self, _n):
            return self

        def with_entities(self, *_a, **_kw):
            return self

        def scalar(self):
            return len(self._rows)

        def all(self):
            return self._rows

    class _CountQuery:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_a, **_kw):
            return self

        def group_by(self, *_a, **_kw):
            return self

        def all(self):
            return self._rows

    db = MagicMock()

    def _query(*args):
        first = args[0]
        if getattr(first, "__name__", "") == "Tenant":
            return _TenantQuery([tenant_a, tenant_b])
        return _CountQuery([(tenant_a.id, 3), (tenant_b.id, 1)])

    db.query.side_effect = _query

    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/admin/tenants", headers=_bearer(_admin_token()))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        names = {item["name"]: item["property_count"] for item in body["items"]}
        assert names == {"Acme": 3, "Beta": 1}
    finally:
        _clear()


def test_get_tenant_404_for_unknown_id():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = None
    db.query.return_value = q

    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/admin/tenants/{uuid4()}",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 404
    finally:
        _clear()


def test_admin_tenants_route_rejects_non_admin_jwt():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/tenants",
            headers=_bearer(_admin_token(role="STAFF", is_admin=False)),
        )
        assert resp.status_code == 403
    finally:
        _clear()


def test_admin_tenants_route_rejects_missing_token():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/admin/tenants")
        assert resp.status_code == 401
    finally:
        _clear()


# --------------------------- /admin/tenants/{id}/properties ---------------------------


def test_list_tenant_properties_calls_service(monkeypatch):
    tenant = _FakeTenant(name="Acme")
    props = [
        _FakeProp(name="HQ", tenant_id=tenant.id),
        _FakeProp(name="Branch", tenant_id=tenant.id, is_active=False),
    ]

    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = tenant
    db.query.return_value = q

    seen: dict[str, Any] = {}

    def fake_admin_list(_db, *, tenant_id, is_active=None, limit=100, offset=0):
        seen["tenant_id"] = tenant_id
        seen["is_active"] = is_active
        seen["limit"] = limit
        seen["offset"] = offset
        return props, len(props)

    monkeypatch.setattr(
        property_service, "admin_list_tenant_properties", fake_admin_list
    )

    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/admin/tenants/{tenant.id}/properties?is_active=false&limit=10",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert {item["name"] for item in body["items"]} == {"HQ", "Branch"}
        assert str(seen["tenant_id"]) == str(tenant.id)
        assert seen["is_active"] is False
        assert seen["limit"] == 10
    finally:
        _clear()


def test_list_tenant_properties_404_when_tenant_missing():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = None
    db.query.return_value = q

    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/admin/tenants/{uuid4()}/properties",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 404
    finally:
        _clear()


# --------------------------- /admin/properties (cross-tenant search) ---------------------------


def test_cross_tenant_property_search_passes_filters(monkeypatch):
    tid = uuid4()
    props = [_FakeProp(name="HQ", tenant_id=tid)]

    seen: dict[str, Any] = {}

    def fake_search(
        _db, *, tenant_id=None, is_active=None, q=None, limit=100, offset=0
    ):
        seen.update(
            tenant_id=tenant_id,
            is_active=is_active,
            q=q,
            limit=limit,
            offset=offset,
        )
        return props, len(props)

    monkeypatch.setattr(property_service, "admin_search_properties", fake_search)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/properties",
            params={
                "tenant_id": str(tid),
                "is_active": "true",
                "q": "HQ",
                "limit": 25,
                "offset": 5,
            },
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "HQ"
        assert str(seen["tenant_id"]) == str(tid)
        assert seen["is_active"] is True
        assert seen["q"] == "HQ"
        assert seen["limit"] == 25
        assert seen["offset"] == 5
    finally:
        _clear()


def test_admin_property_search_role_gated_to_read_roles():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin(role="FINANCE_ADMIN"))
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/properties",
            headers=_bearer(_admin_token(role="FINANCE_ADMIN")),
        )
        assert resp.status_code == 403
    finally:
        _clear()
