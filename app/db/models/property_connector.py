from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class PropertyConnector(Base):
    __tablename__ = "property_connectors"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    connector_id = Column(UUID(as_uuid=True), ForeignKey("connectors.id"), nullable=False)
    api_key = Column(String, nullable=False)
    api_secret = Column(String, nullable=True)
    scopes = Column(JSON, nullable=True)  # List of scopes
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)