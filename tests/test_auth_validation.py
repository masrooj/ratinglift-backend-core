"""Tests for the shared validators and their integration via schemas."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.modules.auth.routes import get_auth_service
from app.modules.auth.schemas import (
    CreateAdminRequest,
    LoginRequest,
    MfaChannelAddRequest,
    MfaChannelType,
    PasswordResetRequest,
    SignupRequest,
    TotpVerifyRequest,
)
from app.modules.auth.service import get_current_user
from app.modules.auth.validators import (
    normalize_email,
    normalize_tenant_name,
    validate_password_strength,
    validate_phone_e164,
    validate_six_digit_code,
)


# ---------------- unit: validators module ----------------


class TestPasswordStrength:
    def test_accepts_strong_password(self):
        assert validate_password_strength("StrongPass123") == "StrongPass123"

    @pytest.mark.parametrize(
        "bad",
        [
            "short1A",           # too short
            "alllowercase1",     # no upper
            "ALLUPPERCASE1",     # no lower
            "NoDigitsHere",      # no digit
            "Has Space1",        # contains space
            "x" * 129,           # too long
        ],
    )
    def test_rejects_weak(self, bad):
        with pytest.raises(ValueError):
            validate_password_strength(bad)


class TestEmailNormalization:
    def test_lowercases_and_strips(self):
        assert normalize_email("  Foo@Example.COM ") == "foo@example.com"

    def test_rejects_plus_alias(self):
        with pytest.raises(ValueError):
            normalize_email("user+spam@example.com")

    def test_rejects_disposable(self):
        with pytest.raises(ValueError):
            normalize_email("someone@mailinator.com")

    def test_rejects_missing_at(self):
        with pytest.raises(ValueError):
            normalize_email("not-an-email")


class TestPhoneE164:
    @pytest.mark.parametrize("good", ["+14155551234", "+923001234567", "+442071838750"])
    def test_accepts_valid(self, good):
        assert validate_phone_e164(good) == good

    @pytest.mark.parametrize(
        "bad",
        [
            "14155551234",        # missing +
            "+0123456789",        # leading 0
            "+123",               # too short
            "+1 (415) abc-1234",  # non-digits
            "",
        ],
    )
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_phone_e164(bad)

    def test_strips_spaces_and_hyphens(self):
        assert validate_phone_e164("+1 415-555-1234") == "+14155551234"


class TestTenantSlug:
    def test_slugifies(self):
        assert normalize_tenant_name("  Acme Inc. ") == "acme-inc"  # dots stripped? No — kept

    def test_none_passthrough(self):
        assert normalize_tenant_name(None) is None

    def test_rejects_bad_slug(self):
        with pytest.raises(ValueError):
            normalize_tenant_name("???")


class TestSixDigitCode:
    def test_accepts_six_digits(self):
        assert validate_six_digit_code("123456") == "123456"

    def test_strips_spaces(self):
        assert validate_six_digit_code(" 123 456 ") == "123456"

    @pytest.mark.parametrize("bad", ["abcdef", "12345", "1234567", ""])
    def test_rejects_non_six_digit(self, bad):
        with pytest.raises(ValueError):
            validate_six_digit_code(bad)


# ---------------- unit: schema integration ----------------


class TestSignupSchema:
    def test_good_payload(self):
        req = SignupRequest(
            email="Owner@Example.com",
            password="StrongPass123",
            full_name="  Alice  ",
            tenant_name="Acme Corp",
            role="OWNER",
        )
        assert req.email == "owner@example.com"
        assert req.full_name == "Alice"
        assert req.tenant_name == "acme-corp"

    def test_rejects_weak_password(self):
        with pytest.raises(ValidationError):
            SignupRequest(
                email="owner@example.com",
                password="weakpass",
                role="OWNER",
            )

    def test_rejects_disposable_email(self):
        with pytest.raises(ValidationError):
            SignupRequest(
                email="owner@mailinator.com",
                password="StrongPass123",
                role="OWNER",
            )

    def test_rejects_plus_alias(self):
        with pytest.raises(ValidationError):
            SignupRequest(
                email="owner+spam@example.com",
                password="StrongPass123",
                role="OWNER",
            )


class TestCreateAdminSchema:
    def test_requires_12_chars(self):
        with pytest.raises(ValidationError):
            CreateAdminRequest(
                email="admin@example.com",
                password="Short12A",  # 8 chars
                role="FINANCE_ADMIN",
            )

    def test_accepts_strong_12_plus(self):
        req = CreateAdminRequest(
            email="admin@example.com",
            password="SuperSecret123!",
            role="FINANCE_ADMIN",
        )
        assert req.password == "SuperSecret123!"


class TestPasswordResetSchema:
    def test_rejects_weak_new_password(self):
        with pytest.raises(ValidationError):
            PasswordResetRequest(
                token="x" * 20,
                new_password="weakpass",
            )


class TestMfaChannelSchema:
    def test_phone_requires_e164(self):
        with pytest.raises(ValidationError):
            MfaChannelAddRequest(channel=MfaChannelType.PHONE, destination="4155551234")

    def test_phone_accepts_e164(self):
        req = MfaChannelAddRequest(channel=MfaChannelType.PHONE, destination="+1 415-555-1234")
        assert req.destination == "+14155551234"

    def test_email_normalized(self):
        req = MfaChannelAddRequest(channel=MfaChannelType.EMAIL, destination="MFA@Example.com")
        assert req.destination == "mfa@example.com"


class TestTotpVerifySchema:
    def test_requires_six_digits(self):
        with pytest.raises(ValidationError):
            TotpVerifyRequest(code="abcdef")

    def test_accepts_valid(self):
        assert TotpVerifyRequest(code="123456").code == "123456"


# ---------------- integration: HTTP surface ----------------


class _NullAuthService:
    """Minimal stub — validation tests don't exercise service logic."""

    def signup(self, **kwargs):
        return {"access_token": "t", "user": None, "role": kwargs["role"], "tenant_id": None}


def teardown_function(_fn):
    app.dependency_overrides.clear()


def test_signup_endpoint_returns_422_on_weak_password():
    app.dependency_overrides[get_auth_service] = lambda: _NullAuthService()
    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "new@example.com",
            "password": "weakpass",
            "role": "OWNER",
        },
    )
    assert r.status_code == 422


def test_signup_endpoint_returns_422_on_disposable_email():
    app.dependency_overrides[get_auth_service] = lambda: _NullAuthService()
    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "new@mailinator.com",
            "password": "StrongPass123",
            "role": "OWNER",
        },
    )
    assert r.status_code == 422


def test_login_email_normalized_before_service():
    captured = {}

    class _Capture:
        def login_with_password(self, **kwargs):
            captured.update(kwargs)
            return {
                "access_token": "t",
                "user": None,
                "role": "STAFF",
                "tenant_id": None,
            }

    app.dependency_overrides[get_auth_service] = lambda: _Capture()
    client = TestClient(app)
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "  User@Example.COM  ", "password": "StrongPass123"},
    )
    assert r.status_code == 200
    assert captured["email"] == "user@example.com"
