"""Finalize connectors table in a single migration.

Brings the connectors table to its final shape for the connector master
feature. Replaces the legacy schema with one that supports:

* Name-only catalog (drops the legacy ``connector_type`` enum + type).
* ``created_at`` / ``updated_at`` timestamps.
* ``logo_sha256`` for image-content de-duplication.
* ``is_active`` (already on the table) — controls visibility in the
  tenant-facing catalog. Toggled by /activate and /deactivate.
* ``is_deleted`` + ``deleted_at`` — soft-delete columns. When ``is_deleted``
  is true, the row is excluded from every query (admin and tenant) and
  ``is_active`` is forced false at the application layer.
* Partial unique indexes on ``LOWER(name)`` and ``logo_sha256``, scoped
  to non-deleted rows so a deleted connector's name / logo can be reused.

Revision ID: 008_connectors_finalize
Revises: 007_property_tenant_fk_restrict
Create Date: 2026-04-21 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "008_connectors_finalize"
down_revision: Union[str, None] = "007_property_tenant_fk_restrict"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NAME_INDEX = "connectors_name_lower_uniq"
_LOGO_INDEX = "connectors_logo_sha256_uniq"


def upgrade() -> None:
    with op.batch_alter_table("connectors") as batch:
        batch.drop_column("connector_type")
        batch.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            )
        )
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            )
        )
        batch.add_column(sa.Column("logo_sha256", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column(
                "is_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
        batch.add_column(
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "display_order",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )

    op.execute("DROP TYPE IF EXISTS connectortype")
    # Partial unique indexes: only enforced for live rows so a deleted
    # connector's name / logo can be reclaimed by a brand-new row.
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_NAME_INDEX} "
        "ON connectors (LOWER(name)) WHERE is_deleted = false"
    )
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_LOGO_INDEX} "
        "ON connectors (logo_sha256) WHERE is_deleted = false"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_LOGO_INDEX}")
    op.execute(f"DROP INDEX IF EXISTS {_NAME_INDEX}")
    op.execute(
        "CREATE TYPE connectortype AS ENUM ("
        "'google','yelp','facebook','tripadvisor')"
    )
    with op.batch_alter_table("connectors") as batch:
        batch.drop_column("display_order")
        batch.drop_column("deleted_at")
        batch.drop_column("is_deleted")
        batch.drop_column("logo_sha256")
        batch.drop_column("updated_at")
        batch.drop_column("created_at")
        batch.add_column(
            sa.Column(
                "connector_type",
                sa.Enum(
                    "google", "yelp", "facebook", "tripadvisor", name="connectortype"
                ),
                nullable=True,
            )
        )
