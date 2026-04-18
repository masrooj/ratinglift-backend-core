"""Tests for tenant-isolation middleware and dependencies.

These tests stand up an isolated FastAPI app that mirrors the production
middleware/dependency wiring and exercises:

* unauthenticated access (rejected)
* same-tenant access (allowed)
* cross-tenant access (rejected)
* admin access to admin routes (allowed)
* admin access to tenant routes (rejected unless ?tenant_id= is supplied)
* the ``filter_by_tenant`` query helper
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.dependencies import (
    RequestContext,
    filter_by_tenant,
    get_current_context,
    require_admin_context,
    require_tenant_context,
)
from app.core.middleware import RequestContextMiddleware, TenantContextMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    role: str = "STAFF",
    is_admin: bool = False,
    secret: str | None = None,
    expired: bool = False,
) -> str:
    now = datetime.now(timezone.utc)
    exp = now - timedelta(minutes=5) if expired else now + timedelta(minutes=15)
    payload = {
        "user_id": user_id or str(uuid4()),
        "tenant_id": tenant_id,
        "role": role,
        "is_admin": is_admin,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(
        payload,
        secret or (settings.admin_jwt_secret if is_admin else settings.jwt_secret),
        algorithm=settings.jwt_algorithm,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(TenantContextMiddleware)

    @app.get("/api/v1/things")
    def list_things(ctx: RequestContext = Depends(require_tenant_context)):
        return ctx.as_dict()

    @app.get("/api/v1/admin/things")
    def list_admin_things(ctx: RequestContext = Depends(require_admin_context)):
        return ctx.as_dict()

    @app.get("/api/v1/whoami")
    def whoami(ctx: RequestContext = Depends(get_current_context)):
        return ctx.as_dict()

    @app.get("/api/v1/auth/ping")  # public path
    def public_ping():
        return {"ok": True}

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_build_app())


TENANT_A = str(uuid4())
TENANT_B = str(uuid4())


# ---------------------------------------------------------------------------
# Unauthenticated requests
# ---------------------------------------------------------------------------


def test_request_without_token_is_rejected(client: TestClient):
    resp = client.get("/api/v1/things")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"


def test_request_with_invalid_token_is_rejected(client: TestClient):
    resp = client.get("/api/v1/things", headers=_bearer("not-a-real-jwt"))
    assert resp.status_code == 401


def test_request_with_expired_token_is_rejected(client: TestClient):
    token = _make_token(tenant_id=TENANT_A, expired=True)
    resp = client.get("/api/v1/things", headers=_bearer(token))
    assert resp.status_code == 401


def test_public_path_does_not_require_token(client: TestClient):
    resp = client.get("/api/v1/auth/ping")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_tenant_user_can_access_own_tenant_route(client: TestClient):
    token = _make_token(tenant_id=TENANT_A)
    resp = client.get("/api/v1/things", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == TENANT_A
    assert resp.json()["is_admin"] is False


def test_tenant_user_cannot_see_other_tenants_data(client: TestClient):
    """Tenant A's token never produces a context with Tenant B's id."""
    token_a = _make_token(tenant_id=TENANT_A)
    resp = client.get("/api/v1/things", headers=_bearer(token_a))
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] != TENANT_B


def test_tenant_user_with_no_tenant_id_in_token_is_rejected(client: TestClient):
    token = _make_token(tenant_id=None)
    resp = client.get("/api/v1/things", headers=_bearer(token))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Admin routing
# ---------------------------------------------------------------------------


def test_non_admin_cannot_access_admin_route(client: TestClient):
    token = _make_token(tenant_id=TENANT_A, is_admin=False)
    resp = client.get("/api/v1/admin/things", headers=_bearer(token))
    assert resp.status_code == 403
    assert "Admin" in resp.json()["detail"]


def test_admin_can_access_admin_route(client: TestClient):
    token = _make_token(tenant_id=None, role="SUPER_ADMIN", is_admin=True)
    resp = client.get("/api/v1/admin/things", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is True


def test_admin_on_tenant_route_without_param_is_rejected(client: TestClient):
    token = _make_token(tenant_id=None, role="SUPER_ADMIN", is_admin=True)
    resp = client.get("/api/v1/things", headers=_bearer(token))
    assert resp.status_code == 403
    assert "tenant_id" in resp.json()["detail"]


def test_admin_can_access_any_tenant_with_param(client: TestClient):
    token = _make_token(tenant_id=None, role="SUPER_ADMIN", is_admin=True)
    resp = client.get(
        "/api/v1/things",
        params={"tenant_id": TENANT_B},
        headers=_bearer(token),
    )
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == TENANT_B
    assert resp.json()["is_admin"] is True


# ---------------------------------------------------------------------------
# filter_by_tenant helper
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self) -> None:
        self.filters: list = []

    def filter(self, expr) -> "_FakeQuery":
        self.filters.append(expr)
        return self


class _FakeColumn:
    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other):  # pragma: no cover - trivial
        return ("eq", self.name, other)


class _FakeModel:
    tenant_id = _FakeColumn("tenant_id")


def test_filter_by_tenant_appends_where_clause():
    q = _FakeQuery()
    out = filter_by_tenant(q, _FakeModel, TENANT_A)
    assert out is q
    assert q.filters == [("eq", "tenant_id", TENANT_A)]


def test_filter_by_tenant_rejects_missing_tenant():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        filter_by_tenant(_FakeQuery(), _FakeModel, None)
    assert excinfo.value.status_code == 403


def test_filter_by_tenant_rejects_model_without_column():
    class NoTenant:
        pass

    with pytest.raises(ValueError):
        filter_by_tenant(_FakeQuery(), NoTenant, TENANT_A)


# ---------------------------------------------------------------------------
# Authorization header parsing
# ---------------------------------------------------------------------------


def test_malformed_authorization_header_is_rejected(client: TestClient):
    """A header that doesn't look like ``Bearer <token>`` is treated as missing."""
    resp = client.get("/api/v1/things", headers={"Authorization": "garbage-no-scheme"})
    assert resp.status_code == 401


def test_basic_auth_scheme_is_rejected(client: TestClient):
    resp = client.get("/api/v1/things", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == 401


def test_bearer_with_empty_token_is_rejected(client: TestClient):
    resp = client.get("/api/v1/things", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_token_signed_with_unknown_secret_is_rejected(client: TestClient):
    bogus = _make_token(tenant_id=TENANT_A, secret="totally-unrelated-secret")
    resp = client.get("/api/v1/things", headers=_bearer(bogus))
    assert resp.status_code == 401


def test_lowercase_authorization_header_is_accepted(client: TestClient):
    token = _make_token(tenant_id=TENANT_A)
    resp = client.get("/api/v1/things", headers={"authorization": f"Bearer {token}"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Cross-tenant scenarios (Tenant A vs Tenant B)
# ---------------------------------------------------------------------------


def test_tenant_a_and_tenant_b_get_isolated_contexts(client: TestClient):
    """Two users from different tenants hitting the same route get disjoint scope."""
    token_a = _make_token(tenant_id=TENANT_A)
    token_b = _make_token(tenant_id=TENANT_B)

    resp_a = client.get("/api/v1/things", headers=_bearer(token_a))
    resp_b = client.get("/api/v1/things", headers=_bearer(token_b))

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_a.json()["tenant_id"] == TENANT_A
    assert resp_b.json()["tenant_id"] == TENANT_B
    assert resp_a.json()["tenant_id"] != resp_b.json()["tenant_id"]


def test_tenant_a_token_cannot_forge_tenant_id_query_param(client: TestClient):
    """Non-admin users can't pivot to another tenant by passing ?tenant_id=."""
    token_a = _make_token(tenant_id=TENANT_A)
    resp = client.get(
        "/api/v1/things",
        params={"tenant_id": TENANT_B},
        headers=_bearer(token_a),
    )
    # Context still resolves to the token's tenant; query param is ignored
    # for non-admin callers.
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == TENANT_A


# ---------------------------------------------------------------------------
# assert_same_tenant helper
# ---------------------------------------------------------------------------


def test_assert_same_tenant_allows_match():
    from app.core.dependencies import assert_same_tenant

    ctx = RequestContext(user_id="u1", tenant_id=TENANT_A, role="STAFF", is_admin=False)
    # Should not raise.
    assert_same_tenant(ctx, TENANT_A)


def test_assert_same_tenant_rejects_mismatch():
    from fastapi import HTTPException

    from app.core.dependencies import assert_same_tenant

    ctx = RequestContext(user_id="u1", tenant_id=TENANT_A, role="STAFF", is_admin=False)
    with pytest.raises(HTTPException) as excinfo:
        assert_same_tenant(ctx, TENANT_B)
    assert excinfo.value.status_code == 403


def test_assert_same_tenant_admin_with_override_must_still_match():
    from fastapi import HTTPException

    from app.core.dependencies import assert_same_tenant

    # Admin opted-in to tenant A via ?tenant_id=A.
    ctx = RequestContext(user_id="admin", tenant_id=TENANT_A, role="SUPER_ADMIN", is_admin=True)
    assert_same_tenant(ctx, TENANT_A)
    with pytest.raises(HTTPException):
        assert_same_tenant(ctx, TENANT_B)


def test_assert_same_tenant_pure_admin_allowed_anywhere():
    from app.core.dependencies import assert_same_tenant

    # Admin with no tenant override (e.g. on /api/v1/admin/* routes).
    ctx = RequestContext(user_id="admin", tenant_id=None, role="SUPER_ADMIN", is_admin=True)
    assert_same_tenant(ctx, TENANT_A)
    assert_same_tenant(ctx, TENANT_B)


# ---------------------------------------------------------------------------
# get_current_context behaviour
# ---------------------------------------------------------------------------


def test_whoami_returns_full_context(client: TestClient):
    user_id = str(uuid4())
    token = _make_token(user_id=user_id, tenant_id=TENANT_A, role="MANAGER")
    resp = client.get("/api/v1/whoami", headers=_bearer(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "user_id": user_id,
        "tenant_id": TENANT_A,
        "role": "MANAGER",
        "is_admin": False,
    }


# ---------------------------------------------------------------------------
# Violation logging
# ---------------------------------------------------------------------------


def test_missing_token_violation_is_logged(client: TestClient, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="app.core.middleware"):
        resp = client.get("/api/v1/things")
    assert resp.status_code == 401
    assert any("tenant_isolation_violation" in rec.message for rec in caplog.records)
    assert any("missing_token" in rec.message for rec in caplog.records)


def test_non_admin_on_admin_route_violation_is_logged(client: TestClient, caplog):
    import logging

    token = _make_token(tenant_id=TENANT_A, is_admin=False)
    with caplog.at_level(logging.WARNING, logger="app.core.middleware"):
        resp = client.get("/api/v1/admin/things", headers=_bearer(token))
    assert resp.status_code == 403
    assert any("non_admin_on_admin_route" in rec.message for rec in caplog.records)


def test_cross_tenant_access_violation_is_logged(caplog):
    import logging

    from fastapi import HTTPException

    from app.core.dependencies import assert_same_tenant

    ctx = RequestContext(user_id="u1", tenant_id=TENANT_A, role="STAFF", is_admin=False)
    with caplog.at_level(logging.WARNING, logger="app.core.dependencies"):
        with pytest.raises(HTTPException):
            assert_same_tenant(ctx, TENANT_B)
    assert any("cross_tenant_access" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Public paths
# ---------------------------------------------------------------------------


def test_public_path_accepts_invalid_token(client: TestClient):
    """Auth endpoints must remain reachable even if the caller sends junk."""
    resp = client.get("/api/v1/auth/ping", headers=_bearer("not-a-jwt"))
    assert resp.status_code == 200


def test_health_endpoint_does_not_require_auth():
    """Sanity check against the real production app (without the DB lifespan)."""
    from app.main import app as production_app

    # Skip the startup/shutdown lifespan — it tries to connect to Postgres,
    # which isn't available in unit-test environments.
    c = TestClient(production_app)
    resp = c.get("/live")
    assert resp.status_code == 200
