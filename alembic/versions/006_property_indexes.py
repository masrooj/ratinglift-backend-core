"""Property updated_at, tenant index, and unique google_place_id per tenant.

Revision ID: 006_property_indexes
Revises: 005_audit_security
Create Date: 2026-04-20 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "006_property_indexes"
down_revision: Union[str, None] = "005_audit_security"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "properties",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_properties_tenant_id", "properties", ["tenant_id"])
    op.create_index(
        "ux_properties_tenant_place",
        "properties",
        ["tenant_id", "google_place_id"],
        unique=True,
        postgresql_where=sa.text("google_place_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_properties_tenant_place", table_name="properties")
    op.drop_index("ix_properties_tenant_id", table_name="properties")
    op.drop_column("properties", "updated_at")
