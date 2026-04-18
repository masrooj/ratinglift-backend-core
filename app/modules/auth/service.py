import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import UUID

import jwt
import redis
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import ExpiredSignatureError, InvalidTokenError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.db.models.audit_log import ActorType, AuditLog
from app.db.models.login_session import LoginSession
from app.db.models.tenant import Tenant
from app.db.models.user import User, UserRole
from app.db.redis import get_redis_client
from app.db.session import get_db
from app.modules.auth import totp as totp_lib
from app.modules.auth.mfa import (
    CHANNEL_EMAIL_PURPOSE,
    CHANNEL_PHONE_PURPOSE,
    LOGIN_PURPOSE,
    generate_and_store_otp,
    verify_stored_otp,
)
from app.modules.auth.oauth import SocialProvider, verify_oauth_token
from app.modules.auth.password_reset import (
    EMAIL_VERIFY_PREFIX,
    PASSWORD_RESET_PREFIX,
    consume_token,
    issue_token,
)
from app.modules.auth.schemas import (
    AuthResponse,
    MfaStatusResponse,
    SessionInfo,
    SessionListResponse,
    TotpSetupResponse,
    UserInfoResponse,
)
from app.modules.auth.senders import get_email_sender, get_sms_sender
from app.modules.auth.tokens import hash_refresh_token, new_jti, new_refresh_token

TENANT_ROLES = {
    UserRole.OWNER.value,
    UserRole.MANAGER.value,
    UserRole.STAFF.value,
}

ADMIN_ROLES = {
    UserRole.SUPER_ADMIN.value,
    UserRole.FINANCE_ADMIN.value,
    UserRole.SUPPORT_ADMIN.value,
    UserRole.OPS_ADMIN.value,
    UserRole.COMPLIANCE_ADMIN.value,
}

oauth2_scheme = HTTPBearer(
    bearerFormat="JWT",
    description=(
        "Paste the access_token returned by POST /api/v1/auth/login or "
        "POST /api/v1/admin/auth/login (no `Bearer ` prefix needed)."
    ),
)


def _extract_token(creds: HTTPAuthorizationCredentials) -> str:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    return creds.credentials


class AuthService:
    def __init__(self, db: Session, redis_client: redis.Redis):
        self.db = db
        self.redis = redis_client

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _tenant_name_from_email(self, email: str) -> str:
        local = email.split("@", 1)[0].replace("_", "-")
        base = (local[:30] or "tenant")
        return f"{base}-tenant"

    def _build_access_token(self, user: User, *, jti: str, admin_token: bool = False) -> tuple[str, int]:
        now = self._now()
        expiry_minutes = (
            settings.admin_access_token_expiry_minutes
            if admin_token
            else settings.access_token_expiry_minutes
        )
        expires_at = now + timedelta(minutes=expiry_minutes)

        payload = {
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "role": user.role.value,
            "is_admin": user.is_admin,
            "jti": jti,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }

        secret = settings.admin_jwt_secret if admin_token else settings.jwt_secret
        token = jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)
        return token, expiry_minutes * 60

    def _decode_token_payload(self, token: str) -> dict:
        for secret in (settings.jwt_secret, settings.admin_jwt_secret):
            try:
                return jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
            except ExpiredSignatureError as exc:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from exc
            except InvalidTokenError:
                continue

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

    def _check_login_rate_limit(self, scope: str, identifier: str) -> None:
        key = f"auth:rate-limit:{scope}:{identifier}"
        attempts = self.redis.incr(key)
        if attempts == 1:
            self.redis.expire(key, settings.login_rate_limit_window_seconds)

        if attempts > settings.login_rate_limit_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Please retry later.",
            )

    def _record_audit_login_attempt(
        self,
        *,
        email: str,
        success: bool,
        ip_address: str | None,
        user: User | None,
        reason: str | None = None,
    ) -> None:
        event = AuditLog(
            actor_id=user.id if user else None,
            actor_type=ActorType.user if user else ActorType.system,
            action="login_attempt",
            entity="auth",
            after_value={
                "email": email,
                "success": success,
                "reason": reason,
            },
            ip_address=ip_address,
        )
        self.db.add(event)

    def _track_failed_login(self, user: User | None, email: str) -> None:
        if user:
            self._register_failure_and_maybe_lock(user)

        key = f"auth:failed-login:{email.lower()}"
        attempts = self.redis.incr(key)
        if attempts == 1:
            self.redis.expire(key, 60 * 60 * 24)

    def _reset_failed_login(self, user: User) -> None:
        user.failed_login_attempts = 0
        user.locked_until = None
        self.db.add(user)

    def _create_session(
        self,
        *,
        user: User,
        ip_address: str | None,
        device_info: str | None,
        location: str | None,
        admin_token: bool,
    ) -> tuple[LoginSession, str, str]:
        """Create a login session row plus JWT jti + plaintext refresh token.

        Returns ``(session, jti, refresh_token_plaintext)``.
        """
        now = self._now()
        expiry_minutes = (
            settings.admin_access_token_expiry_minutes
            if admin_token
            else settings.access_token_expiry_minutes
        )
        jti = new_jti()
        refresh_plain = new_refresh_token()
        session = LoginSession(
            user_id=user.id,
            jti=jti,
            refresh_token_hash=hash_refresh_token(refresh_plain),
            refresh_expires_at=now + timedelta(minutes=settings.refresh_token_expiry_minutes),
            ip_address=ip_address,
            device_info=device_info,
            location=location,
            last_used_at=now,
            expires_at=now + timedelta(minutes=expiry_minutes),
        )
        self.db.add(session)
        self.db.flush()
        return session, jti, refresh_plain

    def _serialize_user(self, user: User) -> UserInfoResponse:
        return UserInfoResponse(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            profile_picture_url=user.profile_picture_url,
            role=user.role.value,
            tenant_id=str(user.tenant_id) if user.tenant_id else None,
            is_admin=user.is_admin,
            mfa_enabled=user.mfa_enabled,
        )

    def _build_auth_response(
        self,
        user: User,
        access_token: str,
        *,
        refresh_token: str | None = None,
        expires_in: int | None = None,
    ) -> AuthResponse:
        user_info = self._serialize_user(user)
        return AuthResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            user=user_info,
            role=user_info.role,
            tenant_id=user_info.tenant_id,
        )

    def _assert_not_locked(self, user: User | None) -> None:
        if not user or not user.locked_until:
            return
        now = self._now()
        locked_until = user.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if now < locked_until:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account temporarily locked due to failed login attempts.",
            )
        # Lockout window expired — clear it.
        user.locked_until = None
        user.failed_login_attempts = 0
        self.db.add(user)

    def _register_failure_and_maybe_lock(self, user: User | None) -> None:
        if not user:
            return
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= settings.account_lockout_threshold:
            user.locked_until = self._now() + timedelta(minutes=settings.account_lockout_minutes)
            self.db.add(
                AuditLog(
                    actor_id=user.id,
                    actor_type=ActorType.system,
                    action="account_locked",
                    entity="user",
                    entity_id=user.id,
                    after_value={"until": user.locked_until.isoformat()},
                )
            )
        self.db.add(user)

    def _get_or_create_tenant(self, tenant_name: str | None, email: str) -> Tenant:
        requested_name = (tenant_name or self._tenant_name_from_email(email)).strip().lower().replace(" ", "-")
        tenant = self.db.query(Tenant).filter(Tenant.name == requested_name).first()
        if tenant:
            return tenant

        candidate = requested_name
        suffix = 1
        while self.db.query(Tenant).filter(Tenant.name == candidate).first() is not None:
            suffix += 1
            candidate = f"{requested_name}-{suffix}"

        tenant = Tenant(name=candidate)
        self.db.add(tenant)
        self.db.flush()
        return tenant

    def get_user_by_email(self, email: str) -> User | None:
        return self.db.query(User).filter(User.email == email.lower()).first()

    def signup(
        self,
        *,
        email: str,
        password: str,
        full_name: str | None,
        tenant_name: str | None,
        role: str,
        ip_address: str | None,
        device_info: str | None,
        location: str | None,
    ) -> AuthResponse:
        existing = self.get_user_by_email(email)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

        tenant = self._get_or_create_tenant(tenant_name, email)
        user = User(
            email=email.lower(),
            full_name=full_name,
            password_hash=get_password_hash(password),
            role=UserRole(role),
            tenant_id=tenant.id,
            is_admin=False,
            auth_provider="password",
        )
        self.db.add(user)
        self.db.flush()

        session, jti, refresh_plain = self._create_session(
            user=user,
            ip_address=ip_address,
            device_info=device_info,
            location=location,
            admin_token=False,
        )
        token, expires_in = self._build_access_token(user, jti=jti, admin_token=False)
        self._record_audit_login_attempt(email=email, success=True, ip_address=ip_address, user=user)

        # Fire-and-forget: send email verification link.
        try:
            self._send_email_verification(user)
        except Exception:  # noqa: BLE001
            pass

        self.db.commit()
        self.db.refresh(user)
        return self._build_auth_response(
            user, token, refresh_token=refresh_plain, expires_in=expires_in
        )

    def _assert_user_can_login(self, user: User, *, admin_only: bool) -> None:
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

        if admin_only:
            if not user.is_admin:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
            if user.role.value not in ADMIN_ROLES:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin role")

    def login_with_password(
        self,
        *,
        email: str,
        password: str,
        ip_address: str | None,
        device_info: str | None,
        location: str | None,
        admin_only: bool,
    ) -> AuthResponse:
        normalized_email = email.lower()
        limiter_id = f"{normalized_email}:{ip_address or 'unknown'}"
        self._check_login_rate_limit("admin" if admin_only else "user", limiter_id)

        user = self.get_user_by_email(normalized_email)
        self._assert_not_locked(user)
        if not user or not verify_password(password, user.password_hash):
            self._track_failed_login(user, normalized_email)
            self._record_audit_login_attempt(
                email=normalized_email,
                success=False,
                ip_address=ip_address,
                user=user,
                reason="invalid_credentials",
            )
            self.db.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

        self._assert_user_can_login(user, admin_only=admin_only)

        if user.mfa_enabled:
            # If the user has verified TOTP, use the app as the source of truth
            # and skip sending an OTP. Otherwise deliver an OTP to verified
            # channels.
            if not user.totp_verified:
                otp = generate_and_store_otp(
                    self.redis,
                    user.id,
                    settings.mfa_otp_ttl_seconds,
                    purpose=LOGIN_PURPOSE,
                )
                self._dispatch_login_otp(user, otp)
                challenge_message = (
                    "OTP sent to your verified channels. Verify at /api/v1/auth/mfa/verify."
                )
            else:
                challenge_message = (
                    "Enter the 6-digit code from your authenticator app at /api/v1/auth/mfa/verify."
                )
            self._record_audit_login_attempt(
                email=normalized_email,
                success=False,
                ip_address=ip_address,
                user=user,
                reason="mfa_challenge_issued",
            )
            self.db.commit()
            return AuthResponse(mfa_required=True, message=challenge_message)

        self._reset_failed_login(user)
        session, jti, refresh_plain = self._create_session(
            user=user,
            ip_address=ip_address,
            device_info=device_info,
            location=location,
            admin_token=admin_only,
        )
        token, expires_in = self._build_access_token(user, jti=jti, admin_token=admin_only)
        self._record_audit_login_attempt(email=normalized_email, success=True, ip_address=ip_address, user=user)
        self.db.commit()
        self.db.refresh(user)
        return self._build_auth_response(
            user, token, refresh_token=refresh_plain, expires_in=expires_in
        )

    def verify_mfa_login(
        self,
        *,
        email: str,
        otp: str,
        ip_address: str | None,
        device_info: str | None,
        location: str | None,
        admin_only: bool,
    ) -> AuthResponse:
        user = self.get_user_by_email(email.lower())
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        self._assert_not_locked(user)
        self._assert_user_can_login(user, admin_only=admin_only)

        if not user.mfa_enabled:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled")

        code_accepted = False
        if user.totp_verified and user.totp_secret and totp_lib.verify(user.totp_secret, otp):
            code_accepted = True
        if not code_accepted and verify_stored_otp(self.redis, user.id, otp, purpose=LOGIN_PURPOSE):
            code_accepted = True

        if not code_accepted:
            self._track_failed_login(user, email)
            self._record_audit_login_attempt(
                email=email.lower(),
                success=False,
                ip_address=ip_address,
                user=user,
                reason="invalid_otp",
            )
            self.db.commit()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OTP")

        self._reset_failed_login(user)
        session, jti, refresh_plain = self._create_session(
            user=user,
            ip_address=ip_address,
            device_info=device_info,
            location=location,
            admin_token=admin_only,
        )
        token, expires_in = self._build_access_token(user, jti=jti, admin_token=admin_only)
        self._record_audit_login_attempt(email=user.email, success=True, ip_address=ip_address, user=user)
        self.db.commit()
        self.db.refresh(user)
        return self._build_auth_response(
            user, token, refresh_token=refresh_plain, expires_in=expires_in
        )

    async def login_with_social_provider(
        self,
        *,
        provider: SocialProvider,
        oauth_token: str,
        ip_address: str | None,
        device_info: str | None,
        location: str | None,
    ) -> AuthResponse:
        profile = await verify_oauth_token(provider, oauth_token)
        user = self.get_user_by_email(profile.email)

        if not user:
            tenant = self._get_or_create_tenant(None, profile.email)
            user = User(
                email=profile.email,
                full_name=profile.name,
                profile_picture_url=profile.picture,
                password_hash=get_password_hash(secrets.token_urlsafe(32)),
                role=UserRole.STAFF,
                tenant_id=tenant.id,
                is_admin=False,
                auth_provider=provider.value,
                oauth_subject=profile.subject,
            )
            self.db.add(user)
            self.db.flush()
        else:
            if not user.tenant_id:
                tenant = self._get_or_create_tenant(None, profile.email)
                user.tenant_id = tenant.id
            user.full_name = profile.name or user.full_name
            user.profile_picture_url = profile.picture or user.profile_picture_url
            user.auth_provider = provider.value
            user.oauth_subject = profile.subject or user.oauth_subject
            self.db.add(user)

        token_admin = False
        self._reset_failed_login(user)
        session, jti, refresh_plain = self._create_session(
            user=user,
            ip_address=ip_address,
            device_info=device_info,
            location=location,
            admin_token=token_admin,
        )
        token, expires_in = self._build_access_token(user, jti=jti, admin_token=token_admin)
        self._record_audit_login_attempt(email=user.email, success=True, ip_address=ip_address, user=user)
        self.db.commit()
        self.db.refresh(user)
        return self._build_auth_response(
            user, token, refresh_token=refresh_plain, expires_in=expires_in
        )

    # ---------------- MFA setup ----------------

    def _dispatch_login_otp(self, user: User, otp: str) -> None:
        """Send login OTP to every verified channel the user has."""
        sent = False
        if user.mfa_email and user.mfa_email_verified:
            get_email_sender().send(user.mfa_email, otp, LOGIN_PURPOSE)
            sent = True
        if user.mfa_phone and user.mfa_phone_verified:
            get_sms_sender().send(user.mfa_phone, otp, LOGIN_PURPOSE)
            sent = True
        if not sent:
            # Fallback: send to account email so user is never locked out.
            get_email_sender().send(user.email, otp, LOGIN_PURPOSE)

    def get_mfa_status(self, user: User) -> MfaStatusResponse:
        return MfaStatusResponse(
            mfa_enabled=user.mfa_enabled,
            email=user.mfa_email,
            email_verified=user.mfa_email_verified,
            phone=user.mfa_phone,
            phone_verified=user.mfa_phone_verified,
        )

    def add_mfa_channel(self, user: User, channel: str, destination: str) -> str:
        """Set the MFA email/phone on the user and send a verification OTP.

        Marks the channel as unverified until the user confirms via
        ``verify_mfa_channel``. Returns the purpose key used for the OTP.
        """
        destination = destination.strip()
        if not destination:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Destination required")

        if channel == "email":
            user.mfa_email = destination.lower()
            user.mfa_email_verified = False
            purpose = CHANNEL_EMAIL_PURPOSE
            sender = get_email_sender()
        elif channel == "phone":
            user.mfa_phone = destination
            user.mfa_phone_verified = False
            purpose = CHANNEL_PHONE_PURPOSE
            sender = get_sms_sender()
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported channel")

        self.db.add(user)
        otp = generate_and_store_otp(
            self.redis,
            user.id,
            settings.mfa_otp_ttl_seconds,
            purpose=purpose,
        )
        sender.send(destination, otp, purpose)
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="mfa_channel_added",
                entity="user",
                entity_id=user.id,
                after_value={"channel": channel, "destination": destination},
            )
        )
        self.db.commit()
        return purpose

    def verify_mfa_channel(self, user: User, channel: str, otp: str) -> None:
        if channel == "email":
            purpose = CHANNEL_EMAIL_PURPOSE
            if not user.mfa_email:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No email channel configured")
        elif channel == "phone":
            purpose = CHANNEL_PHONE_PURPOSE
            if not user.mfa_phone:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No phone channel configured")
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported channel")

        if not verify_stored_otp(self.redis, user.id, otp, purpose=purpose):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired OTP")

        if channel == "email":
            user.mfa_email_verified = True
        else:
            user.mfa_phone_verified = True

        self.db.add(user)
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="mfa_channel_verified",
                entity="user",
                entity_id=user.id,
                after_value={"channel": channel},
            )
        )
        self.db.commit()

    def enable_mfa(self, user: User) -> None:
        if not (user.mfa_email_verified or user.mfa_phone_verified):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verify at least one channel (email or phone) before enabling MFA.",
            )
        user.mfa_enabled = True
        self.db.add(user)
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="mfa_enabled",
                entity="user",
                entity_id=user.id,
            )
        )
        self.db.commit()

    def disable_mfa(self, user: User) -> None:
        user.mfa_enabled = False
        self.db.add(user)
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="mfa_disabled",
                entity="user",
                entity_id=user.id,
            )
        )
        self.db.commit()

    # ---------------- Email verification ----------------

    def _send_email_verification(self, user: User) -> str:
        token = issue_token(
            self.redis,
            EMAIL_VERIFY_PREFIX,
            user.id,
            settings.email_verification_ttl_minutes * 60,
        )
        link = f"{settings.app_public_url.rstrip('/')}/api/v1/auth/email/verify?token={token}"
        body = (
            f"Hi {user.full_name or user.email},\n\n"
            f"Confirm your email to activate your RatingLift account:\n{link}\n\n"
            "If you didn't sign up, you can ignore this email."
        )
        try:
            get_email_sender().send_message(user.email, "Confirm your email", body)
        except Exception:  # noqa: BLE001
            pass
        return token

    def request_email_verification(self, user: User) -> None:
        if user.email_verified:
            return
        self._send_email_verification(user)

    def verify_email_token(self, token: str) -> User:
        user_id_str = consume_token(self.redis, EMAIL_VERIFY_PREFIX, token)
        if not user_id_str:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")
        user = self.db.query(User).filter(User.id == UUID(user_id_str)).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        user.email_verified = True
        self.db.add(user)
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="email_verified",
                entity="user",
                entity_id=user.id,
            )
        )
        self.db.commit()
        return user

    # ---------------- Password reset ----------------

    def request_password_reset(self, email: str) -> None:
        """Issue a password-reset token. Always returns without signalling whether
        the account exists (to avoid user enumeration)."""
        user = self.get_user_by_email(email.lower())
        if not user:
            return
        token = issue_token(
            self.redis,
            PASSWORD_RESET_PREFIX,
            user.id,
            settings.password_reset_ttl_minutes * 60,
        )
        link = f"{settings.app_public_url.rstrip('/')}/api/v1/auth/password/reset?token={token}"
        body = (
            "We received a request to reset your RatingLift password.\n\n"
            f"Reset link (valid for {settings.password_reset_ttl_minutes} minutes):\n{link}\n\n"
            "If you didn't request this, you can safely ignore this email."
        )
        try:
            get_email_sender().send_message(user.email, "Reset your password", body)
        except Exception:  # noqa: BLE001
            pass
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="password_reset_requested",
                entity="user",
                entity_id=user.id,
            )
        )
        self.db.commit()

    def reset_password(self, token: str, new_password: str) -> None:
        user_id_str = consume_token(self.redis, PASSWORD_RESET_PREFIX, token)
        if not user_id_str:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")
        user = self.db.query(User).filter(User.id == UUID(user_id_str)).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        user.password_hash = get_password_hash(new_password)
        user.failed_login_attempts = 0
        user.locked_until = None
        self.db.add(user)

        # Revoke all active sessions for the user.
        self.db.query(LoginSession).filter(
            LoginSession.user_id == user.id,
            LoginSession.revoked.is_(False),
        ).update({LoginSession.revoked: True})

        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="password_reset",
                entity="user",
                entity_id=user.id,
            )
        )
        self.db.commit()

    # ---------------- Refresh tokens / sessions ----------------

    def refresh_access_token(
        self,
        *,
        refresh_token: str,
        ip_address: str | None,
    ) -> AuthResponse:
        token_hash = hash_refresh_token(refresh_token)
        now = self._now()
        session = (
            self.db.query(LoginSession)
            .filter(LoginSession.refresh_token_hash == token_hash)
            .first()
        )
        if not session or session.revoked:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

        refresh_expires_at = session.refresh_expires_at
        if refresh_expires_at and refresh_expires_at.tzinfo is None:
            refresh_expires_at = refresh_expires_at.replace(tzinfo=timezone.utc)
        if refresh_expires_at and now >= refresh_expires_at:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

        user = self.db.query(User).filter(User.id == session.user_id).first()
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not available")

        # Rotate refresh token and access token.
        new_refresh_plain = new_refresh_token()
        new_jti_value = new_jti()
        admin_token = user.is_admin
        access_expiry_minutes = (
            settings.admin_access_token_expiry_minutes
            if admin_token
            else settings.access_token_expiry_minutes
        )
        session.refresh_token_hash = hash_refresh_token(new_refresh_plain)
        session.refresh_expires_at = now + timedelta(minutes=settings.refresh_token_expiry_minutes)
        session.jti = new_jti_value
        session.last_used_at = now
        session.expires_at = now + timedelta(minutes=access_expiry_minutes)
        session.ip_address = ip_address or session.ip_address
        self.db.add(session)
        token, expires_in = self._build_access_token(user, jti=new_jti_value, admin_token=admin_token)
        self.db.commit()
        self.db.refresh(user)
        return self._build_auth_response(
            user, token, refresh_token=new_refresh_plain, expires_in=expires_in
        )

    def logout(
        self,
        *,
        user: User,
        jti: str | None,
        refresh_token: str | None = None,
    ) -> None:
        query = self.db.query(LoginSession).filter(LoginSession.user_id == user.id)
        session: LoginSession | None = None
        if jti:
            session = query.filter(LoginSession.jti == jti).first()
        if session is None and refresh_token:
            session = query.filter(
                LoginSession.refresh_token_hash == hash_refresh_token(refresh_token)
            ).first()
        if session is None:
            return
        session.revoked = True
        self.db.add(session)
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="logout",
                entity="login_session",
                entity_id=session.id,
            )
        )
        self.db.commit()

    def list_sessions(self, user: User, current_jti: str | None = None) -> SessionListResponse:
        rows = (
            self.db.query(LoginSession)
            .filter(LoginSession.user_id == user.id)
            .order_by(LoginSession.created_at.desc())
            .all()
        )

        def _iso(dt: datetime | None) -> str | None:
            return dt.isoformat() if dt else None

        sessions = [
            SessionInfo(
                id=str(s.id),
                ip_address=s.ip_address,
                device_info=s.device_info,
                location=s.location,
                created_at=_iso(s.created_at),
                last_used_at=_iso(s.last_used_at),
                expires_at=_iso(s.expires_at),
                refresh_expires_at=_iso(s.refresh_expires_at),
                revoked=s.revoked,
                current=bool(current_jti and s.jti == current_jti),
            )
            for s in rows
        ]
        return SessionListResponse(sessions=sessions)

    def revoke_session(self, user: User, session_id: str) -> None:
        try:
            sid = UUID(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid session id") from exc
        session = (
            self.db.query(LoginSession)
            .filter(LoginSession.id == sid, LoginSession.user_id == user.id)
            .first()
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        session.revoked = True
        self.db.add(session)
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="session_revoked",
                entity="login_session",
                entity_id=session.id,
            )
        )
        self.db.commit()

    # ---------------- TOTP ----------------

    def setup_totp(self, user: User) -> TotpSetupResponse:
        secret = totp_lib.create_secret()
        user.totp_secret = secret
        user.totp_verified = False
        self.db.add(user)
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="totp_setup",
                entity="user",
                entity_id=user.id,
            )
        )
        self.db.commit()
        return TotpSetupResponse(
            secret=secret,
            otpauth_uri=totp_lib.provisioning_uri(user.email, secret),
        )

    def verify_totp(self, user: User, code: str) -> None:
        if not user.totp_secret:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TOTP not initialized")
        if not totp_lib.verify(user.totp_secret, code):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid TOTP code")
        user.totp_verified = True
        self.db.add(user)
        self.db.add(
            AuditLog(
                actor_id=user.id,
                actor_type=ActorType.user,
                action="totp_verified",
                entity="user",
                entity_id=user.id,
            )
        )
        self.db.commit()

    # ---------------- Admin management ----------------

    def create_admin(
        self,
        *,
        actor: User,
        email: str,
        password: str,
        full_name: str | None,
        role: str,
    ) -> AuthResponse:
        if role not in ADMIN_ROLES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid admin role")

        normalized_email = email.lower()
        if self.get_user_by_email(normalized_email):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

        admin_user = User(
            email=normalized_email,
            full_name=full_name,
            password_hash=get_password_hash(password),
            role=UserRole(role),
            tenant_id=None,
            is_admin=True,
            auth_provider="password",
        )
        self.db.add(admin_user)
        self.db.flush()

        self.db.add(
            AuditLog(
                actor_id=actor.id,
                actor_type=ActorType.user,
                action="admin_created",
                entity="user",
                entity_id=admin_user.id,
                after_value={"email": normalized_email, "role": role},
            )
        )
        self.db.commit()
        self.db.refresh(admin_user)
        return AuthResponse(
            user=self._serialize_user(admin_user),
            role=admin_user.role.value,
            message="Admin created successfully.",
        )


def get_auth_service(
    db: Session = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis_client),
) -> AuthService:
    return AuthService(db=db, redis_client=redis_client)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = _extract_token(creds)
    service = AuthService(db=db, redis_client=get_redis_client())
    payload = service._decode_token_payload(token)

    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token")

    user = db.query(User).filter(User.id == UUID(str(user_id))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # If the session tied to this jti has been revoked, reject the token.
    jti = payload.get("jti")
    if jti:
        session = db.query(LoginSession).filter(LoginSession.jti == jti).first()
        if session and session.revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked"
            )

    return user


def get_current_jti(creds: HTTPAuthorizationCredentials = Depends(oauth2_scheme)) -> str | None:
    token = _extract_token(creds)
    try:
        # Non-strict decode: we just want the jti claim.
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
    except InvalidTokenError:
        try:
            payload = jwt.decode(
                token,
                settings.admin_jwt_secret,
                algorithms=[settings.jwt_algorithm],
                options={"verify_exp": False},
            )
        except InvalidTokenError:
            return None
    return payload.get("jti")


def require_role(roles: list[str]):
    allowed = set()
    for role in roles:
        if hasattr(role, "value"):
            allowed.add(str(role.value))
        else:
            allowed.add(str(role))

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        user_role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
        if user_role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return current_user

    return dependency


def is_admin_role(role: str) -> bool:
    return role in ADMIN_ROLES


def is_tenant_role(role: str) -> bool:
    return role in TENANT_ROLES


def normalize_roles(roles: Iterable[str]) -> list[str]:
    return [str(role.value) if hasattr(role, "value") else str(role) for role in roles]
