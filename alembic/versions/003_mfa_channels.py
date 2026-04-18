"""Add MFA channel fields (email/phone + verification flags)

Revision ID: 003_mfa_channels
Revises: 002_auth_rbac_and_social_fields
Create Date: 2026-04-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "003_mfa_channels"
down_revision: Union[str, None] = "002_auth_rbac_and_social_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_email", sa.String(), nullable=True))
    op.add_column("users", sa.Column("mfa_phone", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column("mfa_email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("mfa_phone_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("users", "mfa_email_verified", server_default=None)
    op.alter_column("users", "mfa_phone_verified", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "mfa_phone_verified")
    op.drop_column("users", "mfa_email_verified")
    op.drop_column("users", "mfa_phone")
    op.drop_column("users", "mfa_email")
