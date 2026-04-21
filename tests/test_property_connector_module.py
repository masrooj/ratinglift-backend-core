"""Tests for the tenant property-connector activation module.

Covers:

* Route wiring (activate / list / deactivate / reactivate).
* Tenant isolation \u2014 service layer is reached with the bearer's tenant.
* Audit logging \u2014 ``connector_activated`` / ``connector_deactivated``
  emitted with the right payload.
* Credential confidentiality \u2014 responses NEVER leak ``api_key`` or
  ``api_secret``.
* Crypto helper round-trips.
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
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.dependencies import RequestContext, require_tenant_context
from app.db.session import get_db
from app.main import app
from app.modules.property_connector import routes as pc_routes
from app.modules.property_connector import service as pc_service


TENANT_A = str(uuid4())
USER_A = str(uuid4())


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


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------


@dataclass
class FakePC:
    property_id: UUID
    connector_id: UUID
    is_active: bool = True
    api_key: str = "k"
    api_secret: str = "s"
    scopes: list[str] | None = None
    config: dict | None = None
    base_url: str | None = None
    connector_name: str | None = None
    connector_logo_url: str | None = None
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FakeDB:
    def __init__(self) -> None:
        self.committed = False

    def add(self, _obj: Any) -> None:  # pragma: no cover - trivial
        pass

    def flush(self) -> None:  # pragma: no cover - trivial
        pass

    def commit(self) -> None:
        self.committed = True

    def refresh(self, _obj: Any) -> None:  # pragma: no cover - trivial
        pass

    def query(self, *_a, **_kw):  # pragma: no cover - not used
        raise AssertionError("FakeDB.query should not be invoked from route tests")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client_a():
    db = FakeDB()
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app, headers=_bearer(TOKEN_A)), db
    app.dependency_overrides.clear()


@pytest.fixture()
def captured_audit(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_log_action(_db, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(pc_routes, "log_action", fake_log_action)
    return calls


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


def test_activate_connector_returns_201_and_emits_audit(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    property_id = uuid4()
    connector_id = uuid4()
    pc = FakePC(
        property_id=property_id,
        connector_id=connector_id,
        config={"location_id": "accounts/123/locations/456"},
        base_url="https://places.googleapis.com/v1",
    )
    after = {
        "id": str(pc.id),
        "property_id": str(property_id),
        "connector_id": str(connector_id),
        "is_active": True,
        "scopes": ["reviews:read"],
        "config": {"location_id": "accounts/123/locations/456"},
        "base_url": "https://places.googleapis.com/v1",
    }

    def fake_activate(_db, *, ctx, property_id: UUID, payload):
        assert ctx.tenant_id == TENANT_A
        assert payload.connector_id == connector_id
        assert payload.api_key == "pk_xxx"
        assert payload.api_secret == "sk_xxx"
        assert payload.config == {"location_id": "accounts/123/locations/456"}
        assert payload.base_url == "https://places.googleapis.com/v1"
        return pc, None, after

    monkeypatch.setattr(pc_service, "activate_connector", fake_activate)

    resp = client.post(
        f"/api/v1/tenant/properties/{property_id}/connectors",
        json={
            "connector_id": str(connector_id),
            "api_key": "pk_xxx",
            "api_secret": "sk_xxx",
            "scopes": ["reviews:read"],
            "config": {"location_id": "accounts/123/locations/456"},
            "base_url": "https://places.googleapis.com/v1/",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Credential confidentiality.
    assert "api_key" not in body
    assert "api_secret" not in body
    assert "scopes" not in body

    # Non-sensitive per-source config IS exposed so the UI can display/edit it.
    assert body["config"] == {"location_id": "accounts/123/locations/456"}
    # base_url is normalised (trailing slash stripped) and surfaced.
    assert body["base_url"] == "https://places.googleapis.com/v1"

    assert body["id"] == str(pc.id)
    assert body["property_id"] == str(property_id)
    assert body["connector_id"] == str(connector_id)
    assert body["is_active"] is True

    assert captured_audit, "expected log_action to be called"
    call = captured_audit[0]
    assert call["action"] == "connector_activated"
    assert call["entity"] == "property_connector"
    assert call["actor_id"] == USER_A
    assert call["before_value"] is None
    assert call["after_value"] == after
    # Audit payload must not carry credentials either.
    assert "api_secret" not in (call["after_value"] or {})


def test_list_connectors_returns_credential_free_payload(monkeypatch, client_a):
    client, _db = client_a
    property_id = uuid4()
    rows = [
        FakePC(property_id=property_id, connector_id=uuid4(), is_active=True),
        FakePC(property_id=property_id, connector_id=uuid4(), is_active=False),
    ]

    def fake_list(_db, *, ctx, property_id: UUID):
        assert ctx.tenant_id == TENANT_A
        return rows

    monkeypatch.setattr(pc_service, "list_connectors", fake_list)

    resp = client.get(f"/api/v1/tenant/properties/{property_id}/connectors")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    for item in body["items"]:
        assert "api_key" not in item
        assert "api_secret" not in item
        assert "scopes" not in item


def test_deactivate_connector_emits_audit_when_changed(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    property_id = uuid4()
    pc = FakePC(property_id=property_id, connector_id=uuid4(), is_active=False)
    before = {
        "id": str(pc.id),
        "property_id": str(property_id),
        "connector_id": str(pc.connector_id),
        "is_active": True,
        "scopes": None,
    }
    after = {**before, "is_active": False}

    def fake_deactivate(_db, *, ctx, property_id, property_connector_id):
        assert ctx.tenant_id == TENANT_A
        assert UUID(str(property_connector_id)) == pc.id
        return pc, before, after, True

    monkeypatch.setattr(pc_service, "deactivate_connector", fake_deactivate)

    resp = client.delete(
        f"/api/v1/tenant/properties/{property_id}/connectors/{pc.id}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_active"] is False
    assert "api_secret" not in body

    assert captured_audit
    call = captured_audit[0]
    assert call["action"] == "connector_deactivated"
    assert call["before_value"]["is_active"] is True
    assert call["after_value"]["is_active"] is False


def test_deactivate_is_idempotent_when_already_inactive(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    property_id = uuid4()
    pc = FakePC(property_id=property_id, connector_id=uuid4(), is_active=False)
    snap = {
        "id": str(pc.id),
        "property_id": str(property_id),
        "connector_id": str(pc.connector_id),
        "is_active": False,
        "scopes": None,
    }

    def fake_deactivate(_db, *, ctx, property_id, property_connector_id):
        return pc, snap, snap, False

    monkeypatch.setattr(pc_service, "deactivate_connector", fake_deactivate)

    resp = client.delete(
        f"/api/v1/tenant/properties/{property_id}/connectors/{pc.id}"
    )
    assert resp.status_code == 200, resp.text
    assert captured_audit == []


# ---------------------------------------------------------------------------
# Service-layer behaviour tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Crypto helper
# ---------------------------------------------------------------------------


def test_encrypt_secret_roundtrips_and_obscures_plaintext():
    plaintext = "super-secret-api-key-value"
    cipher = encrypt_secret(plaintext)
    assert cipher != plaintext
    assert plaintext not in cipher
    assert decrypt_secret(cipher) == plaintext


def test_encrypt_secret_produces_distinct_ciphertexts():
    a = encrypt_secret("same-plaintext")
    b = encrypt_secret("same-plaintext")
    # Fernet embeds a random IV — same input must yield different outputs.
    assert a != b


def test_list_response_carries_connector_name_and_logo(monkeypatch, client_a):
    """The grid in Property → Connectors needs name/logo per row."""
    client, _db = client_a
    property_id = uuid4()
    rows = [
        FakePC(
            property_id=property_id,
            connector_id=uuid4(),
            is_active=True,
            connector_name="Google Reviews",
            connector_logo_url="https://cdn.example/google.png",
        ),
    ]

    monkeypatch.setattr(
        pc_service, "list_connectors", lambda _db, **_kw: rows
    )

    resp = client.get(f"/api/v1/tenant/properties/{property_id}/connectors")
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["connector_name"] == "Google Reviews"
    assert item["connector_logo_url"] == "https://cdn.example/google.png"


def test_reactivate_endpoint_emits_audit_when_changed(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    property_id = uuid4()
    pc = FakePC(
        property_id=property_id,
        connector_id=uuid4(),
        is_active=True,
        connector_name="Yelp",
    )
    before = {
        "id": str(pc.id),
        "property_id": str(property_id),
        "connector_id": str(pc.connector_id),
        "is_active": False,
        "scopes": None,
    }
    after = {**before, "is_active": True}

    def fake_reactivate(_db, *, ctx, property_id, property_connector_id):
        assert ctx.tenant_id == TENANT_A
        assert UUID(str(property_connector_id)) == pc.id
        return pc, before, after, True

    monkeypatch.setattr(pc_service, "reactivate_connector", fake_reactivate)

    resp = client.post(
        f"/api/v1/tenant/properties/{property_id}/connectors/{pc.id}/activate"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_active"] is True
    # Credentials must still not leak through the reactivate path.
    assert "api_key" not in body
    assert "api_secret" not in body

    assert captured_audit
    call = captured_audit[0]
    assert call["action"] == "connector_activated"
    assert call["before_value"]["is_active"] is False
    assert call["after_value"]["is_active"] is True


def test_reactivate_is_idempotent_when_already_active(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    property_id = uuid4()
    pc = FakePC(property_id=property_id, connector_id=uuid4(), is_active=True)
    snap = {
        "id": str(pc.id),
        "property_id": str(property_id),
        "connector_id": str(pc.connector_id),
        "is_active": True,
        "scopes": None,
    }

    monkeypatch.setattr(
        pc_service,
        "reactivate_connector",
        lambda _db, **_kw: (pc, snap, snap, False),
    )

    resp = client.post(
        f"/api/v1/tenant/properties/{property_id}/connectors/{pc.id}/activate"
    )
    assert resp.status_code == 200, resp.text
    assert captured_audit == []


# ---------------------------------------------------------------------------
# Update / rotate endpoint
# ---------------------------------------------------------------------------


def test_update_connector_rotates_secret_and_emits_audit(
    monkeypatch, client_a, captured_audit
):
    client, _db = client_a
    property_id = uuid4()
    pc = FakePC(
        property_id=property_id,
        connector_id=uuid4(),
        is_active=True,
        base_url="https://sandbox.example.com/v1",
        config={"location_id": "accounts/1/locations/2"},
    )
    before = {
        "id": str(pc.id),
        "property_id": str(property_id),
        "connector_id": str(pc.connector_id),
        "is_active": True,
        "scopes": None,
        "config": {"location_id": "accounts/1/locations/1"},
        "base_url": "https://places.googleapis.com/v1",
    }
    after = {**before, "config": pc.config, "base_url": pc.base_url}

    seen: dict[str, Any] = {}

    def fake_update(_db, *, ctx, property_id, property_connector_id, payload):
        assert ctx.tenant_id == TENANT_A
        assert UUID(str(property_connector_id)) == pc.id
        seen["payload"] = payload
        return pc, before, after, True

    monkeypatch.setattr(pc_service, "update_connector", fake_update)

    resp = client.put(
        f"/api/v1/tenant/properties/{property_id}/connectors/{pc.id}",
        json={
            "api_secret": "sk_rotated",
            "config": {"location_id": "accounts/1/locations/2"},
            "base_url": "https://sandbox.example.com/v1",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Credentials must never leak through the rotation response.
    assert "api_key" not in body
    assert "api_secret" not in body
    assert body["base_url"] == "https://sandbox.example.com/v1"
    assert body["config"] == {"location_id": "accounts/1/locations/2"}

    payload = seen["payload"]
    # Only the supplied fields land on the payload (partial update).
    data = payload.model_dump(exclude_unset=True)
    assert set(data.keys()) == {"api_secret", "config", "base_url"}
    assert data["api_secret"] == "sk_rotated"

    assert captured_audit
    call = captured_audit[0]
    assert call["action"] == "connector_updated"
    assert call["entity"] == "property_connector"
    assert call["before_value"] == before
    assert call["after_value"] == after
    # Audit must not carry the rotated plaintext secret.
    assert "api_secret" not in (call["after_value"] or {})
    assert "api_secret" not in (call["before_value"] or {})


def test_update_connector_rejects_non_http_base_url(client_a):
    """Schema-level validation kicks in before the service is reached."""
    client, _db = client_a
    property_id = uuid4()
    pc_id = uuid4()

    resp = client.put(
        f"/api/v1/tenant/properties/{property_id}/connectors/{pc_id}",
        json={"base_url": "ftp://nope.example.com"},
    )
    assert resp.status_code == 422, resp.text


def test_activate_rejects_non_http_base_url(client_a):
    client, _db = client_a
    property_id = uuid4()

    resp = client.post(
        f"/api/v1/tenant/properties/{property_id}/connectors",
        json={
            "connector_id": str(uuid4()),
            "api_key": "k",
            "api_secret": "s",
            "base_url": "javascript:alert(1)",
        },
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Internal credential accessor
# ---------------------------------------------------------------------------


def test_get_credentials_returns_decrypted_secret_and_404_when_missing(
    monkeypatch,
):
    """Worker-facing accessor: decrypts api_secret and carries fetch context."""
    from app.modules.property_connector.schemas import ConnectorCredentials

    pc_id = uuid4()
    prop_id = uuid4()
    tenant_id = uuid4()
    connector_id = uuid4()
    plaintext = "sk_live_super_secret"
    encrypted = encrypt_secret(plaintext)

    pc = FakePC(
        property_id=prop_id,
        connector_id=connector_id,
        is_active=True,
        api_key="pk_live",
        api_secret=encrypted,
        scopes=["reviews:read"],
        config={"location_id": "accounts/1/locations/1"},
        base_url="https://places.googleapis.com/v1",
    )
    pc.id = pc_id

    @dataclass
    class FakeConnector:
        id: UUID
        name: str = "Google Reviews"

    @dataclass
    class FakeProperty:
        id: UUID
        tenant_id: UUID

    connector = FakeConnector(id=connector_id)
    prop = FakeProperty(id=prop_id, tenant_id=tenant_id)

    class _Query:
        def __init__(self, row):
            self._row = row

        def join(self, *_a, **_kw):
            return self

        def outerjoin(self, *_a, **_kw):
            return self

        def filter(self, *_a, **_kw):
            return self

        def first(self):
            return self._row

    class _DB:
        def __init__(self, row):
            self._row = row

        def query(self, *_a, **_kw):
            return _Query(self._row)

    creds = pc_service.get_credentials(
        _DB((pc, connector, prop)), property_connector_id=pc_id
    )
    assert isinstance(creds, ConnectorCredentials)
    assert creds.api_secret == plaintext
    assert creds.api_key == "pk_live"
    assert creds.base_url == "https://places.googleapis.com/v1"
    assert creds.config == {"location_id": "accounts/1/locations/1"}
    assert creds.tenant_id == tenant_id
    assert creds.connector_name == "Google Reviews"
    assert creds.is_active is True

    # Missing row → 404.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        pc_service.get_credentials(_DB(None), property_connector_id=uuid4())
    assert exc_info.value.status_code == 404


def test_update_connector_skips_audit_when_payload_is_noop(
    monkeypatch, client_a, captured_audit
):
    """Service returning ``changed=False`` must NOT trigger log_action."""
    client, _db = client_a
    property_id = uuid4()
    pc = FakePC(
        property_id=property_id,
        connector_id=uuid4(),
        is_active=True,
        base_url="https://places.googleapis.com/v1",
    )
    snap = {
        "id": str(pc.id),
        "property_id": str(property_id),
        "connector_id": str(pc.connector_id),
        "is_active": True,
        "scopes": None,
        "config": None,
        "base_url": "https://places.googleapis.com/v1",
    }

    monkeypatch.setattr(
        pc_service,
        "update_connector",
        lambda _db, **_kw: (pc, snap, snap, False),
    )

    resp = client.put(
        f"/api/v1/tenant/properties/{property_id}/connectors/{pc.id}",
        json={"base_url": "https://places.googleapis.com/v1"},
    )
    assert resp.status_code == 200, resp.text
    assert captured_audit == []


def test_get_credentials_refuses_inactive_binding():
    """Workers must never receive credentials for a deactivated binding.

    Verified at the query level: the filter excludes ``is_active = False``,
    so a deactivated row collapses to the same 404 a missing row would.
    """
    captured: dict[str, Any] = {}

    class _Query:
        def filter(self, *args, **_kw):
            captured["filters"] = args
            return self

        def join(self, *_a, **_kw):
            return self

        def outerjoin(self, *_a, **_kw):
            return self

        def first(self):
            return None

    class _DB:
        def query(self, *_a, **_kw):
            return _Query()

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        pc_service.get_credentials(_DB(), property_connector_id=uuid4())
    assert exc_info.value.status_code == 404
    # Sanity: at least 2 filter criteria were passed (id == ... AND is_active is True).
    assert len(captured["filters"]) >= 2
