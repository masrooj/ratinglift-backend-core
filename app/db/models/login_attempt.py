from sqlalchemy import Boolean, Column, DateTime, String, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class LoginAttempt(Base):
    """Records every login attempt (success and failure) for security tracking."""

    __tablename__ = "login_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    email = Column(String, nullable=False, index=True)
    ip_address = Column(String, nullable=True, index=True)
    user_agent = Column(String, nullable=True)
    success = Column(Boolean, nullable=False, default=False)
    reason = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


Index("ix_login_attempts_email_ts", LoginAttempt.email, LoginAttempt.timestamp.desc())
Index("ix_login_attempts_ip_ts", LoginAttempt.ip_address, LoginAttempt.timestamp.desc())
