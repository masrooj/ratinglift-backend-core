from sqlalchemy import Column, String, DateTime, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import enum

from app.db.base import Base


class SubscriptionStatus(enum.Enum):
    active = "active"
    past_due = "past_due"
    canceled = "canceled"
    incomplete = "incomplete"


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    plan = Column(String, nullable=False)  # e.g., "starter", "growth", "pro"
    status = Column(Enum(SubscriptionStatus), nullable=False, default=SubscriptionStatus.active)
    stripe_customer_id = Column(String, nullable=False)
    current_period_end = Column(DateTime(timezone=True), nullable=False)