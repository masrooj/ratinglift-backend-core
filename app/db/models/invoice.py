from sqlalchemy import Column, String, DateTime, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.db.base import Base


class InvoiceStatus(enum.Enum):
    draft = "draft"
    open = "open"
    paid = "paid"
    void = "void"
    uncollectible = "uncollectible"


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    stripe_invoice_id = Column(String, nullable=False, unique=True)
    amount = Column(Integer, nullable=False)  # Amount in cents
    status = Column(Enum(InvoiceStatus), nullable=False, default=InvoiceStatus.draft)
    issued_at = Column(DateTime(timezone=True), nullable=False)