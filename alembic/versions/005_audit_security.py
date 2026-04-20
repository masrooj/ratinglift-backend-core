"""Audit logs, login attempts, admin action logs and IP blocklist

Revision ID: 005_audit_security
Revises: 004_auth_extensions
Create Date: 2026-04-20 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "005_audit_security"
down_revision: Union[str, None] = "004_auth_extensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # login_attempts
    op.create_table(
        "login_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_login_attempts_email", "login_attempts", ["email"])
    op.create_index("ix_login_attempts_ip_address", "login_attempts", ["ip_address"])
    op.create_index(
        "ix_login_attempts_email_ts",
        "login_attempts",
        ["email", sa.text("timestamp DESC")],
    )
    op.create_index(
        "ix_login_attempts_ip_ts",
        "login_attempts",
        ["ip_address", sa.text("timestamp DESC")],
    )

    # admin_action_logs
    op.create_table(
        "admin_action_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_entity", sa.String(), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("before_value", sa.JSON(), nullable=True),
        sa.Column("after_value", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("request_path", sa.String(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_admin_action_logs_admin_id", "admin_action_logs", ["admin_id"])
    op.create_index("ix_admin_action_logs_action", "admin_action_logs", ["action"])
    op.create_index(
        "ix_admin_action_logs_target_tenant_id",
        "admin_action_logs",
        ["target_tenant_id"],
    )

    # ip_blocklist
    op.create_table(
        "ip_blocklist",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ip_address", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "blocked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("ip_address", name="uq_ip_blocklist_ip_address"),
    )
    op.create_index("ix_ip_blocklist_ip_address", "ip_blocklist", ["ip_address"])


def downgrade() -> None:
    op.drop_index("ix_ip_blocklist_ip_address", table_name="ip_blocklist")
    op.drop_table("ip_blocklist")

    op.drop_index("ix_admin_action_logs_target_tenant_id", table_name="admin_action_logs")
    op.drop_index("ix_admin_action_logs_action", table_name="admin_action_logs")
    op.drop_index("ix_admin_action_logs_admin_id", table_name="admin_action_logs")
    op.drop_table("admin_action_logs")

    op.drop_index("ix_login_attempts_ip_ts", table_name="login_attempts")
    op.drop_index("ix_login_attempts_email_ts", table_name="login_attempts")
    op.drop_index("ix_login_attempts_ip_address", table_name="login_attempts")
    op.drop_index("ix_login_attempts_email", table_name="login_attempts")
    op.drop_table("login_attempts")
