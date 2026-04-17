from sqlalchemy import Column, String, Boolean, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import enum

from app.db.base import Base


class ConnectorType(enum.Enum):
    google = "google"
    yelp = "yelp"
    facebook = "facebook"
    tripadvisor = "tripadvisor"


class Connector(Base):
    __tablename__ = "connectors"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name = Column(String, nullable=False)
    logo_url = Column(String, nullable=True)
    connector_type = Column(Enum(ConnectorType), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)