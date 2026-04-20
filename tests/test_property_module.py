"""Tests for the tenant Property management module.

These exercise:

* Route wiring (create / list / get / update / delete).
* Tenant isolation (cross-tenant access is rejected).
* Audit logging (``log_action`` is invoked with the expected payload, and
  receives the request so IP/UA can be captured).
* Soft-delete semantics on DELETE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.dependencies import RequestContext, require_tenant_context
from app.db.session import get_db
from app.main import app
from app.modules.property import routes as property_routes
from app.modules.property import service as property_service


TENANT_A = str(uuid4())
TENANT_B = str(uuid4())
USER_A = str(uuid4())
USER_B = str(uuid4())


def _make_token(*, user_id: str, tenant_id: str, role: str = "STAFF") -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "is_admin": False,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


TOKEN_A = _make_token(user_id=USER_A, tenant_id=TENANT_A)
TOKEN_B = _make_token(user_id=USER_B, tenant_id=TENANT_B)


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeProperty:
    name: str
    tenant_id: UUID
    google_place_id: str | None = None
    is_active: bool = True
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FakeDB:
    """Minimal stand-in for a SQLAlchemy session.

    Service functions never reach this fake because we monkeypatch them; it
    exists purely so dependency injection has *something* to yield.
    """

    def __init__(self) -> None:
        self.committed = False
        self.flushed = False

    def add(self, _obj: Any) -> None:  # pragma: no cover - trivial
        pass

    def flush(self) -> None:
        self.flushed = True

    def commit(self) -> None:
        self.committed = True

    def refresh(self, _obj: Any) -> None:  # pragma: no cover - trivial
        pass

    def query(self, *_a, **_kw):  # pragma: no cover - not used in route tests
        raise AssertionError("FakeDB.query should not be invoked from route tests")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _override_tenant(tenant_id: str, user_id: str) -> RequestContext:
    return RequestContext(
        user_id=user_id, tenant_id=tenant_id, role="STAFF", is_admin=False
    )


@pytest.fixture()
def client_a(monkeypatch):
    db = FakeDB()
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app, headers=_bearer(TOKEN_A)), db
    app.dependency_overrides.clear()


@pytest.fixture()
def client_b():
    db = FakeDB()
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app, headers=_bearer(TOKEN_B)), db
    app.dependency_overrides.clear()


@pytest.fixture()
def captured_audit(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_log_action(_db, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(property_routes, "log_action", fake_log_action)
    return calls


# ---------------------------------------------------------------------------
# Service-shape tests (call into the service via monkeypatched routes)
# ---------------------------------------------------------------------------


def test_create_property_returns_201_and_emits_audit(monkeypatch, client_a, captured_audit):
    client, _db = client_a
    place_id = "ChIJN1t_tDeu" + "A" * 10  # valid google place-id shape
    created = FakeProperty(name="Acme HQ", tenant_id=UUID(TENANT_A), google_place_id=place_id)

    def fake_create(_db, *, ctx, payload):
        assert ctx.tenant_id == TENANT_A
        assert payload.name == "Acme HQ"
        assert payload.google_place_id == place_id
        return created

    monkeypatch.setattr(property_service, "create_property", fake_create)

    resp = client.post(
        "/api/v1/tenant/properties",
        json={"name": "Acme HQ", "google_place_id": place_id},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Acme HQ"
    assert body["google_place_id"] == place_id
    assert body["is_active"] is True
    assert body["id"] == str(created.id)

    assert captured_audit, "expected log_action to be called"
    call = captured_audit[0]
    assert call["action"] == "property.create"
    assert call["entity"] == "property"
    assert call["actor_id"] == USER_A
    assert call["actor_type"] == "tenant"
    assert call["before_value"] is None
    assert call["after_value"]["name"] == "Acme HQ"
    assert call["after_value"]["tenant_id"] == TENANT_A
    assert call["request"] is not None


def test_list_properties_calls_service_with_tenant_context(monkeypatch, client_a):
    client, _db = client_a
    rows = [
        FakeProperty(name=f"P{i}", tenant_id=UUID(TENANT_A))
        for i in range(2)
    ]
    seen: dict[str, Any] = {}

    def fake_get(_db, *, ctx, is_active=None, q=None, limit=100, offset=0):
        seen["tenant_id"] = ctx.tenant_id
        seen["is_active"] = is_active
        seen["limit"] = limit
        seen["offset"] = offset
        return rows, len(rows)

    monkeypatch.setattr(property_service, "get_properties", fake_get)

    resp = client.get("/api/v1/tenant/properties?limit=50&offset=10&is_active=true")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 10
    assert len(body["items"]) == 2
    assert seen == {
        "tenant_id": TENANT_A,
        "is_active": True,
        "limit": 50,
        "offset": 10,
    }


def test_get_property_by_id_returns_property_for_owning_tenant(
    monkeypatch, client_a
):
    client, _db = client_a
    prop = FakeProperty(name="Alpha", tenant_id=UUID(TENANT_A))

    def fake_get_one(_db, *, ctx, property_id):
        assert ctx.tenant_id == TENANT_A
        assert str(property_id) == str(prop.id)
        return prop

    monkeypatch.setattr(property_service, "get_property_by_id", fake_get_one)

    resp = client.get(f"/api/v1/tenant/properties/{prop.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(prop.id)
    assert resp.json()["name"] == "Alpha"


def test_update_property_emits_audit_with_before_and_after(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    prop = FakeProperty(
        name="New Name", tenant_id=UUID(TENANT_A), is_active=True
    )
    before = {
        "id": str(prop.id),
        "tenant_id": TENANT_A,
        "name": "Old Name",
        "google_place_id": None,
        "is_active": True,
    }
    after = {**before, "name": "New Name"}

    def fake_update(_db, *, ctx, property_id, payload):
        assert ctx.tenant_id == TENANT_A
        assert payload.name == "New Name"
        return prop, before, after

    monkeypatch.setattr(property_service, "update_property", fake_update)

    resp = client.put(
        f"/api/v1/tenant/properties/{prop.id}",
        json={"name": "New Name"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New Name"

    assert captured_audit, "expected audit log call"
    call = captured_audit[0]
    assert call["action"] == "property.update"
    assert call["entity"] == "property"
    assert call["before_value"]["name"] == "Old Name"
    assert call["after_value"]["name"] == "New Name"
    assert call["actor_id"] == USER_A


def test_delete_property_soft_deletes_and_emits_audit(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    prop = FakeProperty(name="Bye", tenant_id=UUID(TENANT_A), is_active=False)
    before = {
        "id": str(prop.id),
        "tenant_id": TENANT_A,
        "name": "Bye",
        "google_place_id": None,
        "is_active": True,
    }
    after = {**before, "is_active": False}

    def fake_deactivate(_db, *, ctx, property_id):
        assert ctx.tenant_id == TENANT_A
        return prop, before, after, True

    monkeypatch.setattr(property_service, "deactivate_property", fake_deactivate)

    resp = client.delete(f"/api/v1/tenant/properties/{prop.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    assert captured_audit
    call = captured_audit[0]
    assert call["action"] == "property.deactivate"
    assert call["before_value"]["is_active"] is True
    assert call["after_value"]["is_active"] is False


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_property_returning_other_tenant_id_is_rejected_at_service_layer(
    monkeypatch, client_b
):
    """If the persistence layer ever returned a row from another tenant,
    the service's ``filter_by_tenant`` would never match it. We assert
    ``get_property_by_id`` raises 404 when the tenant scope yields nothing.
    """
    client, _db = client_b
    foreign_id = uuid4()

    def fake_get_one(_db, *, ctx, property_id):
        # Simulate the realistic outcome of `filter_by_tenant`: no row.
        from fastapi import HTTPException, status

        assert ctx.tenant_id == TENANT_B  # cannot read tenant-A data
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Property not found"
        )

    monkeypatch.setattr(property_service, "get_property_by_id", fake_get_one)

    resp = client.get(f"/api/v1/tenant/properties/{foreign_id}")
    assert resp.status_code == 404


def test_unauthenticated_request_is_rejected():
    """No token, no override → middleware/dep stack rejects the call."""
    # Ensure no leftover overrides from other tests.
    app.dependency_overrides.pop(require_tenant_context, None)
    app.dependency_overrides.pop(get_db, None)
    client = TestClient(app)
    resp = client.get("/api/v1/tenant/properties")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Service unit-tests (no HTTP, snapshot helper)
# ---------------------------------------------------------------------------


def test_snapshot_is_json_safe():
    prop = FakeProperty(
        name="X",
        tenant_id=UUID(TENANT_A),
        google_place_id="gp",
        is_active=True,
    )
    snap = property_service._snapshot(prop)
    assert snap["name"] == "X"
    assert snap["tenant_id"] == TENANT_A
    assert snap["google_place_id"] == "gp"
    assert snap["is_active"] is True
    assert isinstance(snap["id"], str)


# ---------------------------------------------------------------------------
# Validation, idempotent DELETE, and activate endpoint
# ---------------------------------------------------------------------------


def test_create_property_rejects_invalid_google_place_id(client_a):
    client, _db = client_a
    resp = client.post(
        "/api/v1/tenant/properties",
        json={"name": "Acme", "google_place_id": "too-short"},
    )
    assert resp.status_code == 422
    assert "google_place_id" in resp.text


def test_create_property_treats_blank_google_place_id_as_null(monkeypatch, client_a):
    client, _db = client_a
    seen: dict[str, Any] = {}
    created = FakeProperty(name="Acme", tenant_id=UUID(TENANT_A), google_place_id=None)

    def fake_create(_db, *, ctx, payload):
        seen["google_place_id"] = payload.google_place_id
        return created

    monkeypatch.setattr(property_service, "create_property", fake_create)

    resp = client.post(
        "/api/v1/tenant/properties",
        json={"name": "Acme", "google_place_id": "   "},
    )
    assert resp.status_code == 201, resp.text
    assert seen["google_place_id"] is None


def test_delete_already_inactive_property_is_idempotent_no_audit(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    prop = FakeProperty(name="Bye", tenant_id=UUID(TENANT_A), is_active=False)
    snapshot_dict = {
        "id": str(prop.id),
        "tenant_id": TENANT_A,
        "name": "Bye",
        "google_place_id": None,
        "is_active": False,
    }

    def fake_deactivate(_db, *, ctx, property_id):
        return prop, snapshot_dict, snapshot_dict, False  # changed=False

    monkeypatch.setattr(property_service, "deactivate_property", fake_deactivate)

    resp = client.delete(f"/api/v1/tenant/properties/{prop.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False
    assert captured_audit == [], "no audit log expected when nothing changed"


def test_activate_property_emits_audit_and_returns_active(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    prop = FakeProperty(name="Back", tenant_id=UUID(TENANT_A), is_active=True)
    before = {
        "id": str(prop.id),
        "tenant_id": TENANT_A,
        "name": "Back",
        "google_place_id": None,
        "is_active": False,
    }
    after = {**before, "is_active": True}

    def fake_activate(_db, *, ctx, property_id):
        assert ctx.tenant_id == TENANT_A
        return prop, before, after, True

    monkeypatch.setattr(property_service, "activate_property", fake_activate)

    resp = client.post(f"/api/v1/tenant/properties/{prop.id}/activate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is True

    assert captured_audit
    call = captured_audit[0]
    assert call["action"] == "property.activate"
    assert call["before_value"]["is_active"] is False
    assert call["after_value"]["is_active"] is True


def test_activate_already_active_property_is_idempotent_no_audit(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    prop = FakeProperty(name="Active", tenant_id=UUID(TENANT_A), is_active=True)
    snap = {
        "id": str(prop.id),
        "tenant_id": TENANT_A,
        "name": "Active",
        "google_place_id": None,
        "is_active": True,
    }

    def fake_activate(_db, *, ctx, property_id):
        return prop, snap, snap, False

    monkeypatch.setattr(property_service, "activate_property", fake_activate)

    resp = client.post(f"/api/v1/tenant/properties/{prop.id}/activate")
    assert resp.status_code == 200, resp.text
    assert captured_audit == []




# ---------------------------------------------------------------------------
# 409 conflict on duplicate google_place_id + place-id update path
# ---------------------------------------------------------------------------


def test_create_property_returns_409_on_duplicate_place_id(monkeypatch, client_a):
    from fastapi import HTTPException, status

    client, _db = client_a

    def fake_create(_db, *, ctx, payload):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A property with this google_place_id already exists for this tenant",
        )

    monkeypatch.setattr(property_service, "create_property", fake_create)

    resp = client.post(
        "/api/v1/tenant/properties",
        json={"name": "Dup", "google_place_id": "ChIJN1t_tDeu" + "A" * 10},
    )
    assert resp.status_code == 409
    assert "google_place_id" in resp.json()["detail"]


def test_update_property_accepts_google_place_id_change(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    new_pid = "ChIJ" + "B" * 16
    prop = FakeProperty(
        name="Same",
        tenant_id=UUID(TENANT_A),
        google_place_id=new_pid,
        is_active=True,
    )
    before = {
        "id": str(prop.id),
        "tenant_id": TENANT_A,
        "name": "Same",
        "google_place_id": None,
        "is_active": True,
    }
    after = {**before, "google_place_id": new_pid}

    seen: dict = {}

    def fake_update(_db, *, ctx, property_id, payload):
        seen["place_id"] = payload.google_place_id
        return prop, before, after

    monkeypatch.setattr(property_service, "update_property", fake_update)

    resp = client.put(
        f"/api/v1/tenant/properties/{prop.id}",
        json={"google_place_id": new_pid},
    )
    assert resp.status_code == 200, resp.text
    assert seen["place_id"] == new_pid
    assert resp.json()["google_place_id"] == new_pid
    assert captured_audit[0]["after_value"]["google_place_id"] == new_pid


def test_update_property_rejects_invalid_google_place_id(client_a):
    client, _db = client_a
    resp = client.put(
        f"/api/v1/tenant/properties/{uuid4()}",
        json={"google_place_id": "short"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# q= search, X-Total-Count header, bulk endpoints, audit endpoint
# ---------------------------------------------------------------------------


def test_list_properties_passes_q_and_sets_total_count_header(monkeypatch, client_a):
    client, _db = client_a
    seen = {}

    def fake_get(_db, *, ctx, is_active=None, q=None, limit=100, offset=0):
        seen["q"] = q
        return [], 0

    monkeypatch.setattr(property_service, "get_properties", fake_get)

    resp = client.get("/api/v1/tenant/properties?q=acme")
    assert resp.status_code == 200, resp.text
    assert seen["q"] == "acme"
    assert resp.headers["X-Total-Count"] == "0"


def test_bulk_create_returns_207_and_audits_each_success(monkeypatch, client_a, captured_audit):
    client, _db = client_a
    place_id = "ChIJ" + "A" * 20
    p1 = FakeProperty(name="P1", tenant_id=UUID(TENANT_A), google_place_id=place_id)

    def fake_bulk_create(_db, *, ctx, payloads):
        return [
            {
                "index": 0,
                "ok": True,
                "property": p1,
                "after": {"id": str(p1.id), "tenant_id": TENANT_A,
                          "name": "P1", "google_place_id": place_id, "is_active": True},
            },
            {
                "index": 1,
                "ok": False,
                "error": "A property with this google_place_id already exists for this tenant",
                "status": 409,
            },
        ]

    monkeypatch.setattr(property_service, "bulk_create_properties", fake_bulk_create)

    resp = client.post(
        "/api/v1/tenant/properties/bulk",
        json={"items": [
            {"name": "P1", "google_place_id": place_id},
            {"name": "P2", "google_place_id": place_id},
        ]},
    )
    assert resp.status_code == 207, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert body["failed"] == 1
    assert body["results"][0]["ok"] is True
    assert body["results"][0]["property"]["id"] == str(p1.id)
    assert body["results"][1]["ok"] is False
    assert body["results"][1]["status"] == 409
    # Only the successful row should be audited.
    assert len(captured_audit) == 1
    assert captured_audit[0]["action"] == "property.create"


def test_bulk_deactivate_returns_207_with_mixed_outcomes(monkeypatch, client_a, captured_audit):
    client, _db = client_a
    p_changed = FakeProperty(name="A", tenant_id=UUID(TENANT_A), is_active=False)
    p_unchanged = FakeProperty(name="B", tenant_id=UUID(TENANT_A), is_active=False)
    snap_changed_before = {"id": str(p_changed.id), "tenant_id": TENANT_A,
                           "name": "A", "google_place_id": None, "is_active": True}
    snap_changed_after = {**snap_changed_before, "is_active": False}
    snap_unchanged = {"id": str(p_unchanged.id), "tenant_id": TENANT_A,
                      "name": "B", "google_place_id": None, "is_active": False}
    missing_id = uuid4()

    def fake_bulk_deact(_db, *, ctx, property_ids):
        return [
            {"id": str(p_changed.id), "ok": True, "changed": True,
             "property": p_changed, "before": snap_changed_before, "after": snap_changed_after},
            {"id": str(p_unchanged.id), "ok": True, "changed": False,
             "property": p_unchanged, "before": snap_unchanged, "after": snap_unchanged},
            {"id": str(missing_id), "ok": False, "error": "Property not found", "status": 404},
        ]

    monkeypatch.setattr(property_service, "bulk_deactivate_properties", fake_bulk_deact)

    resp = client.post(
        "/api/v1/tenant/properties/bulk-deactivate",
        json={"ids": [str(p_changed.id), str(p_unchanged.id), str(missing_id)]},
    )
    assert resp.status_code == 207, resp.text
    body = resp.json()
    assert body["deactivated"] == 1
    assert body["unchanged"] == 1
    assert body["failed"] == 1
    # Only the genuinely-changed row triggers an audit log.
    assert len(captured_audit) == 1
    assert captured_audit[0]["action"] == "property.deactivate"
    assert captured_audit[0]["before_value"]["is_active"] is True
    assert captured_audit[0]["after_value"]["is_active"] is False


def test_get_property_audit_returns_paginated_envelope_and_header(monkeypatch, client_a):
    client, _db = client_a
    pid = uuid4()

    @dataclass
    class FakeAuditRow:
        id: UUID = field(default_factory=uuid4)
        actor_id: UUID | None = field(default_factory=uuid4)
        actor_type: str = "user"
        action: str = "property.create"
        entity: str = "property"
        entity_id: UUID = field(default_factory=lambda: pid)
        before_value: Any = None
        after_value: Any = field(default_factory=lambda: {"name": "A"})
        ip_address: str | None = "127.0.0.1"
        timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    rows = [FakeAuditRow(entity_id=pid) for _ in range(3)]
    seen = {}

    def fake_get_audit(_db, *, ctx, property_id, limit=100, offset=0):
        seen["property_id"] = str(property_id)
        seen["limit"] = limit
        seen["offset"] = offset
        return rows, 7

    monkeypatch.setattr(property_service, "get_property_audit_logs", fake_get_audit)

    resp = client.get(f"/api/v1/tenant/properties/{pid}/audit?limit=3&offset=4")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 7
    assert body["limit"] == 3
    assert body["offset"] == 4
    assert len(body["items"]) == 3
    assert body["items"][0]["action"] == "property.create"
    assert resp.headers["X-Total-Count"] == "7"
    assert seen == {"property_id": str(pid), "limit": 3, "offset": 4}


def test_get_property_audit_returns_404_when_property_missing(monkeypatch, client_a):
    from fastapi import HTTPException, status as http_status
    client, _db = client_a

    def fake_get_audit(_db, *, ctx, property_id, limit=100, offset=0):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Property not found")

    monkeypatch.setattr(property_service, "get_property_audit_logs", fake_get_audit)
    resp = client.get(f"/api/v1/tenant/properties/{uuid4()}/audit")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Authorization wiring (HTTPBearer + OpenAPI security)
# ---------------------------------------------------------------------------


def test_property_routes_reject_request_without_token():
    """No Authorization header => middleware returns 401 before the route runs."""
    anon = TestClient(app)
    resp = anon.post("/api/v1/tenant/properties", json={"name": "X"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"


def test_property_routes_reject_invalid_token():
    """Garbage bearer token => still treated as unauthenticated."""
    bad = TestClient(app, headers={"Authorization": "Bearer not-a-jwt"})
    resp = bad.get("/api/v1/tenant/properties")
    assert resp.status_code == 401


def test_admin_route_rejects_tenant_token():
    """A tenant JWT must not unlock /api/v1/admin/* even when present."""
    tenant = TestClient(app, headers=_bearer(TOKEN_A))
    resp = tenant.get("/api/v1/admin/tenants")
    assert resp.status_code == 403


def test_property_router_advertises_bearer_security_in_openapi():
    """Swagger Authorize must apply to the property endpoints.

    Regression guard: previously the router declared no security scheme, so
    Swagger UI did not send the bearer token after the user clicked
    Authorize, producing spurious 401s on POST /properties.
    """
    spec = app.openapi()
    components_schemes = spec.get("components", {}).get("securitySchemes", {})
    assert any(
        scheme.get("type") == "http" and scheme.get("scheme") == "bearer"
        for scheme in components_schemes.values()
    ), "Expected an HTTP bearer security scheme to be registered"

    create_op = spec["paths"]["/api/v1/tenant/properties"]["post"]
    assert "security" in create_op and create_op["security"], (
        "POST /api/v1/tenant/properties must declare a security requirement"
    )

    audit_op = spec["paths"]["/api/v1/tenant/properties/{property_id}/audit"]["get"]
    assert "security" in audit_op and audit_op["security"], (
        "GET /properties/{id}/audit must declare a security requirement"
    )

    admin_op = spec["paths"]["/api/v1/admin/tenants"]["get"]
    assert "security" in admin_op and admin_op["security"], (
        "GET /api/v1/admin/tenants must declare a security requirement"
    )


# ---------------------------------------------------------------------------
# Admin-impersonation: admin tokens may operate on tenant routes when they
# explicitly opt-in via ?tenant_id=<uuid>.
# ---------------------------------------------------------------------------


ADMIN_USER_ID = str(uuid4())
ADMIN_TOKEN = _make_token(user_id=ADMIN_USER_ID, tenant_id="", role="SUPER_ADMIN")
# Re-encode with is_admin=True (the helper defaults to False).
import jwt as _jwt
_now = datetime.now(timezone.utc)
ADMIN_TOKEN = _jwt.encode(
    {
        "user_id": ADMIN_USER_ID,
        "tenant_id": None,
        "role": "SUPER_ADMIN",
        "is_admin": True,
        "jti": uuid4().hex,
        "iat": int(_now.timestamp()),
        "exp": int((_now + timedelta(minutes=15)).timestamp()),
    },
    settings.jwt_secret,
    algorithm=settings.jwt_algorithm,
)


def test_admin_token_on_tenant_route_without_tenant_id_param_is_rejected():
    client = TestClient(app, headers=_bearer(ADMIN_TOKEN))
    resp = client.get("/api/v1/tenant/properties")
    assert resp.status_code == 403
    assert "tenant_id" in resp.json()["detail"]


def test_admin_token_on_tenant_route_with_tenant_id_param_is_authorized(
    monkeypatch, captured_audit
):
    """Admin can create a property on behalf of a tenant by passing ?tenant_id=."""
    db = FakeDB()
    app.dependency_overrides[get_db] = lambda: db

    seen: dict[str, Any] = {}
    place_id = "ChIJ" + "Z" * 20
    created = FakeProperty(
        name="Admin-made", tenant_id=UUID(TENANT_A), google_place_id=place_id
    )

    def fake_create(_db, *, ctx, payload):
        seen["tenant_id"] = ctx.tenant_id
        seen["user_id"] = ctx.user_id
        seen["is_admin"] = ctx.is_admin
        return created

    monkeypatch.setattr(property_service, "create_property", fake_create)

    try:
        client = TestClient(app, headers=_bearer(ADMIN_TOKEN))
        resp = client.post(
            f"/api/v1/tenant/properties?tenant_id={TENANT_A}",
            json={"name": "Admin-made", "google_place_id": place_id},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201, resp.text
    # Tenant context resolved to the override, attribution to the admin user.
    assert seen == {
        "tenant_id": TENANT_A,
        "user_id": ADMIN_USER_ID,
        "is_admin": True,
    }
    # Audit log records the admin as the actor (so cross-tenant writes are traceable).
    assert captured_audit
    assert captured_audit[0]["actor_id"] == ADMIN_USER_ID
    assert captured_audit[0]["action"] == "property.create"
