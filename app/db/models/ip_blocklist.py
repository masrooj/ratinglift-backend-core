from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class IpBlocklist(Base):
    """IPs blocked from authentication endpoints due to abuse."""

    __tablename__ = "ip_blocklist"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    ip_address = Column(String, nullable=False, unique=True, index=True)
    reason = Column(String, nullable=True)
    failed_attempts = Column(Integer, nullable=False, default=0)
    blocked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
