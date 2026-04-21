"""One-shot backfill: copy local logos to the active storage backend.

Usage::

    # 1. Set STORAGE_BACKEND=s3 and S3_* env vars in your environment.
    # 2. Run from the project root:
    python -m app.scripts.backfill_logos_to_storage

The script walks every ``connectors`` row whose ``logo_url`` still points
at the local ``/media/...`` mount, uploads the bytes via the configured
storage backend, and rewrites ``logo_url`` to the new public URL. Idempotent:
re-running skips rows that already point at the active backend.

Designed to run *after* you've switched ``STORAGE_BACKEND`` and *before*
restarting the production app (so live traffic never sees a broken link).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from app.core.config import settings
from app.core.storage import LocalFilesystemStorage, get_storage
from app.db.models.connector import Connector
from app.db.session import SessionLocal

logger = logging.getLogger("backfill_logos")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    storage = get_storage()
    if isinstance(storage, LocalFilesystemStorage):
        logger.error(
            "Active backend is local; nothing to do. "
            "Set STORAGE_BACKEND=s3 (and S3_* vars) before running."
        )
        return 1

    local_root = Path(settings.media_root).resolve()
    legacy_prefix = (settings.media_url_prefix or "/media").rstrip("/") + "/"

    db = SessionLocal()
    moved = skipped = missing = 0
    try:
        rows = (
            db.query(Connector)
            .filter(Connector.logo_url.isnot(None))
            .filter(Connector.logo_url.like(f"{legacy_prefix}%"))
            .all()
        )
        logger.info("found %d local logos to migrate", len(rows))
        for row in rows:
            rel = row.logo_url[len(legacy_prefix):]
            src = (local_root / rel).resolve()
            try:
                src.relative_to(local_root)
            except ValueError:
                logger.warning("skip %s: path escapes media_root", row.id)
                skipped += 1
                continue
            if not src.is_file():
                logger.warning("skip %s: file missing at %s", row.id, src)
                missing += 1
                continue
            new_url = storage.save(key=rel, data=src.read_bytes())
            row.logo_url = new_url
            moved += 1
            logger.info("migrated %s -> %s", row.id, new_url)
        db.commit()
    finally:
        db.close()

    logger.info(
        "done: moved=%d missing=%d skipped=%d", moved, missing, skipped
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
