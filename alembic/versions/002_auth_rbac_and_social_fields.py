"""Add RBAC roles and social auth fields

Revision ID: 002_auth_rbac_and_social_fields
Revises: 001_initial
Create Date: 2026-04-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_auth_rbac_and_social_fields"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(), nullable=True))
    op.add_column("users", sa.Column("profile_picture_url", sa.String(), nullable=True))
    op.add_column("users", sa.Column("auth_provider", sa.String(), nullable=False, server_default="password"))
    op.add_column("users", sa.Column("oauth_subject", sa.String(), nullable=True))
    op.add_column("users", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))

    op.execute("ALTER TYPE userrole RENAME TO userrole_old")
    op.execute(
        """
        CREATE TYPE userrole AS ENUM (
            'OWNER',
            'MANAGER',
            'STAFF',
            'SUPER_ADMIN',
            'FINANCE_ADMIN',
            'SUPPORT_ADMIN',
            'OPS_ADMIN',
            'COMPLIANCE_ADMIN'
        )
        """
    )
    op.execute(
        """
        ALTER TABLE users
        ALTER COLUMN role TYPE userrole
        USING (
            CASE role::text
                WHEN 'admin' THEN 'SUPER_ADMIN'
                WHEN 'manager' THEN 'MANAGER'
                WHEN 'user' THEN 'STAFF'
                ELSE role::text
            END
        )::userrole
        """
    )
    op.execute("DROP TYPE userrole_old")

    op.alter_column("users", "auth_provider", server_default=None)
    op.alter_column("users", "failed_login_attempts", server_default=None)


def downgrade() -> None:
    op.execute("ALTER TYPE userrole RENAME TO userrole_new")
    op.execute("CREATE TYPE userrole AS ENUM ('admin', 'manager', 'user')")
    op.execute(
        """
        ALTER TABLE users
        ALTER COLUMN role TYPE userrole
        USING (
            CASE role::text
                WHEN 'SUPER_ADMIN' THEN 'admin'
                WHEN 'FINANCE_ADMIN' THEN 'admin'
                WHEN 'SUPPORT_ADMIN' THEN 'admin'
                WHEN 'OPS_ADMIN' THEN 'admin'
                WHEN 'COMPLIANCE_ADMIN' THEN 'admin'
                WHEN 'OWNER' THEN 'manager'
                WHEN 'MANAGER' THEN 'manager'
                WHEN 'STAFF' THEN 'user'
                ELSE 'user'
            END
        )::userrole
        """
    )
    op.execute("DROP TYPE userrole_new")

    op.drop_column("users", "failed_login_attempts")
    op.drop_column("users", "oauth_subject")
    op.drop_column("users", "auth_provider")
    op.drop_column("users", "profile_picture_url")
    op.drop_column("users", "full_name")
