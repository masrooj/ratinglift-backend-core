from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, JSON, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class PropertyConnector(Base):
    __tablename__ = "property_connectors"
    __table_args__ = (
        # Enforce "one binding per (property, connector)" at the DB layer.
        # Closes the race window in service.activate_connector where two
        # concurrent POSTs could each pass the existence check and insert.
        UniqueConstraint(
            "property_id",
            "connector_id",
            name="uq_property_connectors_property_connector",
        ),
        Index("ix_property_connectors_property_id", "property_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    connector_id = Column(UUID(as_uuid=True), ForeignKey("connectors.id"), nullable=False)
    api_key = Column(String, nullable=False)
    api_secret = Column(String, nullable=True)
    scopes = Column(JSON, nullable=True)  # List of scopes
    # Per-source configuration blob (e.g. Google Business Profile location_id,
    # Yelp business_id). Free-form by design — each connector driver owns its
    # own schema.
    config = Column(JSON, nullable=True)
    # API base URL the worker should hit for this binding. Lets tenants point
    # at sandbox/regional endpoints without a code change. Drivers fall back
    # to their built-in default when this is NULL.
    base_url = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)