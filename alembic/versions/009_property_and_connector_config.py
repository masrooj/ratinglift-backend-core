"""Property google_maps_url + property_connectors per-source config & base_url.

Bundled into a single revision:

* ``properties.google_maps_url`` — public Google Maps link per property
  so the tenant UI can render "View on Google Maps" / map embeds without
  reconstructing it from ``google_place_id``.
* ``property_connectors.config`` — free-form JSON blob for per-source
  settings supplied at activation time (e.g. Google Business Profile
  ``location_id``, Yelp ``business_id``). Each driver owns its own
  sub-schema; this column is intentionally untyped at the DB layer.
* ``property_connectors.base_url`` — optional API base URL the worker
  should hit for this binding (sandbox/regional endpoints without a code
  change). Drivers fall back to their built-in default when NULL.
* ``property_connectors`` indexes — ``ix_property_connectors_property_id``
  for the "list connectors for a property" query, and a unique constraint
  on ``(property_id, connector_id)`` to enforce the "one binding per
  connector per property" rule at the DB layer (closes the race window
  in :func:`activate_connector`).

Revision ID: 009_prop_conn_cfg
Revises: 008_connectors_finalize
Create Date: 2026-04-21 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "009_prop_conn_cfg"
down_revision: Union[str, None] = "008_connectors_finalize"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "properties",
        sa.Column("google_maps_url", sa.String(), nullable=True),
    )
    op.add_column(
        "property_connectors",
        sa.Column("config", sa.JSON(), nullable=True),
    )
    op.add_column(
        "property_connectors",
        sa.Column("base_url", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_property_connectors_property_id",
        "property_connectors",
        ["property_id"],
    )
    op.create_unique_constraint(
        "uq_property_connectors_property_connector",
        "property_connectors",
        ["property_id", "connector_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_property_connectors_property_connector",
        "property_connectors",
        type_="unique",
    )
    op.drop_index(
        "ix_property_connectors_property_id",
        table_name="property_connectors",
    )
    op.drop_column("property_connectors", "base_url")
    op.drop_column("property_connectors", "config")
    op.drop_column("properties", "google_maps_url")
