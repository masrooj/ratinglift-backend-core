from sqlalchemy import Column, String, DateTime, ForeignKey, JSON, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import enum

from app.db.base import Base


class ActorType(enum.Enum):
    user = "user"
    system = "system"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    actor_id = Column(UUID(as_uuid=True), nullable=True)  # Can be user_id or system
    actor_type = Column(Enum(ActorType), nullable=False, default=ActorType.user)
    action = Column(String, nullable=False)  # e.g., "create", "update", "delete"
    entity = Column(String, nullable=False)  # e.g., "user", "property"
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    before_value = Column(JSON, nullable=True)
    after_value = Column(JSON, nullable=True)
    ip_address = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)