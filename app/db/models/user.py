from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Enum, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import enum

from app.db.base import Base


class UserRole(enum.Enum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    STAFF = "STAFF"
    SUPER_ADMIN = "SUPER_ADMIN"
    FINANCE_ADMIN = "FINANCE_ADMIN"
    SUPPORT_ADMIN = "SUPPORT_ADMIN"
    OPS_ADMIN = "OPS_ADMIN"
    COMPLIANCE_ADMIN = "COMPLIANCE_ADMIN"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True)  # nullable for admin
    email = Column(String, nullable=False, unique=True)
    full_name = Column(String, nullable=True)
    profile_picture_url = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    auth_provider = Column(String, nullable=False, default="password")
    oauth_subject = Column(String, nullable=True)
    role = Column(Enum(UserRole, name="userrole"), nullable=False, default=UserRole.STAFF)
    is_active = Column(Boolean, nullable=False, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    email_verified = Column(Boolean, nullable=False, default=False)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    mfa_email = Column(String, nullable=True)
    mfa_phone = Column(String, nullable=True)
    mfa_email_verified = Column(Boolean, nullable=False, default=False)
    mfa_phone_verified = Column(Boolean, nullable=False, default=False)
    totp_secret = Column(String, nullable=True)
    totp_verified = Column(Boolean, nullable=False, default=False)
    failed_login_attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)