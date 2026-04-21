"""Seed default platform connectors.

Idempotent: only inserts a connector when no row with the same
case-insensitive name already exists.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models.connector import Connector

logger = get_logger(__name__)


@dataclass(frozen=True)
class _DefaultConnector:
    name: str


_DEFAULTS: list[_DefaultConnector] = [
    _DefaultConnector("Google Reviews"),
    _DefaultConnector("Yelp"),
    _DefaultConnector("TripAdvisor"),
    _DefaultConnector("Facebook"),
    _DefaultConnector("Instagram"),
    _DefaultConnector("DoorDash"),
    _DefaultConnector("Uber Eats"),
]


def seed_connectors(db: Session) -> list[Connector]:
    created: list[Connector] = []
    for entry in _DEFAULTS:
        existing = (
            db.query(Connector)
            .filter(func.lower(Connector.name) == entry.name.lower())
            .first()
        )
        if existing:
            continue
        row = Connector(
            name=entry.name,
            is_active=True,
        )
        db.add(row)
        db.flush()
        created.append(row)
        logger.info("connector_seeded name=%s", entry.name)
    if created:
        db.commit()
    return created
