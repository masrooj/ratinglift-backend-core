"""Integration tests for admin audit/security read APIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import jwt
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.models.admin_action_log import AdminActionLog
from app.db.models.audit_log import ActorType, AuditLog
from app.db.models.ip_blocklist import IpBlocklist
from app.db.models.login_attempt import LoginAttempt
from app.db.models.user import UserRole
from app.db.session import get_db
from app.main import app


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


class _Q:
    def __init__(self, rows):
        self._rows = list(rows)
        self._offset = 0
        self._limit = None

    def filter(self, *_a, **_kw):
        return self

    def order_by(self, *_a, **_kw):
        return self

    def offset(self, n):
        self._offset = n
        return self

    def limit(self, n):
        self._limit = n
        return self

    def with_entities(self, *_a, **_kw):
        return self

    def scalar(self):
        return len(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        rows = self._rows[self._offset :]
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows


def _fake_db(rows_by_model: dict):
    db = MagicMock()
    db.added = []
    db.add.side_effect = lambda obj: db.added.append(obj)
    db.flush.return_value = None
    db.commit.return_value = None
    db.refresh.side_effect = lambda obj: None
    db.query.side_effect = lambda model: _Q(rows_by_model.get(model, []))
    return db


def _override(db, admin):
    from app.modules.auth.service import get_current_user

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: admin


def _clear():
    app.dependency_overrides.clear()


# --------------------------- audit_logs ---------------------------


def test_list_audit_logs_returns_paginated_payload():
    rows = [
        AuditLog(
            id=uuid4(),
            actor_id=uuid4(),
            actor_type=ActorType.user,
            action="property.create",
            entity="property",
            entity_id=uuid4(),
            before_value=None,
            after_value={"name": "Acme"},
            ip_address="1.1.1.1",
            timestamp=datetime.now(timezone.utc),
        )
        for _ in range(3)
    ]
    db = _fake_db({AuditLog: rows})
    _override(db, _stub_admin())
    try:
        client = TestClient(app)
        r = client.get(
            "/api/v1/admin/audit-logs?limit=10",
            headers=_bearer(_admin_token()),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3
        assert body["items"][0]["entity"] == "property"
        assert body["items"][0]["action"] == "property.create"
    finally:
        _clear()


def test_admin_routes_reject_non_admin_jwt():
    db = _fake_db({})
    _override(db, _stub_admin())
    try:
        client = TestClient(app)
        token = _admin_token(role="STAFF", is_admin=False)
        r = client.get("/api/v1/admin/audit-logs", headers=_bearer(token))
        assert r.status_code == 403
    finally:
        _clear()


def test_admin_routes_reject_missing_token():
    db = _fake_db({})
    _override(db, _stub_admin())
    try:
        client = TestClient(app)
        r = client.get("/api/v1/admin/audit-logs")
        assert r.status_code == 401
    finally:
        _clear()


def test_require_role_blocks_disallowed_admin_role():
    # Token is_admin=true (passes middleware) but user role is not in
    # READ_ROLES → require_role returns 403.
    db = _fake_db({AuditLog: []})
    _override(db, _stub_admin(role="FINANCE_ADMIN"))
    try:
        client = TestClient(app)
        r = client.get(
            "/api/v1/admin/audit-logs",
            headers=_bearer(_admin_token(role="FINANCE_ADMIN")),
        )
        assert r.status_code == 403
    finally:
        _clear()


# --------------------------- admin_actions ---------------------------


def test_list_admin_actions_returns_rows():
    rows = [
        AdminActionLog(
            id=uuid4(),
            admin_id=uuid4(),
            action="tenant.suspend",
            target_entity="tenant",
            target_id=uuid4(),
            target_tenant_id=uuid4(),
            before_value={"is_active": True},
            after_value={"is_active": False},
            ip_address="9.9.9.9",
            user_agent="curl/8",
            request_path="/api/v1/admin/tenants/x",
            extra=None,
            timestamp=datetime.now(timezone.utc),
        )
    ]
    db = _fake_db({AdminActionLog: rows})
    _override(db, _stub_admin(role="COMPLIANCE_ADMIN"))
    try:
        client = TestClient(app)
        r = client.get(
            "/api/v1/admin/admin-actions",
            headers=_bearer(_admin_token(role="COMPLIANCE_ADMIN")),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["action"] == "tenant.suspend"
        assert body["items"][0]["after_value"] == {"is_active": False}
    finally:
        _clear()


# --------------------------- login_attempts ---------------------------


def test_list_login_attempts_returns_rows():
    rows = [
        LoginAttempt(
            id=uuid4(),
            email="user@example.com",
            ip_address="2.2.2.2",
            user_agent="UA",
            success=False,
            reason="invalid_credentials",
            timestamp=datetime.now(timezone.utc),
        )
    ]
    db = _fake_db({LoginAttempt: rows})
    _override(db, _stub_admin())
    try:
        client = TestClient(app)
        r = client.get(
            "/api/v1/admin/login-attempts?success=false",
            headers=_bearer(_admin_token()),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["success"] is False
        assert body["items"][0]["reason"] == "invalid_credentials"
    finally:
        _clear()


# --------------------------- ip_blocklist ---------------------------


def test_list_ip_blocklist_returns_rows():
    rows = [
        IpBlocklist(
            id=uuid4(),
            ip_address="8.8.8.8",
            reason="failed_login_threshold_exceeded",
            failed_attempts=25,
            blocked_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    ]
    db = _fake_db({IpBlocklist: rows})
    _override(db, _stub_admin())
    try:
        client = TestClient(app)
        r = client.get(
            "/api/v1/admin/ip-blocklist",
            headers=_bearer(_admin_token()),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["ip_address"] == "8.8.8.8"
    finally:
        _clear()


def test_manual_block_ip_creates_row_and_logs_admin_action(monkeypatch):
    db = _fake_db({IpBlocklist: []})
    _override(db, _stub_admin())

    fake_row = IpBlocklist(
        id=uuid4(),
        ip_address="4.4.4.4",
        reason="abuse",
        failed_attempts=0,
        blocked_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    import app.modules.admin.audit_routes as routes
    monkeypatch.setattr(routes, "block_ip", lambda *a, **kw: fake_row)
    log_calls = []
    monkeypatch.setattr(
        routes,
        "log_admin_action",
        lambda db, **kw: log_calls.append(kw),
    )

    try:
        client = TestClient(app)
        r = client.post(
            "/api/v1/admin/ip-blocklist",
            headers=_bearer(_admin_token()),
            json={"ip_address": "4.4.4.4", "reason": "abuse", "duration_seconds": 3600},
        )
        assert r.status_code == 201, r.text
        assert r.json()["ip_address"] == "4.4.4.4"
        assert log_calls and log_calls[0]["action"] == "ip.block"
        assert log_calls[0]["after_value"]["ip_address"] == "4.4.4.4"
    finally:
        _clear()


def test_manual_unblock_ip_404_when_not_blocked(monkeypatch):
    db = _fake_db({IpBlocklist: []})
    _override(db, _stub_admin())

    import app.modules.admin.audit_routes as routes
    monkeypatch.setattr(routes, "unblock_ip", lambda *a, **kw: False)

    try:
        client = TestClient(app)
        r = client.delete(
            "/api/v1/admin/ip-blocklist/5.5.5.5",
            headers=_bearer(_admin_token()),
        )
        assert r.status_code == 404
    finally:
        _clear()


def test_manual_unblock_ip_success(monkeypatch):
    db = _fake_db({IpBlocklist: []})
    _override(db, _stub_admin())

    import app.modules.admin.audit_routes as routes
    monkeypatch.setattr(routes, "unblock_ip", lambda *a, **kw: True)
    log_calls = []
    monkeypatch.setattr(
        routes,
        "log_admin_action",
        lambda db, **kw: log_calls.append(kw),
    )

    try:
        client = TestClient(app)
        r = client.delete(
            "/api/v1/admin/ip-blocklist/5.5.5.5",
            headers=_bearer(_admin_token()),
        )
        assert r.status_code == 200, r.text
        assert "unblocked" in r.json()["message"]
        assert log_calls and log_calls[0]["action"] == "ip.unblock"
    finally:
        _clear()
