from enum import Enum

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.modules.auth.validators import (
    normalize_email,
    normalize_tenant_name,
    validate_password_strength,
    validate_phone_e164,
    validate_six_digit_code,
)


class TenantRole(str, Enum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    STAFF = "STAFF"


class AdminRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    FINANCE_ADMIN = "FINANCE_ADMIN"
    SUPPORT_ADMIN = "SUPPORT_ADMIN"
    OPS_ADMIN = "OPS_ADMIN"
    COMPLIANCE_ADMIN = "COMPLIANCE_ADMIN"


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    tenant_name: str | None = Field(default=None, max_length=255)
    role: TenantRole = TenantRole.OWNER

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        return normalize_email(v) if v is not None else v

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        return validate_password_strength(v)

    @field_validator("tenant_name")
    @classmethod
    def _validate_tenant(cls, v: str | None) -> str | None:
        return normalize_tenant_name(v)

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        return normalize_email(v) if v is not None else v


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        return normalize_email(v) if v is not None else v


class SocialLoginRequest(BaseModel):
    oauth_token: str = Field(min_length=10)


class MfaVerifyRequest(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6)
    is_admin_login: bool = False

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        return normalize_email(v) if v is not None else v

    @field_validator("otp")
    @classmethod
    def _validate_otp(cls, v: str) -> str:
        return validate_six_digit_code(v)


class CreateAdminRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    role: AdminRole = AdminRole.SUPPORT_ADMIN

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        return normalize_email(v) if v is not None else v

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        if len(v) < 12:
            raise ValueError("Admin password must be at least 12 characters")
        return validate_password_strength(v)


class MfaChannelType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"


class MfaChannelAddRequest(BaseModel):
    channel: MfaChannelType
    destination: str = Field(
        min_length=3,
        max_length=255,
        description="email address or E.164 phone number",
    )

    @model_validator(mode="after")
    def _validate_destination(self) -> "MfaChannelAddRequest":
        if self.channel == MfaChannelType.EMAIL:
            self.destination = normalize_email(self.destination)
        else:
            self.destination = validate_phone_e164(self.destination)
        return self


class MfaChannelVerifyRequest(BaseModel):
    channel: MfaChannelType
    otp: str = Field(min_length=6, max_length=6)

    @field_validator("otp")
    @classmethod
    def _validate_otp(cls, v: str) -> str:
        return validate_six_digit_code(v)


class MfaEnableRequest(BaseModel):
    pass


class MfaStatusResponse(BaseModel):
    mfa_enabled: bool
    email: str | None = None
    email_verified: bool = False
    phone: str | None = None
    phone_verified: bool = False


class MfaChannelResponse(BaseModel):
    channel: MfaChannelType
    destination: str
    verified: bool
    message: str | None = None


class UserInfoResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: str | None
    profile_picture_url: str | None
    role: str
    tenant_id: str | None
    is_admin: bool
    mfa_enabled: bool


class AuthResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    refresh_token: str | None = None
    expires_in: int | None = None
    user: UserInfoResponse | None = None
    role: str | None = None
    tenant_id: str | None = None
    mfa_required: bool = False
    message: str | None = None


# ---------------- password reset / email verify ----------------


class PasswordForgotRequest(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        return normalize_email(v) if v is not None else v


class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        return validate_password_strength(v)


class EmailVerifyRequest(BaseModel):
    token: str = Field(min_length=16, max_length=128)


class EmailResendRequest(BaseModel):
    pass


class SimpleMessageResponse(BaseModel):
    message: str


# ---------------- refresh / session ----------------


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=256)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class SessionInfo(BaseModel):
    id: str
    ip_address: str | None = None
    device_info: str | None = None
    location: str | None = None
    created_at: str | None = None
    last_used_at: str | None = None
    expires_at: str | None = None
    refresh_expires_at: str | None = None
    revoked: bool = False
    current: bool = False


class SessionListResponse(BaseModel):
    sessions: list[SessionInfo]


# ---------------- TOTP ----------------


class TotpSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str
    message: str = (
        "Scan the QR code in your authenticator app, then POST the 6-digit "
        "code to /mfa/totp/verify."
    )


class TotpVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        return validate_six_digit_code(v)
