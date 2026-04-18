"""Add email verification, account lockout, TOTP, refresh token fields

Revision ID: 004_auth_extensions
Revises: 003_mfa_channels
Create Date: 2026-04-17 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004_auth_extensions"
down_revision: Union[str, None] = "003_mfa_channels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("totp_secret", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column("totp_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("users", "email_verified", server_default=None)
    op.alter_column("users", "totp_verified", server_default=None)

    # login_sessions
    op.add_column("login_sessions", sa.Column("jti", sa.String(), nullable=True))
    op.create_unique_constraint("uq_login_sessions_jti", "login_sessions", ["jti"])
    op.create_index("ix_login_sessions_jti", "login_sessions", ["jti"])
    op.add_column("login_sessions", sa.Column("refresh_token_hash", sa.String(), nullable=True))
    op.create_index(
        "ix_login_sessions_refresh_token_hash",
        "login_sessions",
        ["refresh_token_hash"],
    )
    op.add_column(
        "login_sessions",
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "login_sessions",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("login_sessions", "last_used_at")
    op.drop_column("login_sessions", "refresh_expires_at")
    op.drop_index("ix_login_sessions_refresh_token_hash", table_name="login_sessions")
    op.drop_column("login_sessions", "refresh_token_hash")
    op.drop_index("ix_login_sessions_jti", table_name="login_sessions")
    op.drop_constraint("uq_login_sessions_jti", "login_sessions", type_="unique")
    op.drop_column("login_sessions", "jti")

    op.drop_column("users", "totp_verified")
    op.drop_column("users", "totp_secret")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "email_verified")
