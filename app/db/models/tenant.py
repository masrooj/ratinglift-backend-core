from sqlalchemy import Column, String, DateTime, Boolean, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import enum

from app.db.base import Base


class PlanType(enum.Enum):
    starter = "starter"
    growth = "growth"
    pro = "pro"


class TenantStatus(enum.Enum):
    active = "active"
    suspended = "suspended"
    cancelled = "cancelled"


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name = Column(String, nullable=False, unique=True)
    plan = Column(Enum(PlanType), nullable=False, default=PlanType.starter)
    status = Column(Enum(TenantStatus), nullable=False, default=TenantStatus.active)
    trial_end = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)