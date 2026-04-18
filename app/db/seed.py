"""Database seeding entry point.

Runs automatically on every application startup (see ``app/main.py``
lifespan) and can also be triggered manually:

    python -m app.db.seed

Every seeder MUST be idempotent — i.e. check if its records already
exist and do nothing if so. Add new seeders to ``SEEDERS`` below.
"""
from __future__ import annotations

import sys
from typing import Callable

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.modules.auth.bootstrap import seed_admins

logger = get_logger(__name__)

Seeder = Callable[[Session], object]

# Register new seeders here. Each one is called with a shared Session.
SEEDERS: list[tuple[str, Seeder]] = [
    ("admins", seed_admins),
]


def run_seeders(db: Session | None = None) -> None:
    """Run every registered seeder once. Idempotent by contract."""
    owns_session = db is None
    session = db or SessionLocal()
    try:
        for name, seeder in SEEDERS:
            try:
                seeder(session)
                logger.info("seeder_ok name=%s", name)
            except Exception as exc:  # pragma: no cover - best-effort
                logger.error("seeder_failed name=%s error=%s", name, exc)
    finally:
        if owns_session:
            session.close()


def main() -> int:
    run_seeders()
    print("Seeding complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
