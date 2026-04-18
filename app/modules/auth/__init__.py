"""Authentication module exports."""

from app.modules.auth.routes import admin_auth_router, auth_router, router
from app.modules.auth.service import get_current_user, require_role

__all__ = [
    "router",
    "auth_router",
    "admin_auth_router",
    "get_current_user",
    "require_role",
]
