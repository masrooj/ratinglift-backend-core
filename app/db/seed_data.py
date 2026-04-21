"""Seed data for the application.

Central catalog of records to create when bootstrapping an environment.
Values here can be overridden at runtime via environment variables so
real passwords never live in source control for production.

Rules:
- Every seeder must be idempotent (the orchestrator in ``app/db/seed.py``
  runs this on every startup).
- Keep secrets here ONLY for development defaults. Production MUST override
  via env vars (see ``_resolve_env`` below).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class AdminSeed:
    email: str
    password: str
    full_name: str
    role: str = "SUPER_ADMIN"


# ---------------------------------------------------------------------------
# Platform admins
# ---------------------------------------------------------------------------
# The first entry is the bootstrap SUPER_ADMIN. Additional admins can be
# added here and will be created on next startup.
#
# For production, set these env vars to override the dev defaults:
#   BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD, BOOTSTRAP_ADMIN_FULL_NAME
ADMINS: list[AdminSeed] = [
    AdminSeed(
        email=_env("BOOTSTRAP_ADMIN_EMAIL") or "",
        password=_env("BOOTSTRAP_ADMIN_PASSWORD") or "",
        full_name=_env("BOOTSTRAP_ADMIN_FULL_NAME") or "",
        role="SUPER_ADMIN",
    ),
]


# ---------------------------------------------------------------------------
# Reserved for future seed data (add as the app grows)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TenantSeed:
    name: str
    owner_email: str


DEMO_TENANTS: list[TenantSeed] = []  # add TenantSeed(...) entries to enable
