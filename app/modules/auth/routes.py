from fastapi import APIRouter, Depends, Request

from app.db.models.user import User, UserRole
from app.modules.auth.oauth import SocialProvider
from app.modules.auth.schemas import (
    AdminLoginRequest,
    AuthResponse,
    CreateAdminRequest,
    EmailResendRequest,
    EmailVerifyRequest,
    LoginRequest,
    LogoutRequest,
    MfaChannelAddRequest,
    MfaChannelResponse,
    MfaChannelVerifyRequest,
    MfaEnableRequest,
    MfaStatusResponse,
    MfaVerifyRequest,
    PasswordForgotRequest,
    PasswordResetRequest,
    RefreshRequest,
    SessionListResponse,
    SignupRequest,
    SimpleMessageResponse,
    SocialLoginRequest,
    TotpSetupResponse,
    TotpVerifyRequest,
)
from app.modules.auth.service import (
    AuthService,
    get_auth_service,
    get_current_jti,
    get_current_user,
    require_role,
)

router = APIRouter()
auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
admin_auth_router = APIRouter(prefix="/api/v1/admin/auth", tags=["admin-auth"])


def _get_client_ip(request: Request) -> str | None:
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _device_info(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _location(request: Request) -> str | None:
    return request.headers.get("x-user-location")


@auth_router.post("/signup", response_model=AuthResponse)
async def signup(
    payload: SignupRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.signup(
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        tenant_name=payload.tenant_name,
        role=payload.role.value,
        ip_address=_get_client_ip(request),
        device_info=_device_info(request),
        location=_location(request),
    )


@auth_router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.login_with_password(
        email=payload.email,
        password=payload.password,
        ip_address=_get_client_ip(request),
        device_info=_device_info(request),
        location=_location(request),
        admin_only=False,
    )


@admin_auth_router.post("/login", response_model=AuthResponse)
async def admin_login(
    payload: AdminLoginRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.login_with_password(
        email=payload.email,
        password=payload.password,
        ip_address=_get_client_ip(request),
        device_info=_device_info(request),
        location=_location(request),
        admin_only=True,
    )


@auth_router.post("/mfa/verify", response_model=AuthResponse)
async def verify_mfa(
    payload: MfaVerifyRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.verify_mfa_login(
        email=payload.email,
        otp=payload.otp,
        ip_address=_get_client_ip(request),
        device_info=_device_info(request),
        location=_location(request),
        admin_only=payload.is_admin_login,
    )


@auth_router.post("/social/google", response_model=AuthResponse)
async def social_google_login(
    payload: SocialLoginRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    return await auth_service.login_with_social_provider(
        provider=SocialProvider.GOOGLE,
        oauth_token=payload.oauth_token,
        ip_address=_get_client_ip(request),
        device_info=_device_info(request),
        location=_location(request),
    )


@auth_router.post("/social/microsoft", response_model=AuthResponse)
async def social_microsoft_login(
    payload: SocialLoginRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    return await auth_service.login_with_social_provider(
        provider=SocialProvider.MICROSOFT,
        oauth_token=payload.oauth_token,
        ip_address=_get_client_ip(request),
        device_info=_device_info(request),
        location=_location(request),
    )


@auth_router.post("/social/facebook", response_model=AuthResponse)
async def social_facebook_login(
    payload: SocialLoginRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    return await auth_service.login_with_social_provider(
        provider=SocialProvider.FACEBOOK,
        oauth_token=payload.oauth_token,
        ip_address=_get_client_ip(request),
        device_info=_device_info(request),
        location=_location(request),
    )


@auth_router.get("/me", response_model=AuthResponse)
async def current_user_profile(current_user=Depends(get_current_user)):
    return AuthResponse(
        user={
            "id": str(current_user.id),
            "email": current_user.email,
            "full_name": current_user.full_name,
            "profile_picture_url": current_user.profile_picture_url,
            "role": current_user.role.value,
            "tenant_id": str(current_user.tenant_id) if current_user.tenant_id else None,
            "is_admin": current_user.is_admin,
            "mfa_enabled": current_user.mfa_enabled,
        },
        role=current_user.role.value,
        tenant_id=str(current_user.tenant_id) if current_user.tenant_id else None,
    )


@admin_auth_router.post("/create-admin", response_model=AuthResponse, status_code=201)
async def create_admin(
    payload: CreateAdminRequest,
    actor: User = Depends(require_role([UserRole.SUPER_ADMIN.value])),
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.create_admin(
        actor=actor,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        role=payload.role.value,
    )


@auth_router.get("/mfa/status", response_model=MfaStatusResponse)
async def mfa_status(
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.get_mfa_status(current_user)


@auth_router.post("/mfa/channel", response_model=MfaChannelResponse)
async def add_mfa_channel(
    payload: MfaChannelAddRequest,
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.add_mfa_channel(current_user, payload.channel.value, payload.destination)
    return MfaChannelResponse(
        channel=payload.channel,
        destination=payload.destination,
        verified=False,
        message="Verification OTP sent. Call /mfa/channel/verify to confirm.",
    )


@auth_router.post("/mfa/channel/verify", response_model=MfaChannelResponse)
async def verify_mfa_channel(
    payload: MfaChannelVerifyRequest,
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.verify_mfa_channel(current_user, payload.channel.value, payload.otp)
    destination = (
        current_user.mfa_email if payload.channel.value == "email" else current_user.mfa_phone
    ) or ""
    return MfaChannelResponse(
        channel=payload.channel,
        destination=destination,
        verified=True,
        message="Channel verified.",
    )


@auth_router.post("/mfa/enable", response_model=MfaStatusResponse)
async def enable_mfa(
    _payload: MfaEnableRequest | None = None,
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.enable_mfa(current_user)
    return auth_service.get_mfa_status(current_user)


@auth_router.post("/mfa/disable", response_model=MfaStatusResponse)
async def disable_mfa(
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.disable_mfa(current_user)
    return auth_service.get_mfa_status(current_user)


# ---------------- Password reset ----------------


@auth_router.post("/password/forgot", response_model=SimpleMessageResponse)
async def password_forgot(
    payload: PasswordForgotRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.request_password_reset(payload.email)
    # Always return 200 to avoid user enumeration.
    return SimpleMessageResponse(
        message="If an account exists for that email, a reset link has been sent."
    )


@auth_router.post("/password/reset", response_model=SimpleMessageResponse)
async def password_reset(
    payload: PasswordResetRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.reset_password(payload.token, payload.new_password)
    return SimpleMessageResponse(message="Password has been reset. Please log in again.")


# ---------------- Email verification ----------------


@auth_router.post("/email/verify", response_model=SimpleMessageResponse)
async def email_verify(
    payload: EmailVerifyRequest,
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.verify_email_token(payload.token)
    return SimpleMessageResponse(message="Email verified.")


@auth_router.post("/email/resend", response_model=SimpleMessageResponse)
async def email_resend(
    _payload: EmailResendRequest | None = None,
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.request_email_verification(current_user)
    return SimpleMessageResponse(message="Verification email sent if required.")


# ---------------- Refresh tokens + session management ----------------


@auth_router.post("/refresh", response_model=AuthResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.refresh_access_token(
        refresh_token=payload.refresh_token,
        ip_address=_get_client_ip(request),
    )


@auth_router.post("/logout", response_model=SimpleMessageResponse)
async def logout(
    payload: LogoutRequest | None = None,
    current_user: User = Depends(get_current_user),
    jti: str | None = Depends(get_current_jti),
    auth_service: AuthService = Depends(get_auth_service),
):
    refresh_token = payload.refresh_token if payload else None
    auth_service.logout(user=current_user, jti=jti, refresh_token=refresh_token)
    return SimpleMessageResponse(message="Logged out.")


@auth_router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    current_user: User = Depends(get_current_user),
    jti: str | None = Depends(get_current_jti),
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.list_sessions(current_user, current_jti=jti)


@auth_router.post("/sessions/{session_id}/revoke", response_model=SimpleMessageResponse)
async def revoke_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.revoke_session(current_user, session_id)
    return SimpleMessageResponse(message="Session revoked.")


# ---------------- TOTP ----------------


@auth_router.post("/mfa/totp/setup", response_model=TotpSetupResponse)
async def totp_setup(
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    return auth_service.setup_totp(current_user)


@auth_router.post("/mfa/totp/verify", response_model=MfaStatusResponse)
async def totp_verify(
    payload: TotpVerifyRequest,
    current_user: User = Depends(get_current_user),
    auth_service: AuthService = Depends(get_auth_service),
):
    auth_service.verify_totp(current_user, payload.code)
    return auth_service.get_mfa_status(current_user)


router.include_router(auth_router)
router.include_router(admin_auth_router)
