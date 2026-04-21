"""Tests for the admin Connector Master CRUD endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.models.connector import Connector
from app.db.models.user import UserRole
from app.db.session import get_db
from app.main import app
from app.modules.admin.connectors import service as connector_service


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


def _png(width: int = 1, height: int = 1) -> bytes:
    """Return real PNG bytes that Pillow's ``verify()`` accepts."""
    from io import BytesIO
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def _make_connector(
    name: str = "Google Reviews",
    is_active: bool = True,
) -> Connector:
    row = Connector()
    row.id = uuid4()
    row.name = name
    row.logo_url = "https://cdn.example.com/google.png"
    row.logo_sha256 = None
    row.is_active = is_active
    row.is_deleted = False
    row.deleted_at = None
    row.display_order = 0
    return row


# --------------------------- list ---------------------------


def test_list_connectors_returns_all(monkeypatch):
    rows = [
        _make_connector("Google"),
        _make_connector("Instagram", is_active=False),
    ]

    monkeypatch.setattr(
        connector_service,
        "list_connectors",
        lambda db, is_active=None, include_deleted=False, limit=50, offset=0: (
            rows,
            len(rows),
        ),
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/connectors", headers=_bearer(_admin_token())
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        names = [item["name"] for item in body["items"]]
        assert names == ["Google", "Instagram"]
    finally:
        _clear()


# --------------------------- create ---------------------------


def test_create_connector_emits_audit(monkeypatch):
    new_row = _make_connector("DoorDash")

    def fake_create(db, *, name, logo_url):
        assert name == "DoorDash"
        return new_row

    monkeypatch.setattr(connector_service, "create_connector", fake_create)

    calls: list[dict] = []
    import app.modules.admin.connectors.routes as routes_mod

    monkeypatch.setattr(
        routes_mod, "log_admin_action", lambda db, **kw: calls.append(kw)
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    admin = _stub_admin()
    _override_user(admin)
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/connectors",
            data={"name": "DoorDash"},
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "DoorDash"
        assert body["is_active"] is True

        assert calls, "expected log_admin_action to be invoked"
        kw = calls[0]
        assert kw["action"] == "connector_created"
        assert kw["target_entity"] == "connector"
        assert UUID(str(kw["target_id"])) == new_row.id
        assert kw["admin_id"] == admin.id
        assert kw["after_value"]["name"] == "DoorDash"
    finally:
        _clear()


def test_create_connector_rejects_duplicate_name(monkeypatch):
    def fake_create(db, *, name, logo_url):
        raise HTTPException(status_code=409, detail="A connector with this name already exists")

    monkeypatch.setattr(connector_service, "create_connector", fake_create)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/connectors",
            data={"name": "Google"},
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 409
    finally:
        _clear()


def test_create_connector_requires_name():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/connectors",
            data={},
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 422
    finally:
        _clear()


def test_create_connector_with_logo_in_one_call(monkeypatch, tmp_path):
    """Single multipart call creates the row AND uploads the logo."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "app_public_url", "https://api.example.com")
    monkeypatch.setattr(
        connector_service, "_assert_unique_logo_hash", lambda *a, **kw: None
    )

    new_row = _make_connector("Acme")
    new_row.logo_url = None

    def fake_create(db, *, name, logo_url):
        assert logo_url is None
        return new_row

    monkeypatch.setattr(connector_service, "create_connector", fake_create)

    calls: list[dict] = []
    import app.modules.admin.connectors.routes as routes_mod

    monkeypatch.setattr(
        routes_mod, "log_admin_action", lambda db, **kw: calls.append(kw)
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        png = _png()
        resp = client.post(
            "/api/v1/admin/connectors",
            data={"name": "Acme"},
            files={"file": ("logo.png", png, "image/png")},
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Acme"
        # Absolute URL using the configured public base.
        assert body["logo_url"].startswith("https://api.example.com/media/connectors/")
        # File written under media root.
        from pathlib import Path

        rel = body["logo_url"].split("/media/", 1)[1]
        assert (tmp_path / rel).is_file()
        # Filename uses the slugified connector name (no UUIDs in path).
        assert rel == "connectors/acme.png"

        assert calls and calls[0]["action"] == "connector_created"
        assert calls[0]["after_value"]["logo_url"] is not None
    finally:
        _clear()


# --------------------------- update ---------------------------


def test_update_connector_emits_audit_with_before_after(monkeypatch):
    existing = _make_connector("Google")

    def fake_get(db, connector_id):
        assert connector_id == existing.id
        return existing

    def fake_update(db, *, connector, name, logo_url, is_active, display_order=None):
        if name is not None:
            connector.name = name
        if logo_url is not None:
            connector.logo_url = logo_url
        if is_active is not None:
            connector.is_active = is_active
        return connector

    monkeypatch.setattr(connector_service, "get_connector_or_404", fake_get)
    monkeypatch.setattr(connector_service, "update_connector", fake_update)

    calls: list[dict] = []
    import app.modules.admin.connectors.routes as routes_mod

    monkeypatch.setattr(
        routes_mod, "log_admin_action", lambda db, **kw: calls.append(kw)
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.put(
            f"/api/v1/admin/connectors/{existing.id}",
            json={"name": "Google Reviews", "is_active": False},
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "Google Reviews"
        assert body["is_active"] is False

        assert calls
        kw = calls[0]
        assert kw["action"] == "connector_updated"
        assert kw["before_value"]["name"] == "Google"
        assert kw["before_value"]["is_active"] is True
        assert kw["after_value"]["name"] == "Google Reviews"
        assert kw["after_value"]["is_active"] is False
    finally:
        _clear()


# --------------------------- delete (soft) ---------------------------


def test_delete_connector_soft_deletes_and_audits(monkeypatch):
    existing = _make_connector("Yelp")

    def fake_get(db, connector_id):
        return existing

    def fake_soft_delete(db, *, connector):
        connector.is_deleted = True
        connector.is_active = False
        from datetime import datetime, timezone

        connector.deleted_at = datetime.now(timezone.utc)
        return connector

    monkeypatch.setattr(connector_service, "get_connector_or_404", fake_get)
    monkeypatch.setattr(connector_service, "soft_delete_connector", fake_soft_delete)

    calls: list[dict] = []
    import app.modules.admin.connectors.routes as routes_mod

    monkeypatch.setattr(
        routes_mod, "log_admin_action", lambda db, **kw: calls.append(kw)
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin(role="OPS_ADMIN"))
    try:
        client = TestClient(app)
        resp = client.delete(
            f"/api/v1/admin/connectors/{existing.id}",
            headers=_bearer(_admin_token(role="OPS_ADMIN")),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Delete forces is_active=false alongside is_deleted=true
        assert body["is_active"] is False
        assert body["is_deleted"] is True
        assert body["deleted_at"] is not None

        assert calls
        kw = calls[0]
        assert kw["action"] == "connector_deleted"
        assert kw["before_value"]["is_deleted"] is False
        assert kw["after_value"]["is_deleted"] is True
        assert kw["before_value"]["is_active"] is True
        assert kw["after_value"]["is_active"] is False
    finally:
        _clear()


# --------------------------- RBAC ---------------------------


def test_connector_routes_reject_non_admin_jwt():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/connectors",
            headers=_bearer(_admin_token(role="STAFF", is_admin=False)),
        )
        assert resp.status_code == 403
    finally:
        _clear()


def test_connector_routes_reject_missing_token():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/admin/connectors")
        assert resp.status_code == 401
    finally:
        _clear()


def test_connector_routes_reject_disallowed_admin_role():
    """Admins outside SUPER_ADMIN/OPS_ADMIN must be rejected by require_role."""
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin(role="FINANCE_ADMIN"))
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/connectors",
            json={"name": "X"},
            headers=_bearer(_admin_token(role="FINANCE_ADMIN")),
        )
        assert resp.status_code == 403
    finally:
        _clear()


# --------------------------- service-level invariants ---------------------------


def test_service_rejects_duplicate_name_case_insensitive():
    """Service.create_connector raises 409 when name (case-insensitive) exists."""
    existing = _make_connector("Google")

    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = existing
    db.query.return_value = q

    with pytest.raises(HTTPException) as exc:
        connector_service.create_connector(
            db, name="google", logo_url=None
        )
    assert exc.value.status_code == 409


def test_service_get_connector_or_404_raises_when_missing():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = None
    db.query.return_value = q

    with pytest.raises(HTTPException) as exc:
        connector_service.get_connector_or_404(db, uuid4())
    assert exc.value.status_code == 404


def test_service_soft_delete_sets_is_deleted_true():
    row = _make_connector("Yelp")
    db = MagicMock()
    # No attached property_connectors.
    q = MagicMock()
    q.filter.return_value = q
    q.scalar.return_value = 0
    db.query.return_value = q

    out = connector_service.soft_delete_connector(db, connector=row)
    assert out.is_deleted is True
    assert out.deleted_at is not None
    # Delete also forces inactive so the row is fully retired.
    assert out.is_active is False
    db.flush.assert_called_once()


def test_service_soft_delete_blocked_when_attached_to_properties():
    """Deleting a connector that has property_connectors rows must 409."""
    row = _make_connector("Yelp")
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.scalar.return_value = 3  # 3 properties currently attached
    db.query.return_value = q

    with pytest.raises(HTTPException) as exc:
        connector_service.soft_delete_connector(db, connector=row)
    assert exc.value.status_code == 409
    assert "3 properties" in exc.value.detail
    # State must be unchanged on rejection.
    assert row.is_deleted is False
    assert row.is_active is True
    assert row.deleted_at is None
    db.flush.assert_not_called()


def test_delete_endpoint_returns_409_when_attached(monkeypatch):
    """End-to-end: DELETE returns 409 with toast-friendly message."""
    existing = _make_connector("Yelp")
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    def boom(db, *, connector):
        raise HTTPException(
            status_code=409,
            detail="Cannot delete connector: it is attached to 2 properties. "
            "Detach it from all properties before deleting.",
        )

    monkeypatch.setattr(connector_service, "soft_delete_connector", boom)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.delete(
            f"/api/v1/admin/connectors/{existing.id}",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert "Cannot delete connector" in body["detail"]
        assert "Detach it from all properties" in body["detail"]
    finally:
        _clear()


# --------------------------- list filters + detail ---------------------------


def test_list_connectors_passes_filters_to_service(monkeypatch):
    seen: dict = {}

    def fake_list(db, *, is_active=None, include_deleted=False, limit=50, offset=0):
        seen["is_active"] = is_active
        seen["limit"] = limit
        seen["offset"] = offset
        return [], 0

    monkeypatch.setattr(connector_service, "list_connectors", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/connectors",
            params={"is_active": "true"},
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        assert seen["is_active"] is True
    finally:
        _clear()


def test_get_connector_detail_returns_row(monkeypatch):
    existing = _make_connector("Google")
    monkeypatch.setattr(
        connector_service,
        "get_connector_or_404",
        lambda db, connector_id: existing,
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/admin/connectors/{existing.id}",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == str(existing.id)
        assert body["name"] == "Google"
    finally:
        _clear()


def test_get_connector_detail_404_when_missing(monkeypatch):
    def fake_get(db, connector_id):
        from fastapi import HTTPException as _HE

        raise _HE(status_code=404, detail="Connector not found")

    monkeypatch.setattr(connector_service, "get_connector_or_404", fake_get)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/admin/connectors/{uuid4()}",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 404
    finally:
        _clear()


# --------------------------- pagination ---------------------------


def test_list_connectors_passes_pagination(monkeypatch):
    seen: dict = {}

    def fake_list(db, *, is_active=None, include_deleted=False, limit=50, offset=0):
        seen["limit"] = limit
        seen["offset"] = offset
        return [], 0

    monkeypatch.setattr(connector_service, "list_connectors", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/connectors?limit=10&offset=20",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["limit"] == 10
        assert body["offset"] == 20
        assert seen == {"limit": 10, "offset": 20}
    finally:
        _clear()


# --------------------------- restore ---------------------------


def test_restore_connector_emits_audit(monkeypatch):
    existing = _make_connector("Yelp")
    existing.is_deleted = True
    from datetime import datetime, timezone

    existing.deleted_at = datetime.now(timezone.utc)

    monkeypatch.setattr(
        connector_service,
        "get_connector_including_deleted_or_404",
        lambda db, cid: existing,
    )

    def fake_restore(db, *, connector):
        connector.is_deleted = False
        connector.deleted_at = None
        return connector

    monkeypatch.setattr(connector_service, "restore_connector", fake_restore)

    calls: list[dict] = []
    import app.modules.admin.connectors.routes as routes_mod

    monkeypatch.setattr(
        routes_mod, "log_admin_action", lambda db, **kw: calls.append(kw)
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/restore",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_deleted"] is False
        assert body["deleted_at"] is None

        assert calls
        kw = calls[0]
        assert kw["action"] == "connector_restored"
        assert kw["before_value"]["is_deleted"] is True
        assert kw["after_value"]["is_deleted"] is False
    finally:
        _clear()


# --------------------------- logo upload / delete ---------------------------


def test_upload_connector_logo_saves_and_audits(monkeypatch, tmp_path):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(
        connector_service, "_assert_unique_logo_hash", lambda *a, **kw: None
    )

    existing = _make_connector("Google")
    existing.logo_url = None

    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    calls: list[dict] = []
    import app.modules.admin.connectors.routes as routes_mod

    monkeypatch.setattr(
        routes_mod, "log_admin_action", lambda db, **kw: calls.append(kw)
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        # Real 1x1 PNG that passes Pillow's verify().
        png = _png()
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/logo",
            headers=_bearer(_admin_token()),
            files={"file": ("logo.png", png, "image/png")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["logo_url"]
        # Absolute URL so the front end can render it from anywhere.
        assert body["logo_url"].startswith("http://") or body["logo_url"].startswith("https://")
        assert "/media/connectors/" in body["logo_url"]
        # File must actually exist on disk under tmp_path
        from pathlib import Path

        rel = body["logo_url"].split("/media/", 1)[1]
        assert (tmp_path / rel).is_file()

        assert calls and calls[0]["action"] == "connector_logo_uploaded"
        assert "extra" not in calls[0] or calls[0].get("extra") is None
    finally:
        _clear()


def test_upload_connector_logo_rejects_unsupported_type(monkeypatch, tmp_path):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))

    existing = _make_connector("Google")
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/logo",
            headers=_bearer(_admin_token()),
            files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        assert resp.status_code == 415
    finally:
        _clear()


def test_upload_connector_logo_rejects_oversized(monkeypatch, tmp_path):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "connector_logo_max_bytes", 16)

    existing = _make_connector("Google")
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/logo",
            headers=_bearer(_admin_token()),
            files={"file": ("logo.png", b"x" * 64, "image/png")},
        )
        assert resp.status_code == 413
    finally:
        _clear()


def test_delete_connector_logo_clears_field(monkeypatch, tmp_path):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(
        connector_service, "_is_logo_referenced_by_others", lambda *a, **kw: False
    )

    # Pre-create a fake logo file under the new media root.
    (tmp_path / "connectors").mkdir(parents=True)
    cid = uuid4()
    fpath = tmp_path / "connectors" / f"{cid}_abc12345.png"
    fpath.write_bytes(b"\x89PNG fake")

    existing = _make_connector("Google")
    existing.id = cid
    existing.logo_url = f"/media/connectors/{fpath.name}"

    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, c: existing
    )

    calls: list[dict] = []
    import app.modules.admin.connectors.routes as routes_mod

    monkeypatch.setattr(
        routes_mod, "log_admin_action", lambda db, **kw: calls.append(kw)
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.delete(
            f"/api/v1/admin/connectors/{cid}/logo",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["logo_url"] is None
        assert not fpath.exists()  # file removed from disk

        assert calls and calls[0]["action"] == "connector_logo_cleared"
    finally:
        _clear()


# --------------------------- slug-based filenames ---------------------------


def test_slugify_helper_handles_unicode_and_specials():
    from app.modules.admin.connectors.service import _slugify

    assert _slugify("Google Reviews") == "google-reviews"
    assert _slugify("  TripAdvisor!! ") == "tripadvisor"
    assert _slugify("Caf\u00e9 D\u00e9lice") == "cafe-delice"
    assert _slugify("***") == "connector"
    assert _slugify("") == "connector"


def test_save_connector_logo_uses_slug_filename(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "media_url_prefix", "/media")
    monkeypatch.setattr(svc, "_assert_unique_logo_hash", lambda *a, **kw: None)

    connector = _make_connector("Google Reviews")
    connector.logo_url = None

    db = MagicMock()
    svc.save_connector_logo(
        db,
        connector=connector,
        file_bytes=_png(),
        filename="whatever.png",
        content_type="image/png",
    )

    assert connector.logo_url == "/media/connectors/google-reviews.png"
    assert (tmp_path / "connectors" / "google-reviews.png").is_file()


def test_update_connector_renames_logo_file_on_rename(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "media_url_prefix", "/media")

    # Seed an existing logo on disk for connector "Google".
    conn_dir = tmp_path / "connectors"
    conn_dir.mkdir(parents=True, exist_ok=True)
    (conn_dir / "google.png").write_bytes(b"data")

    connector = _make_connector("Google")
    connector.logo_url = "/media/connectors/google.png"

    monkeypatch.setattr(svc, "_assert_unique_name", lambda *a, **kw: None)

    db = MagicMock()
    svc.update_connector(
        db,
        connector=connector,
        name="Google Reviews",
        logo_url=None,
        is_active=None,
    )

    assert connector.name == "Google Reviews"
    assert connector.logo_url == "/media/connectors/google-reviews.png"
    assert (conn_dir / "google-reviews.png").is_file()
    assert not (conn_dir / "google.png").exists()


def test_update_connector_rename_skips_external_logo(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "media_url_prefix", "/media")

    connector = _make_connector("Yelp")
    connector.logo_url = "https://cdn.example.com/yelp.png"

    monkeypatch.setattr(svc, "_assert_unique_name", lambda *a, **kw: None)

    db = MagicMock()
    svc.update_connector(
        db,
        connector=connector,
        name="Yelp Reviews",
        logo_url=None,
        is_active=None,
    )

    # External URL is left untouched.
    assert connector.logo_url == "https://cdn.example.com/yelp.png"
    assert connector.name == "Yelp Reviews"


# --------------------------- image content validation ---------------------------


def test_upload_logo_rejects_bytes_not_actually_an_image(monkeypatch, tmp_path):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))

    existing = _make_connector("Google")
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        # Right MIME, wrong bytes.
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/logo",
            headers=_bearer(_admin_token()),
            files={"file": ("logo.png", b"not really a png" * 8, "image/png")},
        )
        assert resp.status_code == 415
    finally:
        _clear()


def test_upload_logo_rejects_format_mismatch(monkeypatch, tmp_path):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))

    existing = _make_connector("Google")
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        # PNG bytes uploaded as image/webp — must be rejected.
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/logo",
            headers=_bearer(_admin_token()),
            files={"file": ("logo.webp", _png(), "image/webp")},
        )
        assert resp.status_code == 415
    finally:
        _clear()


def test_upload_logo_rejects_oversized_dimensions(monkeypatch, tmp_path):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "connector_logo_max_pixels", 16)

    existing = _make_connector("Google")
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        big = _png(width=64, height=64)
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/logo",
            headers=_bearer(_admin_token()),
            files={"file": ("logo.png", big, "image/png")},
        )
        assert resp.status_code == 413
    finally:
        _clear()


def test_upload_logo_accepts_minimal_svg(monkeypatch, tmp_path):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "media_url_prefix", "/media")
    monkeypatch.setattr(svc, "_assert_unique_logo_hash", lambda *a, **kw: None)

    connector = _make_connector("Logo Co")
    connector.logo_url = None

    db = MagicMock()
    svc.save_connector_logo(
        db,
        connector=connector,
        file_bytes=b'<svg xmlns="http://www.w3.org/2000/svg"/>',
        filename="logo.svg",
        content_type="image/svg+xml",
    )
    assert connector.logo_url == "/media/connectors/logo-co.svg"


def test_upload_logo_rejects_html_disguised_as_svg(monkeypatch, tmp_path):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc
    from fastapi import HTTPException as _HE

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))

    connector = _make_connector("Bad")
    connector.logo_url = None

    db = MagicMock()
    with pytest.raises(_HE) as exc:
        svc.save_connector_logo(
            db,
            connector=connector,
            file_bytes=b"<html><body>nope</body></html>",
            filename="logo.svg",
            content_type="image/svg+xml",
        )
    assert exc.value.status_code == 415


# --------------------------- collision sweep ---------------------------


def test_uploading_new_extension_purges_old_logo_file(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "media_url_prefix", "/media")
    monkeypatch.setattr(svc, "_assert_unique_logo_hash", lambda *a, **kw: None)
    monkeypatch.setattr(svc, "_is_logo_referenced_by_others", lambda *a, **kw: False)

    conn_dir = tmp_path / "connectors"
    conn_dir.mkdir(parents=True, exist_ok=True)
    # Stale SVG from a previous upload (same slug, different extension).
    stale = conn_dir / "acme.svg"
    stale.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')

    connector = _make_connector("Acme")
    connector.logo_url = "/media/connectors/acme.svg"

    db = MagicMock()
    svc.save_connector_logo(
        db,
        connector=connector,
        file_bytes=_png(),
        filename="new.png",
        content_type="image/png",
    )

    assert connector.logo_url == "/media/connectors/acme.png"
    assert (conn_dir / "acme.png").is_file()
    assert not stale.exists(), "stale logo with different extension should be purged"


# --------------------------- duplicate logo image rejection ---------------------------


def test_save_connector_logo_rejects_duplicate_image_hash(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "media_url_prefix", "/media")

    import hashlib

    payload = _png()
    digest = hashlib.sha256(payload).hexdigest()

    other = _make_connector("Other")
    other.logo_sha256 = digest

    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = other
    db.query.return_value = q

    connector = _make_connector("New One")
    connector.logo_url = None
    connector.logo_sha256 = None

    with pytest.raises(HTTPException) as exc:
        svc.save_connector_logo(
            db,
            connector=connector,
            file_bytes=payload,
            filename="x.png",
            content_type="image/png",
        )
    assert exc.value.status_code == 409
    assert "already used" in exc.value.detail.lower()


def test_save_connector_logo_stores_hash_on_success(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    monkeypatch.setattr(app_settings, "media_url_prefix", "/media")

    connector = _make_connector("Brand New")
    connector.logo_url = None
    connector.logo_sha256 = None

    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = None  # no duplicate
    db.query.return_value = q

    payload = _png()
    svc.save_connector_logo(
        db,
        connector=connector,
        file_bytes=payload,
        filename="x.png",
        content_type="image/png",
    )

    import hashlib

    assert connector.logo_sha256 == hashlib.sha256(payload).hexdigest()


def test_clear_connector_logo_resets_hash(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))

    connector = _make_connector("Already")
    connector.logo_url = None
    connector.logo_sha256 = "abc"

    svc.clear_connector_logo(MagicMock(), connector=connector)
    assert connector.logo_sha256 is None
    assert connector.logo_url is None


# --------------------------- activate / deactivate ---------------------------


def _override_audit(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import app.modules.admin.connectors.routes as routes_mod

    monkeypatch.setattr(
        routes_mod, "log_admin_action", lambda db, **kw: calls.append(kw)
    )
    return calls


def test_deactivate_connector_emits_connector_deactivated_audit(monkeypatch):
    existing = _make_connector("Yelp", is_active=True)
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    def fake_update(db, *, connector, name, logo_url, is_active, display_order=None):
        assert name is None and logo_url is None
        connector.is_active = bool(is_active)
        return connector

    monkeypatch.setattr(connector_service, "update_connector", fake_update)
    calls = _override_audit(monkeypatch)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin(role="SUPER_ADMIN"))
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/deactivate",
            headers=_bearer(_admin_token(role="SUPER_ADMIN")),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is False
        assert calls and calls[0]["action"] == "connector_deactivated"
        assert calls[0]["before_value"]["is_active"] is True
        assert calls[0]["after_value"]["is_active"] is False
    finally:
        _clear()


def test_activate_connector_emits_connector_activated_audit(monkeypatch):
    existing = _make_connector("Yelp", is_active=False)
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    def fake_update(db, *, connector, name, logo_url, is_active, display_order=None):
        connector.is_active = bool(is_active)
        return connector

    monkeypatch.setattr(connector_service, "update_connector", fake_update)
    calls = _override_audit(monkeypatch)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin(role="SUPER_ADMIN"))
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/activate",
            headers=_bearer(_admin_token(role="SUPER_ADMIN")),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is True
        assert calls and calls[0]["action"] == "connector_activated"
    finally:
        _clear()


def test_activate_is_idempotent_when_already_active(monkeypatch):
    existing = _make_connector("Yelp", is_active=True)
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    def fake_update(*a, **kw):  # pragma: no cover - must NOT be called
        raise AssertionError("update_connector should not run for idempotent activate")

    monkeypatch.setattr(connector_service, "update_connector", fake_update)
    calls = _override_audit(monkeypatch)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin(role="SUPER_ADMIN"))
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/activate",
            headers=_bearer(_admin_token(role="SUPER_ADMIN")),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["is_active"] is True
        assert calls == [], "no audit row when state unchanged"
    finally:
        _clear()


def test_deactivate_allows_ops_admin(monkeypatch):
    """OPS_ADMIN is now allowed to deactivate (parity with restore/delete)."""
    existing = _make_connector("Yelp", is_active=True)
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    def fake_update(db, *, connector, name, logo_url, is_active, display_order=None):
        connector.is_active = bool(is_active)
        return connector

    monkeypatch.setattr(connector_service, "update_connector", fake_update)
    _override_audit(monkeypatch)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin(role="OPS_ADMIN"))
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/deactivate",
            headers=_bearer(_admin_token(role="OPS_ADMIN")),
        )
        assert resp.status_code == 200, resp.text
    finally:
        _clear()


def test_activate_allows_ops_admin(monkeypatch):
    """OPS_ADMIN is now allowed to activate."""
    existing = _make_connector("Yelp", is_active=False)
    monkeypatch.setattr(
        connector_service, "get_connector_or_404", lambda db, cid: existing
    )

    def fake_update(db, *, connector, name, logo_url, is_active, display_order=None):
        connector.is_active = bool(is_active)
        return connector

    monkeypatch.setattr(connector_service, "update_connector", fake_update)
    _override_audit(monkeypatch)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin(role="OPS_ADMIN"))
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/v1/admin/connectors/{existing.id}/activate",
            headers=_bearer(_admin_token(role="OPS_ADMIN")),
        )
        assert resp.status_code == 200, resp.text
    finally:
        _clear()


# --------------------------- tenant-facing list ---------------------------


def _tenant_token(tenant_id: str, user_id: str | None = None, role: str = "STAFF") -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id or str(uuid4()),
        "tenant_id": tenant_id,
        "role": role,
        "is_admin": False,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def test_tenant_connectors_returns_only_active(monkeypatch):
    captured: dict = {}

    def fake_list(db, *, is_active=None, include_deleted=False, limit=50, offset=0):
        captured["is_active"] = is_active
        captured["limit"] = limit
        return [_make_connector("Google"), _make_connector("Yelp")], 2

    monkeypatch.setattr(connector_service, "list_connectors", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/tenant/connectors",
            headers=_bearer(_tenant_token(tenant_id=str(uuid4()))),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert [i["name"] for i in body["items"]] == ["Google", "Yelp"]
        assert captured["is_active"] is True, "tenant route must hard-filter to active only"
    finally:
        _clear()


def test_tenant_connectors_with_property_id_enriches_binding_state(monkeypatch):
    """Property tab calls ``?property_id=`` to render Connect/Disconnect/Reactivate.

    * Connector A has an active binding   → ``is_connected=True``.
    * Connector B has an inactive binding → ``property_connector_id`` set,
      ``is_connected=False`` (UI renders "Reactivate").
    * Connector C has no binding          → both fields stay null/false
      (UI renders "Connect").
    """
    from app.db.models.property import Property
    from app.db.models.property_connector import PropertyConnector

    conn_a = _make_connector("Google")
    conn_b = _make_connector("Yelp")
    conn_c = _make_connector("Facebook")

    monkeypatch.setattr(
        connector_service,
        "list_connectors",
        lambda db, **kw: ([conn_a, conn_b, conn_c], 3),
    )

    property_id = uuid4()
    tenant_id = str(uuid4())

    pc_a = PropertyConnector()
    pc_a.id = uuid4()
    pc_a.property_id = property_id
    pc_a.connector_id = conn_a.id
    pc_a.is_active = True

    pc_b = PropertyConnector()
    pc_b.id = uuid4()
    pc_b.property_id = property_id
    pc_b.connector_id = conn_b.id
    pc_b.is_active = False

    owned = Property()
    owned.id = property_id

    class _Q:
        def __init__(self, db, model):
            self._db = db
            self._model = model

        def filter(self, *_a, **_kw):
            return self

        def first(self):
            return self._db._owned if self._model is Property else None

        def all(self):
            return self._db._bindings if self._model is PropertyConnector else []

    class _DB:
        _owned = owned
        _bindings = [pc_a, pc_b]

        def query(self, model, *_a, **_kw):
            return _Q(self, model)

    app.dependency_overrides[get_db] = lambda: _DB()
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/tenant/connectors?property_id={property_id}",
            headers=_bearer(_tenant_token(tenant_id=tenant_id)),
        )
        assert resp.status_code == 200, resp.text
        items = {i["name"]: i for i in resp.json()["items"]}

        assert items["Google"]["is_connected"] is True
        assert items["Google"]["property_connector_id"] == str(pc_a.id)

        assert items["Yelp"]["is_connected"] is False
        assert items["Yelp"]["property_connector_id"] == str(pc_b.id)

        assert items["Facebook"]["is_connected"] is False
        assert items["Facebook"]["property_connector_id"] is None
    finally:
        _clear()


def test_tenant_connectors_property_id_404_when_not_owned(monkeypatch):
    """Tenant cannot probe binding state for someone else's property."""
    from app.db.models.property import Property
    from app.db.models.property_connector import PropertyConnector

    monkeypatch.setattr(
        connector_service,
        "list_connectors",
        lambda db, **kw: ([_make_connector("Google")], 1),
    )

    class _Q:
        def filter(self, *_a, **_kw):
            return self

        def first(self):
            return None

        def all(self):
            return []

    class _DB:
        def query(self, *_a, **_kw):
            return _Q()

    app.dependency_overrides[get_db] = lambda: _DB()
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/tenant/connectors?property_id={uuid4()}",
            headers=_bearer(_tenant_token(tenant_id=str(uuid4()))),
        )
        assert resp.status_code == 404, resp.text
    finally:
        _clear()


def test_tenant_connectors_without_property_id_omits_binding_state(monkeypatch):
    """Backwards compatibility: unscoped catalog call returns plain items."""
    monkeypatch.setattr(
        connector_service,
        "list_connectors",
        lambda db, **kw: ([_make_connector("Google")], 1),
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/tenant/connectors",
            headers=_bearer(_tenant_token(tenant_id=str(uuid4()))),
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        # Schema defaults: never connected, no binding.
        assert item["is_connected"] is False
        assert item["property_connector_id"] is None
    finally:
        _clear()


def test_tenant_connectors_requires_tenant_context(monkeypatch):
    monkeypatch.setattr(
        connector_service,
        "list_connectors",
        lambda db, **kw: ([], 0),
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        # Admin token (no tenant_id, is_admin=True) without ?tenant_id= override -> 403.
        resp = client.get(
            "/api/v1/tenant/connectors",
            headers=_bearer(_admin_token(role="SUPER_ADMIN")),
        )
        assert resp.status_code == 403, resp.text
    finally:
        _clear()




# --------------------------- include_deleted, ordering, reorder ---------------------------


def test_admin_list_excludes_deleted_by_default(monkeypatch):
    captured: dict = {}

    def fake_list(db, *, is_active=None, include_deleted=False, limit=50, offset=0):
        captured["include_deleted"] = include_deleted
        return [], 0

    monkeypatch.setattr(connector_service, "list_connectors", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/connectors", headers=_bearer(_admin_token())
        )
        assert resp.status_code == 200, resp.text
        assert captured["include_deleted"] is False
    finally:
        _clear()


def test_admin_list_can_include_deleted(monkeypatch):
    captured: dict = {}

    def fake_list(db, *, is_active=None, include_deleted=False, limit=50, offset=0):
        captured["include_deleted"] = include_deleted
        return [], 0

    monkeypatch.setattr(connector_service, "list_connectors", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/connectors?include_deleted=true",
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        assert captured["include_deleted"] is True
    finally:
        _clear()


def test_reorder_connectors_endpoint(monkeypatch):
    a = _make_connector("Alpha")
    b = _make_connector("Beta")
    seen: dict = {}

    def fake_reorder(db, *, items):
        seen["items"] = list(items)
        a.display_order = items[0][1]
        b.display_order = items[1][1]
        return [a, b]

    monkeypatch.setattr(connector_service, "reorder_connectors", fake_reorder)

    calls: list[dict] = []
    import app.modules.admin.connectors.routes as routes_mod

    monkeypatch.setattr(
        routes_mod, "log_admin_action", lambda db, **kw: calls.append(kw)
    )

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/connectors/reorder",
            json={
                "items": [
                    {"id": str(a.id), "display_order": 5},
                    {"id": str(b.id), "display_order": 2},
                ]
            },
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert body["items"][0]["display_order"] == 5
        assert body["items"][1]["display_order"] == 2

        assert seen["items"] == [(a.id, 5), (b.id, 2)]
        assert calls and calls[0]["action"] == "connector_reordered"
    finally:
        _clear()


def test_reorder_returns_404_for_unknown_id(monkeypatch):
    def fake_reorder(db, *, items):
        raise HTTPException(status_code=404, detail="Connector(s) not found: x")

    monkeypatch.setattr(connector_service, "reorder_connectors", fake_reorder)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/connectors/reorder",
            json={"items": [{"id": str(uuid4()), "display_order": 0}]},
            headers=_bearer(_admin_token()),
        )
        assert resp.status_code == 404, resp.text
    finally:
        _clear()


# --------------------------- tenant schema does not leak soft-delete ---------------------------


def test_tenant_response_omits_soft_delete_fields(monkeypatch):
    monkeypatch.setattr(
        connector_service,
        "list_connectors",
        lambda db, *, is_active=None, include_deleted=False, limit=100, offset=0: (
            [_make_connector("Google")],
            1,
        ),
    )
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/tenant/connectors",
            headers=_bearer(_tenant_token(tenant_id=str(uuid4()))),
        )
        assert resp.status_code == 200, resp.text
        item = resp.json()["items"][0]
        # Tenant-side payload must NOT expose soft-delete bookkeeping.
        assert "is_deleted" not in item
        assert "deleted_at" not in item
        # Nor admin-only fields like is_active (tenant only sees active rows).
        assert "is_active" not in item
        # Display order IS exposed so the picker can render order.
        assert item["display_order"] == 0
    finally:
        _clear()


# --------------------------- storage abstraction + cleanup safety ---------------------------


def test_local_storage_save_and_delete(tmp_path):
    from app.core.storage import LocalFilesystemStorage

    s = LocalFilesystemStorage(root=str(tmp_path), url_prefix="/media")
    url = s.save(key="connectors/foo.png", data=b"abc")
    assert url == "/media/connectors/foo.png"
    assert s.exists("connectors/foo.png")
    assert s.key_from_url("/media/connectors/foo.png") == "connectors/foo.png"
    assert s.key_from_url("https://elsewhere.example/foo.png") is None
    s.delete("connectors/foo.png")
    assert not s.exists("connectors/foo.png")


def test_local_storage_rejects_traversal(tmp_path):
    from app.core.storage import LocalFilesystemStorage

    s = LocalFilesystemStorage(root=str(tmp_path), url_prefix="/media")
    # Should be a no-op (silently ignored), never escape the root.
    s.delete("../../etc/passwd")
    assert not s.exists("../../etc/passwd")


def test_local_storage_move(tmp_path):
    from app.core.storage import LocalFilesystemStorage

    s = LocalFilesystemStorage(root=str(tmp_path), url_prefix="/media")
    s.save(key="connectors/old.png", data=b"x")
    s.move(src_key="connectors/old.png", dst_key="connectors/new.png")
    assert not s.exists("connectors/old.png")
    assert s.exists("connectors/new.png")


def test_local_storage_list_prefix(tmp_path):
    from app.core.storage import LocalFilesystemStorage

    s = LocalFilesystemStorage(root=str(tmp_path), url_prefix="/media")
    s.save(key="connectors/acme.png", data=b"x")
    s.save(key="connectors/acme.svg", data=b"y")
    s.save(key="connectors/other.png", data=b"z")
    keys = sorted(s.list_prefix("connectors/acme."))
    assert keys == ["connectors/acme.png", "connectors/acme.svg"]


def test_s3_storage_url_helpers_round_trip():
    from app.core.storage import S3Storage

    s = S3Storage(bucket="b", region="us-east-1", key_prefix="env/prod")
    key = "connectors/google.png"
    url = s.url_for(key)
    assert url == "https://b.s3.us-east-1.amazonaws.com/env/prod/connectors/google.png"
    assert s.key_from_url(url) == key
    assert s.key_from_url("/media/foo") is None


def test_storage_backend_setting_selects_local(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.core.storage import LocalFilesystemStorage, get_storage, set_storage

    set_storage(None)
    monkeypatch.setattr(app_settings, "storage_backend", "local")
    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    assert isinstance(get_storage(), LocalFilesystemStorage)


def test_storage_backend_setting_selects_s3(monkeypatch):
    from app.core.config import settings as app_settings
    from app.core.storage import S3Storage, get_storage, set_storage

    set_storage(None)
    monkeypatch.setattr(app_settings, "storage_backend", "s3")
    monkeypatch.setattr(app_settings, "s3_bucket", "rl-bucket")
    monkeypatch.setattr(app_settings, "s3_region", "us-west-2")
    monkeypatch.setattr(app_settings, "s3_url_base", None)
    monkeypatch.setattr(app_settings, "s3_key_prefix", "")
    try:
        assert isinstance(get_storage(), S3Storage)
    finally:
        set_storage(None)


def test_clear_logo_skips_delete_when_other_row_references_same_file(tmp_path, monkeypatch):
    """A soft-deleted row may still reference the same logo URL/hash.
    The active connector clearing its logo must NOT remove the file in that case."""
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    (tmp_path / "connectors").mkdir(parents=True, exist_ok=True)
    fpath = tmp_path / "connectors" / "shared.png"
    fpath.write_bytes(b"\x89PNG fake")

    connector = _make_connector("Live")
    connector.logo_url = "/media/connectors/shared.png"
    connector.logo_sha256 = "deadbeef"

    # Pretend another row points at the same URL/hash.
    monkeypatch.setattr(
        svc, "_is_logo_referenced_by_others", lambda *a, **kw: True
    )

    db = MagicMock()
    svc.clear_connector_logo(db, connector=connector)

    assert connector.logo_url is None
    assert connector.logo_sha256 is None
    assert fpath.exists(), "shared file must NOT be removed when another row references it"


def test_clear_logo_removes_file_when_no_other_row_references_it(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))
    (tmp_path / "connectors").mkdir(parents=True, exist_ok=True)
    fpath = tmp_path / "connectors" / "solo.png"
    fpath.write_bytes(b"\x89PNG fake")

    connector = _make_connector("Solo")
    connector.logo_url = "/media/connectors/solo.png"
    connector.logo_sha256 = "deadbeef"

    monkeypatch.setattr(
        svc, "_is_logo_referenced_by_others", lambda *a, **kw: False
    )

    db = MagicMock()
    svc.clear_connector_logo(db, connector=connector)

    assert connector.logo_url is None
    assert not fpath.exists()


def test_clear_logo_ignores_external_url(tmp_path, monkeypatch):
    from app.core.config import settings as app_settings
    from app.modules.admin.connectors import service as svc

    monkeypatch.setattr(app_settings, "media_root", str(tmp_path))

    connector = _make_connector("Ext")
    connector.logo_url = "https://cdn.example.com/external.png"

    db = MagicMock()
    svc.clear_connector_logo(db, connector=connector)
    assert connector.logo_url is None  # DB cleared, no file ops attempted


# --------------------------- X-Total-Count header & dedicated logo verbs ---------------------------


def test_list_endpoint_sets_x_total_count_header(monkeypatch):
    rows = [_make_connector("A"), _make_connector("B")]

    def fake_list(db, *, is_active=None, include_deleted=False, limit=50, offset=0):
        return rows, 42  # pretend there are 42 in total

    monkeypatch.setattr(connector_service, "list_connectors", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    _override_user(_stub_admin())
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/connectors", headers=_bearer(_admin_token())
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("x-total-count") == "42"
        assert resp.json()["total"] == 42
    finally:
        _clear()


def test_tenant_list_endpoint_sets_x_total_count_header(monkeypatch):
    from app.core.dependencies import require_tenant_context

    rows = [_make_connector("A")]

    def fake_list(db, *, is_active=None, include_deleted=False, limit=50, offset=0):
        return rows, 7

    monkeypatch.setattr(connector_service, "list_connectors", fake_list)

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_tenant_context] = lambda: SimpleNamespace(
        tenant_id=uuid4(), user_id=uuid4()
    )
    try:
        client = TestClient(app)
        resp = client.get(
            "/api/v1/tenant/connectors", headers=_bearer(_admin_token(role="USER", is_admin=False))
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("x-total-count") == "7"
        assert resp.json()["total"] == 7
    finally:
        _clear()
