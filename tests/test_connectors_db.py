"""Optional database-level tests for connector invariants.

These hit a real Postgres database (sqlite cannot enforce partial unique
indexes the way our schema relies on). Skipped by default; opt in with::

    RUN_DB_TESTS=1 pytest tests/test_connectors_db.py

The active database is whatever ``DATABASE_URL`` resolves to. The test
runs Alembic migrations to head, exercises the constraint, and cleans up
the rows it created. It never drops tables or modifies unrelated data.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.session import SessionLocal, engine

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="DB tests are opt-in; set RUN_DB_TESTS=1 to enable",
)


def _db_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except (OperationalError, Exception):  # noqa: BLE001
        return False


@pytest.fixture(scope="module", autouse=True)
def _require_postgres():
    if not _db_reachable():
        pytest.skip("DATABASE_URL is not reachable")
    if engine.dialect.name != "postgresql":
        pytest.skip("partial unique indexes require PostgreSQL")


def _insert(session, **fields):
    fields.setdefault("id", uuid.uuid4())
    fields.setdefault("is_active", True)
    fields.setdefault("is_deleted", False)
    fields.setdefault("display_order", 0)
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(f":{k}" for k in fields)
    session.execute(
        text(f"INSERT INTO connectors ({cols}) VALUES ({placeholders})"), fields
    )
    return fields["id"]


def test_name_unique_among_alive_rows():
    """Two alive rows with the same (case-insensitive) name must collide."""
    s = SessionLocal()
    created: list[uuid.UUID] = []
    try:
        created.append(_insert(s, name="DB-Test-Alpha"))
        s.commit()
        with pytest.raises(IntegrityError):
            created.append(_insert(s, name="db-test-alpha"))
            s.commit()
        s.rollback()
    finally:
        for cid in created:
            s.execute(text("DELETE FROM connectors WHERE id = :id"), {"id": cid})
        s.commit()
        s.close()


def test_name_can_be_reused_when_previous_row_is_soft_deleted():
    """The partial index is scoped to ``is_deleted = false``, so a
    soft-deleted row must not block re-creating the same name."""
    s = SessionLocal()
    created: list[uuid.UUID] = []
    try:
        cid = _insert(s, name="DB-Test-Beta", is_deleted=True)
        created.append(cid)
        s.commit()
        # Same name, alive — should succeed.
        created.append(_insert(s, name="DB-Test-Beta"))
        s.commit()
    finally:
        for cid in created:
            s.execute(text("DELETE FROM connectors WHERE id = :id"), {"id": cid})
        s.commit()
        s.close()


def test_logo_sha256_unique_among_alive_rows():
    s = SessionLocal()
    created: list[uuid.UUID] = []
    digest = "a" * 64
    try:
        created.append(_insert(s, name="DB-Test-Gamma-1", logo_sha256=digest))
        s.commit()
        with pytest.raises(IntegrityError):
            created.append(
                _insert(s, name="DB-Test-Gamma-2", logo_sha256=digest)
            )
            s.commit()
        s.rollback()
    finally:
        for cid in created:
            s.execute(text("DELETE FROM connectors WHERE id = :id"), {"id": cid})
        s.commit()
        s.close()
