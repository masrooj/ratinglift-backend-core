from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class Property(Base):
    __tablename__ = "properties"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = Column(String, nullable=False)
    google_place_id = Column(String, nullable=True)
    # Public Google Maps URL for this property (e.g. the share link). Stored
    # so the tenant UI can render a "View on Google Maps" link / embed
    # without having to construct it from the place_id every time.
    google_maps_url = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ux_properties_tenant_place",
            "tenant_id",
            "google_place_id",
            unique=True,
            postgresql_where=text("google_place_id IS NOT NULL"),
        ),
    )
