"""Tests for the audit/security tracking subsystems.

These tests intentionally use mocked SQLAlchemy Sessions so they remain fast
and database-agnostic. Database integration is exercised indirectly via the
existing alembic migration tests.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.db.models.admin_action_log import AdminActionLog
from app.db.models.audit_log import ActorType, AuditLog
from app.db.models.ip_blocklist import IpBlocklist
from app.db.models.login_attempt import LoginAttempt
from app.modules.audit import log_action, log_admin_action
from app.modules.security import (
    block_ip,
    ensure_ip_allowed,
    is_ip_blocked,
    record_login_attempt,
    register_failed_attempt_for_ip,
    unblock_ip,
)
from app.modules.security import ip_blocking as ip_blocking_module
from app.modules.security import login_tracking as login_tracking_module


# --------------------------- helpers ---------------------------


def _capture_session():
    """Return a MagicMock Session that records ``add`` calls in ``.added``."""
    db = MagicMock()
    db.added = []
    db.add.side_effect = lambda obj: db.added.append(obj)
    db.flush.return_value = None
    return db


# --------------------------- audit.log_action ---------------------------


def test_log_action_inserts_audit_log_row():
    db = _capture_session()
    actor_id = uuid4()
    entity_id = uuid4()

    entry = log_action(
        db,
        actor_id=actor_id,
        actor_type="tenant",
        action="property.create",
        entity="property",
        entity_id=entity_id,
        before_value=None,
        after_value={"name": "Test Property"},
        ip_address="10.0.0.1",
    )

    assert isinstance(entry, AuditLog)
    assert len(db.added) == 1
    added = db.added[0]
    assert added.action == "property.create"
    assert added.entity == "property"
    assert added.actor_id == actor_id
    assert added.entity_id == entity_id
    # tenant/admin/user free-form values map to ActorType.user
    assert added.actor_type == ActorType.user
    assert added.after_value == {"name": "Test Property"}
    assert added.ip_address == "10.0.0.1"
    db.flush.assert_called_once()


def test_log_action_system_actor_when_actor_id_missing():
    db = _capture_session()
    log_action(
        db,
        actor_id=None,
        actor_type="system",
        action="cron.cleanup",
        entity="job",
    )
    assert db.added[0].actor_type == ActorType.system
    assert db.added[0].actor_id is None


def test_log_action_uses_request_state_ip_when_not_provided():
    db = _capture_session()
    request = SimpleNamespace(state=SimpleNamespace(ip_address="1.2.3.4"))

    log_action(
        db,
        actor_id=uuid4(),
        actor_type="user",
        action="connector.activate",
        entity="connector",
        request=request,
    )
    assert db.added[0].ip_address == "1.2.3.4"


def test_log_action_serialises_complex_values_to_json_safe():
    db = _capture_session()
    log_action(
        db,
        actor_id=uuid4(),
        actor_type="user",
        action="ai.draft.approve",
        entity="ai_draft",
        before_value={"status": "pending", "id": uuid4()},
        after_value={"status": "approved"},
    )
    before = db.added[0].before_value
    assert before["status"] == "pending"
    # UUIDs must be coerced to strings to remain JSON-safe.
    assert isinstance(before["id"], str)


# --------------------------- audit.log_admin_action ---------------------------


def test_log_admin_action_writes_two_rows_and_mirrors():
    db = _capture_session()
    admin_id = uuid4()
    tenant_id = uuid4()

    log_admin_action(
        db,
        admin_id=admin_id,
        action="tenant.suspend",
        target_entity="tenant",
        target_id=tenant_id,
        target_tenant_id=tenant_id,
        before_value={"is_active": True},
        after_value={"is_active": False},
        ip_address="9.9.9.9",
        user_agent="curl/8",
        request_path="/api/v1/admin/tenants/suspend",
        extra={"reason": "billing"},
    )

    # One AdminActionLog + one mirrored AuditLog.
    kinds = [type(o).__name__ for o in db.added]
    assert "AdminActionLog" in kinds
    assert "AuditLog" in kinds
    admin_row = next(o for o in db.added if isinstance(o, AdminActionLog))
    assert admin_row.action == "tenant.suspend"
    assert admin_row.admin_id == admin_id
    assert admin_row.target_tenant_id == tenant_id
    assert admin_row.before_value == {"is_active": True}
    assert admin_row.after_value == {"is_active": False}
    assert admin_row.ip_address == "9.9.9.9"
    assert admin_row.user_agent == "curl/8"
    assert admin_row.request_path == "/api/v1/admin/tenants/suspend"
    assert admin_row.extra == {"reason": "billing"}

    audit_row = next(o for o in db.added if isinstance(o, AuditLog))
    assert audit_row.action == "admin.tenant.suspend"
    assert audit_row.actor_id == admin_id


def test_log_admin_action_requires_admin_id():
    db = _capture_session()
    with pytest.raises(ValueError):
        log_admin_action(db, admin_id=None, action="impersonate")


def test_log_admin_action_pulls_request_context():
    db = _capture_session()
    request = SimpleNamespace(
        state=SimpleNamespace(
            ip_address="5.5.5.5",
            user_agent="Mozilla/5.0",
            request_path="/api/v1/admin/billing",
        )
    )
    log_admin_action(
        db,
        admin_id=uuid4(),
        action="billing.update",
        request=request,
    )
    admin_row = next(o for o in db.added if isinstance(o, AdminActionLog))
    assert admin_row.ip_address == "5.5.5.5"
    assert admin_row.user_agent == "Mozilla/5.0"
    assert admin_row.request_path == "/api/v1/admin/billing"


# --------------------------- security.login_tracking ---------------------------


def test_record_login_attempt_persists_failure():
    db = _capture_session()
    record_login_attempt(
        db,
        email="USER@example.com",
        ip_address="2.2.2.2",
        success=False,
        reason="invalid_credentials",
        user_agent="UA",
    )
    row = db.added[0]
    assert isinstance(row, LoginAttempt)
    assert row.email == "user@example.com"  # lower-cased
    assert row.success is False
    assert row.reason == "invalid_credentials"
    assert row.ip_address == "2.2.2.2"
    assert row.user_agent == "UA"


def test_record_login_attempt_persists_success():
    db = _capture_session()
    record_login_attempt(
        db, email="ok@example.com", ip_address="3.3.3.3", success=True
    )
    assert db.added[0].success is True
    assert db.added[0].reason is None


# --------------------------- security.ip_blocking ---------------------------


def _query_returning(value):
    """Build a chained query mock that ``.first()`` returns ``value``."""
    chain = MagicMock()
    chain.filter.return_value = chain
    chain.first.return_value = value
    return chain


def test_is_ip_blocked_returns_false_when_no_row():
    db = _capture_session()
    db.query.return_value = _query_returning(None)
    assert is_ip_blocked(db, "1.1.1.1") is False


def test_is_ip_blocked_returns_true_for_active_block():
    db = _capture_session()
    row = IpBlocklist(
        ip_address="1.1.1.1",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db.query.return_value = _query_returning(row)
    assert is_ip_blocked(db, "1.1.1.1") is True


def test_is_ip_blocked_cleans_up_expired_row():
    db = _capture_session()
    row = IpBlocklist(
        ip_address="1.1.1.1",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.query.return_value = _query_returning(row)
    assert is_ip_blocked(db, "1.1.1.1") is False
    db.delete.assert_called_once_with(row)


def test_ensure_ip_allowed_raises_403_when_blocked(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(ip_blocking_module, "is_ip_blocked", lambda _db, _ip: True)
    with pytest.raises(HTTPException) as exc:
        ensure_ip_allowed(MagicMock(), "1.2.3.4")
    assert exc.value.status_code == 403


def test_block_and_unblock_ip_inserts_and_deletes():
    db = _capture_session()
    db.query.return_value = _query_returning(None)
    row = block_ip(db, "8.8.8.8", reason="threshold", failed_attempts=25)
    assert isinstance(row, IpBlocklist)
    assert row.ip_address == "8.8.8.8"
    assert row.reason == "threshold"
    assert row.expires_at is not None

    db.query.return_value = _query_returning(row)
    assert unblock_ip(db, "8.8.8.8") is True
    db.delete.assert_called_once_with(row)


def test_register_failed_attempt_for_ip_blocks_over_threshold(monkeypatch):
    db = _capture_session()
    db.query.return_value = _query_returning(None)
    monkeypatch.setattr(
        ip_blocking_module,
        "recent_failures_for_ip",
        lambda _db, _ip, window_minutes=15: 50,
    )
    blocked = register_failed_attempt_for_ip(db, "9.9.9.9", threshold=20)
    assert blocked is True
    assert any(isinstance(o, IpBlocklist) for o in db.added)


def test_register_failed_attempt_for_ip_noop_under_threshold(monkeypatch):
    db = _capture_session()
    monkeypatch.setattr(
        ip_blocking_module,
        "recent_failures_for_ip",
        lambda _db, _ip, window_minutes=15: 1,
    )
    assert register_failed_attempt_for_ip(db, "9.9.9.9", threshold=20) is False
    assert db.added == []


# --------------------------- auth integration: failed login tracked ---------


def test_failed_login_writes_login_attempt_and_audit(monkeypatch):
    """Auth service must persist BOTH a LoginAttempt and an AuditLog on failure."""
    from app.modules.auth.service import AuthService

    db = _capture_session()
    redis_client = MagicMock()
    svc = AuthService(db=db, redis_client=redis_client)

    # Avoid touching the IP-blocking failure query — keep this purely about
    # the tracking writes.
    monkeypatch.setattr(
        ip_blocking_module,
        "recent_failures_for_ip",
        lambda _db, _ip, window_minutes=15: 0,
    )

    svc._record_audit_login_attempt(
        email="bad@example.com",
        success=False,
        ip_address="6.6.6.6",
        user=None,
        reason="invalid_credentials",
    )

    kinds = [type(o).__name__ for o in db.added]
    assert "LoginAttempt" in kinds
    assert "AuditLog" in kinds

    audit = next(o for o in db.added if isinstance(o, AuditLog))
    assert audit.action == "login_failure"
    assert audit.entity == "auth"
    assert audit.actor_type == ActorType.system  # no user

    attempt = next(o for o in db.added if isinstance(o, LoginAttempt))
    assert attempt.success is False
    assert attempt.email == "bad@example.com"
    assert attempt.reason == "invalid_credentials"


def test_successful_login_writes_login_attempt_and_audit():
    from app.modules.auth.service import AuthService

    db = _capture_session()
    svc = AuthService(db=db, redis_client=MagicMock())

    user = SimpleNamespace(id=uuid4(), email="ok@example.com")
    svc._record_audit_login_attempt(
        email=user.email,
        success=True,
        ip_address="7.7.7.7",
        user=user,
    )
    audit = next(o for o in db.added if isinstance(o, AuditLog))
    assert audit.action == "login_success"
    assert audit.actor_id == user.id


# --------------------------- middleware: request.state context ---------


def test_request_context_middleware_sets_security_state():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.core.middleware import RequestContextMiddleware

    captured: dict = {}

    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/probe")
    async def probe(request: Request):
        captured["ip_address"] = request.state.ip_address
        captured["user_agent"] = request.state.user_agent
        captured["request_path"] = request.state.request_path
        captured["request_id"] = request.state.request_id
        return {"ok": True}

    client = TestClient(app)
    response = client.get(
        "/probe",
        headers={"User-Agent": "pytest-agent", "X-Forwarded-For": "203.0.113.5"},
    )
    assert response.status_code == 200, response.text
    assert captured["request_path"] == "/probe"
    assert captured["user_agent"] == "pytest-agent"
    assert captured["ip_address"] == "203.0.113.5"
    # request_id is a uuid string.
    UUID(captured["request_id"])
