"""Seed admin users defined in ``app.db.seed_data.ADMINS``.

Idempotent: for each entry, creates the admin if missing, otherwise
leaves the existing row alone (never overwrites passwords).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.security import get_password_hash
from app.db.models.audit_log import ActorType, AuditLog
from app.db.models.user import User, UserRole
from app.db.seed_data import ADMINS, AdminSeed

logger = get_logger(__name__)

_ADMIN_ROLE_VALUES = {
    UserRole.SUPER_ADMIN.value,
    UserRole.FINANCE_ADMIN.value,
    UserRole.SUPPORT_ADMIN.value,
    UserRole.OPS_ADMIN.value,
    UserRole.COMPLIANCE_ADMIN.value,
}


def _seed_one(db: Session, entry: AdminSeed) -> User | None:
    email = entry.email.strip().lower()
    if not email or not entry.password:
        logger.info("admin_seed_skipped reason=missing_email_or_password")
        return None

    if entry.role not in _ADMIN_ROLE_VALUES:
        logger.warning("admin_seed_invalid_role email=%s role=%s", email, entry.role)
        return None

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        logger.info("admin_seed_already_present email=%s", email)
        return existing

    admin = User(
        email=email,
        full_name=entry.full_name,
        password_hash=get_password_hash(entry.password),
        role=UserRole(entry.role),
        tenant_id=None,
        is_admin=True,
        auth_provider="password",
    )
    db.add(admin)
    db.flush()

    db.add(
        AuditLog(
            actor_id=None,
            actor_type=ActorType.system,
            action="admin_seeded",
            entity="user",
            entity_id=admin.id,
            after_value={"email": email, "role": entry.role},
        )
    )
    db.commit()
    db.refresh(admin)
    logger.info("admin_seed_created email=%s role=%s", email, entry.role)
    return admin


def seed_admins(db: Session) -> list[User]:
    """Seed each admin in ``ADMINS``. Skips entries that already exist.

    Deduplicates the input list by (normalized) email so accidental
    duplicates in ``seed_data.ADMINS`` never create multiple rows.
    """
    seen: set[str] = set()
    created: list[User] = []
    for entry in ADMINS:
        key = entry.email.strip().lower()
        if key in seen:
            logger.info("admin_seed_duplicate_entry_skipped email=%s", key)
            continue
        seen.add(key)

        user = _seed_one(db, entry)
        if user is not None:
            created.append(user)
    return created
