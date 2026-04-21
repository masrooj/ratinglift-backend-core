"""Tests for the super-admin cross-tenant property-connector report."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.models.user import UserRole
from app.db.session import get_db
from app.main import app
from app.modules.admin.property_connectors import service as pc_admin_service


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


def _stub_admin(role: str = "SUPER_ADMIN", is_admin: bool = True):
    return SimpleNamespace(
        id=uuid4(),
        email="admin@example.com",
        role=UserRole(role),
        is_admin=is_admin,
        tenant_id=None,
    )


def _override_user(admin):
    from app.modules.auth.service import get_current_user

    app.dependency_overrides[get_current_user] = lambda: admin


def _clear():
    app.dependency_overrides.clear()


def _row(
    *,
    tenant_name: str = "Acme Hotels",
    property_name: str = "Acme Downtown",
    connector_name: str = "Google Reviews",
    is_active: bool = True,
) -> dict:
    return {
        "id": uuid4(),
        "is_active": is_active,
        "created_at": datetime.now(timezone.utc),
        "scopes": ["read"],
        "config": {"region": "us"},
        "base_url": "https://api.example.com",
        "tenant_id": uuid4(),
        "tenant_name": tenant_name,
        "property_id": uuid4(),
        "property_name": property_name,
        "connector_id": uuid4(),
        "connector_name": connector_name,
        "connector_logo_url": "https://cdn.example.com/g.png",
    }


# --------------------------- list ---------------------------


def test_admin_list_no_filters_returns_full_report(monkeypatch):
    rows = [
        _row(tenant_name="Acme", property_name="A1", connector_name="Google"),
        _row(tenant_name="Beta", property_name="B1", connector_name="Instagram"),
    ]
    captured: dict = {}

    def fake_list(db, **kwargs):
        captured.update(kwargs)
        return rows, len(rows)

    monkeypatch.setattr(pc_admin_service, "list_bindings", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/property-connectors",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert resp.headers["X-Total-Count"] == "2"
        names = [(r["tenant_name"], r["connector_name"]) for r in body["items"]]
        assert names == [("Acme", "Google"), ("Beta", "Instagram")]
        # No filters applied
        assert captured["tenant_id"] is None
        assert captured["property_id"] is None
        assert captured["connector_id"] is None
        assert captured["is_active"] is None
    finally:
        _clear()


def test_admin_list_filter_by_connector_id_propagates(monkeypatch):
    cid = uuid4()
    captured: dict = {}

    def fake_list(db, **kwargs):
        captured.update(kwargs)
        return [_row()], 1

    monkeypatch.setattr(pc_admin_service, "list_bindings", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/admin/property-connectors?connector_id={cid}",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        assert captured["connector_id"] == cid
    finally:
        _clear()


def test_admin_list_filter_by_tenant_and_property_and_active(monkeypatch):
    tid = uuid4()
    pid = uuid4()
    captured: dict = {}

    def fake_list(db, **kwargs):
        captured.update(kwargs)
        return [], 0

    monkeypatch.setattr(pc_admin_service, "list_bindings", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/property-connectors",
            params={
                "tenant_id": str(tid),
                "property_id": str(pid),
                "is_active": "false",
                "limit": 10,
                "offset": 5,
            },
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        assert captured["tenant_id"] == tid
        assert captured["property_id"] == pid
        assert captured["is_active"] is False
        assert captured["limit"] == 10
        assert captured["offset"] == 5
    finally:
        _clear()


def test_admin_list_response_never_exposes_credentials(monkeypatch):
    monkeypatch.setattr(
        pc_admin_service,
        "list_bindings",
        lambda db, **kwargs: ([_row()], 1),
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/property-connectors",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        for forbidden in (
            "api_key",
            "api_secret",
            "credentials",
            "encrypted_secret",
            "access_token",
            "refresh_token",
        ):
            assert forbidden not in item, f"{forbidden} leaked into response"
    finally:
        _clear()


# --------------------------- RBAC ---------------------------


def test_admin_list_rejects_non_admin_jwt():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/property-connectors",
            headers=_bearer(_admin_token(role="STAFF", is_admin=False)),
        )
        assert resp.status_code == 403
    finally:
        _clear()


def test_admin_list_rejects_missing_token():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/admin/property-connectors")
        assert resp.status_code == 401
    finally:
        _clear()


def test_admin_list_rejects_disallowed_admin_role():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin(role="FINANCE_ADMIN"))
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/property-connectors",
            headers=_bearer(_admin_token(role="FINANCE_ADMIN")),
        )
        assert resp.status_code == 403
    finally:
        _clear()


# ---------------------------------------------------------------------------
# Admin mutation routes — activate / update / deactivate / reactivate
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field
from uuid import UUID

import pytest

from app.core.dependencies import RequestContext
from app.modules.admin.property_connectors import routes as pc_admin_routes
from app.modules.property_connector import service as pc_service


@dataclass
class _FakePC:
    property_id: UUID
    connector_id: UUID
    is_active: bool = True
    config: dict | None = None
    base_url: str | None = None
    connector_name: str | None = "Google"
    connector_logo_url: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@pytest.fixture()
def captured_audit(monkeypatch):
    calls: list[dict] = []

    def fake_log_admin_action(_db, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(pc_admin_routes, "log_admin_action", fake_log_admin_action)
    return calls


def _admin_client():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    admin = _stub_admin()
    _override_user(admin)
    return TestClient(app), admin


def test_admin_activate_connector_routes_to_tenant_service_and_audits(
    monkeypatch, captured_audit
):
    property_id = uuid4()
    connector_id = uuid4()
    target_tenant_id = uuid4()
    pc = _FakePC(
        property_id=property_id,
        connector_id=connector_id,
        config={"location_id": "x"},
        base_url="https://api.example.com",
    )
    after = {
        "id": str(pc.id),
        "property_id": str(property_id),
        "connector_id": str(connector_id),
        "is_active": True,
        "scopes": ["reviews:read"],
        "config": {"location_id": "x"},
        "base_url": "https://api.example.com",
    }

    def fake_resolve(_db, *, admin_user_id, property_id):
        ctx = RequestContext(
            user_id=str(admin_user_id),
            tenant_id=str(target_tenant_id),
            role="SUPER_ADMIN",
            is_admin=True,
        )
        return ctx, property_id, target_tenant_id

    def fake_activate(_db, *, ctx, property_id, payload):
        # Cross-tenant context must reflect the property's actual tenant.
        assert ctx.tenant_id == str(target_tenant_id)
        assert ctx.is_admin is True
        assert payload.connector_id == connector_id
        assert payload.api_key == "pk"
        assert payload.api_secret == "sk"
        return pc, None, after

    monkeypatch.setattr(
        pc_admin_routes.pc_admin_service,
        "admin_context_for_property",
        fake_resolve,
    )
    monkeypatch.setattr(pc_service, "activate_connector", fake_activate)

    client, admin = _admin_client()
    try:
        resp = client.post(
            f"/api/v1/admin/property-connectors/properties/{property_id}/connectors",
            headers=_bearer(_admin_token()),
            json={
                "connector_id": str(connector_id),
                "api_key": "pk",
                "api_secret": "sk",
                "scopes": ["reviews:read"],
                "config": {"location_id": "x"},
                "base_url": "https://api.example.com",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Credential confidentiality.
        for forbidden in ("api_key", "api_secret", "scopes"):
            assert forbidden not in body
        assert body["id"] == str(pc.id)
        assert body["is_active"] is True

        assert captured_audit, "expected admin audit log"
        call = captured_audit[0]
        assert call["action"] == "connector_activated"
        assert call["target_entity"] == "property_connector"
        assert call["target_tenant_id"] == target_tenant_id
        assert call["admin_id"] == admin.id
        # No credentials or reason should be carried unless caller sent one.
        assert "api_secret" not in (call.get("after_value") or {})
        assert call.get("extra") is None
    finally:
        _clear()


def test_admin_deactivate_connector_resolves_binding_and_audits(
    monkeypatch, captured_audit
):
    pc_id = uuid4()
    property_id = uuid4()
    target_tenant_id = uuid4()
    pc = _FakePC(property_id=property_id, connector_id=uuid4(), is_active=False)
    before = {"id": str(pc.id), "is_active": True}
    after = {"id": str(pc.id), "is_active": False}

    def fake_resolve(_db, *, admin_user_id, property_connector_id):
        assert UUID(str(property_connector_id)) == pc_id
        ctx = RequestContext(
            user_id=str(admin_user_id),
            tenant_id=str(target_tenant_id),
            role="SUPER_ADMIN",
            is_admin=True,
        )
        return ctx, property_id, target_tenant_id

    def fake_deactivate(_db, *, ctx, property_id: UUID, property_connector_id):
        assert ctx.tenant_id == str(target_tenant_id)
        assert UUID(str(property_connector_id)) == pc_id
        return pc, before, after, True

    monkeypatch.setattr(
        pc_admin_routes.pc_admin_service,
        "admin_context_for_binding",
        fake_resolve,
    )
    monkeypatch.setattr(pc_service, "deactivate_connector", fake_deactivate)

    client, _admin = _admin_client()
    try:
        resp = client.delete(
            f"/api/v1/admin/property-connectors/{pc_id}",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_active"] is False
        assert "api_secret" not in body
        assert captured_audit[0]["action"] == "connector_deactivated"
        assert captured_audit[0]["target_tenant_id"] == target_tenant_id
    finally:
        _clear()


def test_admin_deactivate_noop_does_not_audit(monkeypatch, captured_audit):
    pc_id = uuid4()
    property_id = uuid4()
    pc = _FakePC(property_id=property_id, connector_id=uuid4(), is_active=False)

    def fake_resolve(_db, *, admin_user_id, property_connector_id):
        ctx = RequestContext(
            user_id=str(admin_user_id),
            tenant_id=str(uuid4()),
            role="SUPER_ADMIN",
            is_admin=True,
        )
        return ctx, property_id, uuid4()

    def fake_deactivate(_db, *, ctx, property_id, property_connector_id):
        return pc, {}, {}, False  # changed=False

    monkeypatch.setattr(
        pc_admin_routes.pc_admin_service,
        "admin_context_for_binding",
        fake_resolve,
    )
    monkeypatch.setattr(pc_service, "deactivate_connector", fake_deactivate)

    client, _admin = _admin_client()
    try:
        resp = client.delete(
            f"/api/v1/admin/property-connectors/{pc_id}",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        assert captured_audit == []
    finally:
        _clear()


def test_admin_reactivate_connector(monkeypatch, captured_audit):
    pc_id = uuid4()
    property_id = uuid4()
    target_tenant_id = uuid4()
    pc = _FakePC(property_id=property_id, connector_id=uuid4(), is_active=True)
    before = {"is_active": False}
    after = {"is_active": True}

    def fake_resolve(_db, *, admin_user_id, property_connector_id):
        ctx = RequestContext(
            user_id=str(admin_user_id),
            tenant_id=str(target_tenant_id),
            role="SUPER_ADMIN",
            is_admin=True,
        )
        return ctx, property_id, target_tenant_id

    def fake_reactivate(_db, *, ctx, property_id, property_connector_id):
        assert ctx.tenant_id == str(target_tenant_id)
        return pc, before, after, True

    monkeypatch.setattr(
        pc_admin_routes.pc_admin_service,
        "admin_context_for_binding",
        fake_resolve,
    )
    monkeypatch.setattr(pc_service, "reactivate_connector", fake_reactivate)

    client, _admin = _admin_client()
    try:
        resp = client.post(
            f"/api/v1/admin/property-connectors/{pc_id}/activate",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is True
        assert captured_audit[0]["action"] == "connector_activated"
        assert captured_audit[0]["target_tenant_id"] == target_tenant_id
    finally:
        _clear()


def test_admin_update_connector_rotates_and_audits(monkeypatch, captured_audit):
    pc_id = uuid4()
    property_id = uuid4()
    target_tenant_id = uuid4()
    pc = _FakePC(property_id=property_id, connector_id=uuid4())
    before = {"base_url": "https://old"}
    after = {"base_url": "https://new"}

    def fake_resolve(_db, *, admin_user_id, property_connector_id):
        ctx = RequestContext(
            user_id=str(admin_user_id),
            tenant_id=str(target_tenant_id),
            role="SUPER_ADMIN",
            is_admin=True,
        )
        return ctx, property_id, target_tenant_id

    def fake_update(_db, *, ctx, property_id, property_connector_id, payload):
        assert ctx.tenant_id == str(target_tenant_id)
        assert payload.api_secret == "rotated"
        return pc, before, after, True

    monkeypatch.setattr(
        pc_admin_routes.pc_admin_service,
        "admin_context_for_binding",
        fake_resolve,
    )
    monkeypatch.setattr(pc_service, "update_connector", fake_update)

    client, _admin = _admin_client()
    try:
        resp = client.put(
            f"/api/v1/admin/property-connectors/{pc_id}",
            headers=_bearer(_admin_token()),
            json={"api_secret": "rotated"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for forbidden in ("api_key", "api_secret", "scopes"):
            assert forbidden not in body
        assert captured_audit[0]["action"] == "connector_updated"
        assert captured_audit[0]["target_tenant_id"] == target_tenant_id
    finally:
        _clear()


# --------------------------- mutation RBAC ---------------------------


def test_admin_mutation_routes_reject_non_admin():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        non_admin = _bearer(_admin_token(role="STAFF", is_admin=False))
        # Each mutation must reject non-admins with 403.
        assert (
            client.post(
                f"/api/v1/admin/property-connectors/properties/{uuid4()}/connectors",
                headers=non_admin,
                json={
                    "connector_id": str(uuid4()),
                    "api_key": "k",
                    "api_secret": "s",
                },
            ).status_code
            == 403
        )
        assert (
            client.put(
                f"/api/v1/admin/property-connectors/{uuid4()}",
                headers=non_admin,
                json={"api_secret": "x"},
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/api/v1/admin/property-connectors/{uuid4()}",
                headers=non_admin,
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/admin/property-connectors/{uuid4()}/activate",
                headers=non_admin,
            ).status_code
            == 403
        )
    finally:
        _clear()


# --------------------------- service helpers ---------------------------


def test_admin_context_for_property_uses_property_tenant():
    """Service helper must build a ctx that mirrors the property's tenant."""
    from app.db.models.property import Property

    target_tenant = uuid4()
    prop = Property()
    prop.id = uuid4()
    prop.tenant_id = target_tenant

    class _Q:
        def filter(self, *_a, **_kw):
            return self

        def first(self):
            return prop

    class _DB:
        def query(self, *_a, **_kw):
            return _Q()

    admin_id = uuid4()
    ctx, pid, tid = pc_admin_routes.pc_admin_service.admin_context_for_property(
        _DB(), admin_user_id=admin_id, property_id=prop.id
    )
    assert ctx.tenant_id == str(target_tenant)
    assert ctx.is_admin is True
    assert ctx.user_id == str(admin_id)
    assert pid == prop.id
    assert tid == target_tenant


def test_admin_context_for_property_404_when_missing():
    from fastapi import HTTPException

    class _Q:
        def filter(self, *_a, **_kw):
            return self

        def first(self):
            return None

    class _DB:
        def query(self, *_a, **_kw):
            return _Q()

    with pytest.raises(HTTPException) as exc:
        pc_admin_routes.pc_admin_service.admin_context_for_property(
            _DB(), admin_user_id=uuid4(), property_id=uuid4()
        )
    assert exc.value.status_code == 404


