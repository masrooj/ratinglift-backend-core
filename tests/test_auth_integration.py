from fastapi.testclient import TestClient

from app.main import app
from app.modules.auth.routes import get_auth_service
from app.modules.auth.service import get_current_user


class DummyAuthService:
    def signup(self, **kwargs):
        return {
            "access_token": "token-signup",
            "token_type": "bearer",
            "user": {
                "id": "user-1",
                "email": kwargs["email"],
                "full_name": kwargs.get("full_name"),
                "profile_picture_url": None,
                "role": kwargs["role"],
                "tenant_id": "tenant-1",
                "is_admin": False,
                "mfa_enabled": False,
            },
            "role": kwargs["role"],
            "tenant_id": "tenant-1",
            "mfa_required": False,
            "message": None,
        }

    def login_with_password(self, **kwargs):
        if kwargs["email"].startswith("mfa-"):
            return {
                "access_token": None,
                "token_type": "bearer",
                "user": None,
                "role": None,
                "tenant_id": None,
                "mfa_required": True,
                "message": "OTP generated. Please verify MFA at /api/v1/auth/mfa/verify.",
            }
        return {
            "access_token": "token-login",
            "token_type": "bearer",
            "user": {
                "id": "user-1",
                "email": kwargs["email"],
                "full_name": "User",
                "profile_picture_url": None,
                "role": "STAFF" if not kwargs["admin_only"] else "SUPER_ADMIN",
                "tenant_id": None if kwargs["admin_only"] else "tenant-1",
                "is_admin": kwargs["admin_only"],
                "mfa_enabled": False,
            },
            "role": "STAFF" if not kwargs["admin_only"] else "SUPER_ADMIN",
            "tenant_id": None if kwargs["admin_only"] else "tenant-1",
            "mfa_required": False,
            "message": None,
        }

    def verify_mfa_login(self, **kwargs):
        return {
            "access_token": "token-mfa",
            "token_type": "bearer",
            "user": {
                "id": "user-1",
                "email": kwargs["email"],
                "full_name": "User",
                "profile_picture_url": None,
                "role": "STAFF",
                "tenant_id": "tenant-1",
                "is_admin": kwargs["admin_only"],
                "mfa_enabled": True,
            },
            "role": "STAFF",
            "tenant_id": "tenant-1",
            "mfa_required": False,
            "message": None,
        }

    async def login_with_social_provider(self, **kwargs):
        return {
            "access_token": "token-social",
            "token_type": "bearer",
            "user": {
                "id": "user-social-1",
                "email": "social@example.com",
                "full_name": "Social User",
                "profile_picture_url": "https://example.com/avatar.png",
                "role": "STAFF",
                "tenant_id": "tenant-1",
                "is_admin": False,
                "mfa_enabled": False,
            },
            "role": "STAFF",
            "tenant_id": "tenant-1",
            "mfa_required": False,
            "message": None,
        }

    def create_admin(self, **kwargs):
        return {
            "access_token": None,
            "token_type": "bearer",
            "user": {
                "id": "admin-new-1",
                "email": kwargs["email"],
                "full_name": kwargs.get("full_name"),
                "profile_picture_url": None,
                "role": kwargs["role"],
                "tenant_id": None,
                "is_admin": True,
                "mfa_enabled": False,
            },
            "role": kwargs["role"],
            "tenant_id": None,
            "mfa_required": False,
            "message": "Admin created successfully.",
        }


def _override_auth_service():
    return DummyAuthService()


def _client() -> TestClient:
    app.dependency_overrides[get_auth_service] = _override_auth_service
    return TestClient(app)


def teardown_module(_module):
    app.dependency_overrides.clear()


def test_signup_endpoint_integration():
    client = _client()
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "newuser@example.com",
            "password": "StrongPass123",
            "full_name": "New User",
            "tenant_name": "Acme",
            "role": "OWNER",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "token-signup"
    assert body["user"]["email"] == "newuser@example.com"
    assert body["role"] == "OWNER"
    assert body["tenant_id"] == "tenant-1"


def test_login_endpoint_integration():
    client = _client()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "StrongPass123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "token-login"
    assert body["role"] == "STAFF"
    assert body["tenant_id"] == "tenant-1"


def test_admin_login_endpoint_integration():
    client = _client()
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "admin@example.com", "password": "StrongPass123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "token-login"
    assert body["role"] == "SUPER_ADMIN"
    assert body["user"]["is_admin"] is True


def test_mfa_verify_endpoint_integration():
    client = _client()
    response = client.post(
        "/api/v1/auth/mfa/verify",
        json={"email": "user@example.com", "otp": "123456"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "token-mfa"
    assert body["user"]["mfa_enabled"] is True


def test_social_login_endpoints_integration():
    client = _client()

    for path in [
        "/api/v1/auth/social/google",
        "/api/v1/auth/social/microsoft",
        "/api/v1/auth/social/facebook",
    ]:
        response = client.post(path, json={"oauth_token": "fake-oauth-token-12345"})
        assert response.status_code == 200
        body = response.json()
        assert body["access_token"] == "token-social"
        assert body["user"]["email"] == "social@example.com"
        assert body["tenant_id"] == "tenant-1"


def test_login_request_body_rejects_legacy_fields_silently():
    """otp/device_info/location are no longer in LoginRequest; extras are ignored."""
    client = _client()
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "user@example.com",
            "password": "StrongPass123",
            "otp": "123456",
            "device_info": "iPhone",
            "location": "KHI",
        },
    )
    # Pydantic default is to ignore unknown fields => login still succeeds.
    assert response.status_code == 200


def test_login_returns_mfa_challenge_without_token():
    client = _client()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "mfa-user@example.com", "password": "StrongPass123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mfa_required"] is True
    assert body["access_token"] is None
    assert "mfa/verify" in (body["message"] or "")


class _StubUser:
    def __init__(self, role: str, is_admin: bool = True):
        from app.db.models.user import UserRole
        self.id = "actor-1"
        self.role = UserRole(role)
        self.is_admin = is_admin
        self.tenant_id = None
        self.email = "actor@example.com"


def test_create_admin_requires_super_admin_role():
    app.dependency_overrides[get_auth_service] = _override_auth_service
    app.dependency_overrides[get_current_user] = lambda: _StubUser("SUPPORT_ADMIN")
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/admin/auth/create-admin",
            json={
                "email": "new-admin@example.com",
                "password": "SuperSecret123!",
                "full_name": "New Admin",
                "role": "FINANCE_ADMIN",
            },
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_create_admin_succeeds_for_super_admin():
    app.dependency_overrides[get_auth_service] = _override_auth_service
    app.dependency_overrides[get_current_user] = lambda: _StubUser("SUPER_ADMIN")
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/admin/auth/create-admin",
            json={
                "email": "new-admin@example.com",
                "password": "SuperSecret123!",
                "full_name": "New Admin",
                "role": "FINANCE_ADMIN",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["user"]["email"] == "new-admin@example.com"
        assert body["user"]["is_admin"] is True
        assert body["role"] == "FINANCE_ADMIN"
        assert body["message"] == "Admin created successfully."
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_create_admin_rejects_invalid_role_at_schema_level():
    app.dependency_overrides[get_auth_service] = _override_auth_service
    app.dependency_overrides[get_current_user] = lambda: _StubUser("SUPER_ADMIN")
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/admin/auth/create-admin",
            json={
                "email": "bad@example.com",
                "password": "SuperSecret123!",
                "role": "OWNER",  # tenant role, not an admin role
            },
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_bootstrap_admin_seeder_is_noop_without_env(monkeypatch):
    from app.modules.auth import bootstrap as bootstrap_module
    from app.db.models.user import UserRole

    # Entry with empty credentials => skipped (no DB access).
    empty_entry = bootstrap_module.AdminSeed(
        email="", password="", full_name="", role=UserRole.SUPER_ADMIN.value
    )
    monkeypatch.setattr(bootstrap_module, "ADMINS", [empty_entry])

    class _FakeDb:
        def query(self, *_a, **_k):
            raise AssertionError("DB should not be touched when credentials are empty")

    assert bootstrap_module.seed_admins(_FakeDb()) == []  # type: ignore[arg-type]


def test_bootstrap_admin_seeder_skips_non_admin_role(monkeypatch):
    from app.modules.auth import bootstrap as bootstrap_module

    bad_entry = bootstrap_module.AdminSeed(
        email="bad@example.com",
        password="ValidPass123!",
        full_name="Bad",
        role="OWNER",  # tenant role, not admin
    )
    monkeypatch.setattr(bootstrap_module, "ADMINS", [bad_entry])

    class _FakeDb:
        def query(self, *_a, **_k):
            raise AssertionError("DB should not be touched for invalid role")

    assert bootstrap_module.seed_admins(_FakeDb()) == []  # type: ignore[arg-type]


# ---------------- MFA setup flow ----------------


class _MfaStubUser:
    def __init__(self):
        from app.db.models.user import UserRole
        self.id = "user-mfa-1"
        self.role = UserRole.STAFF
        self.is_admin = False
        self.tenant_id = "tenant-1"
        self.email = "user@example.com"
        self.mfa_enabled = False
        self.mfa_email = None
        self.mfa_email_verified = False
        self.mfa_phone = None
        self.mfa_phone_verified = False


class _MfaDummyService:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def get_mfa_status(self, user):
        return {
            "mfa_enabled": user.mfa_enabled,
            "email": user.mfa_email,
            "email_verified": user.mfa_email_verified,
            "phone": user.mfa_phone,
            "phone_verified": user.mfa_phone_verified,
        }

    def add_mfa_channel(self, user, channel, destination):
        self.calls.append(("add", (channel, destination), {}))
        if channel == "email":
            user.mfa_email = destination
            user.mfa_email_verified = False
        else:
            user.mfa_phone = destination
            user.mfa_phone_verified = False
        return f"channel:{channel}"

    def verify_mfa_channel(self, user, channel, otp):
        self.calls.append(("verify", (channel, otp), {}))
        if otp != "123456":
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Invalid or expired OTP")
        if channel == "email":
            user.mfa_email_verified = True
        else:
            user.mfa_phone_verified = True

    def enable_mfa(self, user):
        if not (user.mfa_email_verified or user.mfa_phone_verified):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Verify at least one channel ...")
        user.mfa_enabled = True

    def disable_mfa(self, user):
        user.mfa_enabled = False


def _mfa_client(user):
    dummy = _MfaDummyService()
    app.dependency_overrides[get_auth_service] = lambda: dummy
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), dummy


def test_mfa_status_endpoint():
    user = _MfaStubUser()
    try:
        client, _ = _mfa_client(user)
        r = client.get("/api/v1/auth/mfa/status")
        assert r.status_code == 200
        body = r.json()
        assert body["mfa_enabled"] is False
        assert body["email_verified"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mfa_add_and_verify_email_channel():
    user = _MfaStubUser()
    try:
        client, dummy = _mfa_client(user)

        r = client.post(
            "/api/v1/auth/mfa/channel",
            json={"channel": "email", "destination": "mfa@example.com"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["channel"] == "email"
        assert body["verified"] is False

        r = client.post(
            "/api/v1/auth/mfa/channel/verify",
            json={"channel": "email", "otp": "123456"},
        )
        assert r.status_code == 200
        assert r.json()["verified"] is True
        assert user.mfa_email_verified is True
        assert dummy.calls[0][0] == "add"
        assert dummy.calls[1][0] == "verify"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mfa_verify_wrong_otp_returns_401():
    user = _MfaStubUser()
    user.mfa_email = "mfa@example.com"
    try:
        client, _ = _mfa_client(user)
        r = client.post(
            "/api/v1/auth/mfa/channel/verify",
            json={"channel": "email", "otp": "999999"},
        )
        assert r.status_code == 401
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mfa_enable_requires_verified_channel():
    user = _MfaStubUser()
    try:
        client, _ = _mfa_client(user)
        r = client.post("/api/v1/auth/mfa/enable", json={})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_mfa_enable_then_disable_flow():
    user = _MfaStubUser()
    user.mfa_email = "mfa@example.com"
    user.mfa_email_verified = True
    try:
        client, _ = _mfa_client(user)

        r = client.post("/api/v1/auth/mfa/enable", json={})
        assert r.status_code == 200
        assert r.json()["mfa_enabled"] is True

        r = client.post("/api/v1/auth/mfa/disable")
        assert r.status_code == 200
        assert r.json()["mfa_enabled"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)

