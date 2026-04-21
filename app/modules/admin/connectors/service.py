"""Service layer for admin connector master operations.

Pure data access + invariants. Endpoints are responsible for audit logging
and transaction commit/rollback.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid  # noqa: F401  (kept for backwards-compat callers)
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.storage import get_storage
from app.db.models.connector import Connector

try:  # Pillow is required for raster image validation.
    from PIL import Image, UnidentifiedImageError
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    Image = None  # type: ignore[assignment]
    UnidentifiedImageError = Exception  # type: ignore[assignment,misc]

# Allow-list for connector logo uploads.
_ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "svg", "webp"}
_ALLOWED_LOGO_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/svg+xml": "svg",
    "image/webp": "webp",
}

_SLUG_MAX_LEN = 60

# PIL format -> safe extension. Used to verify upload metadata matches
# the actual decoded image, rather than trusting the client-supplied
# Content-Type alone.
_PIL_FORMAT_TO_EXT = {
    "PNG": "png",
    "JPEG": "jpg",
    "WEBP": "webp",
}


def _slugify(name: str) -> str:
    """Convert a connector display name into a filesystem-safe slug.

    Lowercase, ASCII-only, words joined by ``-``. Falls back to ``connector``
    when the input cannot produce any safe characters.
    """
    if not name:
        return "connector"
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    slug = slug[:_SLUG_MAX_LEN].strip("-")
    return slug or "connector"


def snapshot(connector: Connector) -> dict[str, Any]:
    """JSON-safe snapshot of a connector row for audit before/after values."""
    return {
        "id": str(connector.id),
        "name": connector.name,
        "logo_url": connector.logo_url,
        "logo_sha256": connector.logo_sha256,
        "is_active": bool(connector.is_active),
        "is_deleted": bool(getattr(connector, "is_deleted", False)),
        "deleted_at": connector.deleted_at.isoformat() if getattr(connector, "deleted_at", None) else None,
        "display_order": int(getattr(connector, "display_order", 0) or 0),
    }


def _alive(query):
    """Restrict a Connector query to non-soft-deleted rows."""
    return query.filter(Connector.is_deleted.is_(False))


def list_connectors(
    db: Session,
    *,
    is_active: bool | None = None,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Connector], int]:
    q = db.query(Connector)
    if not include_deleted:
        q = _alive(q)
    if is_active is not None:
        q = q.filter(Connector.is_active.is_(bool(is_active)))
    total = q.with_entities(func.count(Connector.id)).scalar() or 0
    rows = (
        q.order_by(Connector.display_order.asc(), Connector.name.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, int(total)


def get_connector_or_404(db: Session, connector_id: UUID) -> Connector:
    row = (
        _alive(db.query(Connector))
        .filter(Connector.id == connector_id)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found"
        )
    return row


def get_connector_including_deleted_or_404(
    db: Session, connector_id: UUID
) -> Connector:
    """Like ``get_connector_or_404`` but also returns soft-deleted rows.

    Used by the restore endpoint, which is the only path allowed to act on
    a deleted connector.
    """
    row = db.query(Connector).filter(Connector.id == connector_id).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found"
        )
    return row


def _assert_unique_name(db: Session, name: str, *, exclude_id: UUID | None = None) -> None:
    q = _alive(db.query(Connector)).filter(
        func.lower(Connector.name) == name.lower()
    )
    if exclude_id is not None:
        q = q.filter(Connector.id != exclude_id)
    if q.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A connector with this name already exists",
        )


def create_connector(
    db: Session,
    *,
    name: str,
    logo_url: str | None,
) -> Connector:
    _assert_unique_name(db, name)
    row = Connector(
        name=name.strip(),
        logo_url=logo_url,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def update_connector(
    db: Session,
    *,
    connector: Connector,
    name: str | None,
    logo_url: str | None,
    is_active: bool | None,
    display_order: int | None = None,
) -> Connector:
    if name is not None and name.strip() != connector.name:
        new_name = name.strip()
        _assert_unique_name(db, new_name, exclude_id=connector.id)
        connector.name = new_name
        _rename_logo_to_match_name(connector)
    if logo_url is not None:
        connector.logo_url = logo_url
    if is_active is not None:
        connector.is_active = bool(is_active)
    if display_order is not None:
        if display_order < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="display_order must be ≥ 0",
            )
        connector.display_order = int(display_order)
    db.flush()
    return connector


def reorder_connectors(
    db: Session, *, items: list[tuple[UUID, int]]
) -> list[Connector]:
    """Bulk-set ``display_order`` for the given connector ids.

    Atomic: either every id resolves and is updated, or 404 is raised and
    nothing changes (caller is expected to roll back the transaction).
    Soft-deleted rows are not reorderable (404).
    """
    if not items:
        return []
    ids = [pid for pid, _ in items]
    rows = (
        _alive(db.query(Connector))
        .filter(Connector.id.in_(ids))
        .all()
    )
    by_id = {row.id: row for row in rows}
    missing = [str(pid) for pid in ids if pid not in by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector(s) not found: {', '.join(missing)}",
        )
    for pid, order in items:
        if order < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="display_order must be ≥ 0",
            )
        by_id[pid].display_order = int(order)
    db.flush()
    return [by_id[pid] for pid, _ in items]


def soft_delete_connector(db: Session, *, connector: Connector) -> Connector:
    """Mark a connector as deleted and force it inactive.

    Refuses (409) when one or more properties currently have this connector
    attached, so we never orphan ``property_connectors`` rows. Tenants must
    detach first.

    Idempotent: deleting an already-deleted row is a no-op.
    """
    if connector.is_deleted:
        return connector

    # Lazy import to avoid pulling the model at module import time.
    from app.db.models.property_connector import PropertyConnector

    attached = (
        db.query(func.count(PropertyConnector.id))
        .filter(PropertyConnector.connector_id == connector.id)
        .scalar()
        or 0
    )
    if attached:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete connector: it is attached to {attached} "
                f"propert{'y' if attached == 1 else 'ies'}. "
                "Detach it from all properties before deleting."
            ),
        )

    from datetime import datetime, timezone

    connector.is_deleted = True
    connector.deleted_at = datetime.now(timezone.utc)
    connector.is_active = False
    db.flush()
    return connector


def restore_connector(db: Session, *, connector: Connector) -> Connector:
    """Undo a soft-delete. Leaves ``is_active`` untouched."""
    if not connector.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connector is not deleted",
        )
    # Re-check name + logo uniqueness before resurrecting, in case a new row
    # has claimed the name/logo while this one was deleted.
    _assert_unique_name(db, connector.name, exclude_id=connector.id)
    if connector.logo_sha256:
        _assert_unique_logo_hash(
            db, connector.logo_sha256, exclude_id=connector.id
        )
    connector.is_deleted = False
    connector.deleted_at = None
    db.flush()
    return connector


# --- logo upload helpers --------------------------------------------------


def _logo_key(slug: str, ext: str) -> str:
    return f"connectors/{slug}.{ext}"


def _is_logo_referenced_by_others(
    db: Session,
    *,
    connector_id: UUID,
    logo_url: str | None = None,
    logo_sha256: str | None = None,
) -> bool:
    """True if any *other* connector row points to the given logo.

    Used as a safety net before removing a file from storage: even though
    the partial unique index prevents two alive rows from sharing a hash,
    a soft-deleted row can still hold a reference and we don't want to
    break it.
    """
    conds = []
    if logo_url:
        conds.append(Connector.logo_url == logo_url)
    if logo_sha256:
        conds.append(Connector.logo_sha256 == logo_sha256)
    if not conds:
        return False
    return (
        db.query(Connector.id)
        .filter(Connector.id != connector_id)
        .filter(or_(*conds))
        .first()
        is not None
    )


def _safe_extension(filename: str | None, content_type: str | None) -> str:
    """Resolve a safe file extension from upload metadata.

    Prefers content-type allow-list; falls back to the filename suffix.
    Raises 415 for unsupported types.
    """
    if content_type:
        ext = _ALLOWED_LOGO_CONTENT_TYPES.get(content_type.lower())
        if ext:
            return ext
    if filename:
        suffix = Path(filename).suffix.lower().lstrip(".")
        if suffix in _ALLOWED_LOGO_EXTENSIONS:
            return "jpg" if suffix == "jpeg" else suffix
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="Unsupported logo type. Allowed: png, jpg, svg, webp.",
    )


def _delete_existing_logo(db: Session, connector: Connector) -> None:
    """Best-effort removal of a previously uploaded logo from storage.

    Skips deletion when:
      * the logo lives outside our storage (external URL), or
      * another connector row still references the same URL/hash.
    """
    if not connector.logo_url:
        return
    storage = get_storage()
    key = storage.key_from_url(connector.logo_url)
    if key is None:
        return  # external URL — never touch
    if _is_logo_referenced_by_others(
        db,
        connector_id=connector.id,
        logo_url=connector.logo_url,
        logo_sha256=connector.logo_sha256,
    ):
        return
    storage.delete(key)


def save_connector_logo(
    db: Session,
    *,
    connector: Connector,
    file_bytes: bytes,
    filename: str | None,
    content_type: str | None,
) -> Connector:
    """Persist an uploaded logo and update ``connector.logo_url``."""
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file"
        )
    if len(file_bytes) > settings.connector_logo_max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Logo too large (max {settings.connector_logo_max_bytes} bytes)"
            ),
        )

    ext = _safe_extension(filename, content_type)
    ext = _validate_image_bytes(file_bytes, ext)

    digest = hashlib.sha256(file_bytes).hexdigest()
    _assert_unique_logo_hash(db, digest, exclude_id=connector.id)

    slug = _slugify(connector.name)
    file_id = f"{slug}.{ext}"
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", file_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename"
        )

    storage = get_storage()
    key = _logo_key(slug, ext)
    _delete_existing_logo(db, connector)
    _purge_slug_siblings(db, connector, slug, keep=file_id)
    public_url = storage.save(key=key, data=file_bytes, content_type=content_type)

    connector.logo_url = public_url
    connector.logo_sha256 = digest
    db.flush()
    return connector


def clear_connector_logo(db: Session, *, connector: Connector) -> Connector:
    _delete_existing_logo(db, connector)
    connector.logo_url = None
    connector.logo_sha256 = None
    db.flush()
    return connector


def _assert_unique_logo_hash(
    db: Session, digest: str, *, exclude_id: UUID | None = None
) -> None:
    q = _alive(db.query(Connector)).filter(Connector.logo_sha256 == digest)
    if exclude_id is not None:
        q = q.filter(Connector.id != exclude_id)
    if q.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This image is already used by another connector",
        )


def _validate_image_bytes(file_bytes: bytes, declared_ext: str) -> str:
    """Verify the upload is a real image and within size/dimension limits.

    SVG is text-based (XML) and is allowed through with a minimal sniff so
    we don't pull in an XML parser dependency. Raster formats are decoded
    with Pillow; the actual decoded format must match ``declared_ext`` (or
    be a recognized equivalent), and pixel dimensions must respect the
    ``connector_logo_max_pixels`` setting.

    Returns the canonical extension to use on disk.
    """
    if declared_ext == "svg":
        # Quick smell-test: must contain an <svg> root tag somewhere in the
        # first kilobyte. Refuse anything else (including HTML, scripts, etc).
        head = file_bytes[:1024].lstrip().lower()
        if not (head.startswith(b"<?xml") or head.startswith(b"<svg")):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="File does not look like a valid SVG image",
            )
        if b"<svg" not in file_bytes[:4096].lower():
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="File does not look like a valid SVG image",
            )
        return "svg"

    if Image is None:  # pragma: no cover - dependency missing
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Image validation backend unavailable",
        )

    from io import BytesIO

    try:
        with Image.open(BytesIO(file_bytes)) as img:
            img.verify()  # detects truncated / malformed payloads
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File is not a valid image",
        )

    # ``verify`` consumes the file; reopen for size/format inspection.
    try:
        with Image.open(BytesIO(file_bytes)) as img:
            actual_ext = _PIL_FORMAT_TO_EXT.get((img.format or "").upper())
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File is not a valid image",
        )

    if actual_ext is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported image format",
        )
    if actual_ext != declared_ext:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Image content ({actual_ext}) does not match declared type "
                f"({declared_ext})"
            ),
        )

    max_px = settings.connector_logo_max_pixels
    if max_px and (width > max_px or height > max_px):
        raise HTTPException(
            status_code=413,
            detail=f"Image dimensions exceed {max_px}px (got {width}x{height})",
        )

    return actual_ext


def _purge_slug_siblings(
    db: Session, connector: Connector, slug: str, *, keep: str
) -> None:
    """Delete stale logo files for ``slug`` whose extension no longer matches.

    Without this, uploading ``foo.png`` after ``foo.svg`` would leave the
    old SVG orphaned in storage. Skips any sibling key still referenced by
    another connector row.
    """
    safe_slug = re.sub(r"[^A-Za-z0-9_\-]", "", slug)
    if not safe_slug:
        return
    storage = get_storage()
    for key in storage.list_prefix(f"connectors/{safe_slug}."):
        if key.endswith("/" + keep) or key == f"connectors/{keep}":
            continue
        sibling_url = storage.url_for(key)
        if _is_logo_referenced_by_others(
            db, connector_id=connector.id, logo_url=sibling_url
        ):
            continue
        storage.delete(key)


def _rename_logo_to_match_name(connector: Connector) -> None:
    """Rename a stored logo so its key matches the connector's new name.

    No-op when the connector has no logo, or the logo lives at an external
    URL. Best-effort: storage errors are swallowed and the DB pointer is
    left untouched in that case.
    """
    if not connector.logo_url:
        return
    storage = get_storage()
    src_key = storage.key_from_url(connector.logo_url)
    if src_key is None:
        return  # external URL — never touch
    ext = src_key.rsplit(".", 1)[-1].lower() if "." in src_key else ""
    if not ext:
        return
    dst_key = _logo_key(_slugify(connector.name), ext)
    if dst_key == src_key:
        return
    storage.move(src_key=src_key, dst_key=dst_key)
    connector.logo_url = storage.url_for(dst_key)