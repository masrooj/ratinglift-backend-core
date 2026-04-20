from sqlalchemy import Column, DateTime, String, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class AdminActionLog(Base):
    """Dedicated audit trail for sensitive admin actions.

    Examples: impersonation, billing changes, tenant suspension, data deletion.
    """

    __tablename__ = "admin_action_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    admin_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    action = Column(String, nullable=False, index=True)
    target_entity = Column(String, nullable=True)
    target_id = Column(UUID(as_uuid=True), nullable=True)
    target_tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    before_value = Column(JSON, nullable=True)
    after_value = Column(JSON, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    request_path = Column(String, nullable=True)
    extra = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
