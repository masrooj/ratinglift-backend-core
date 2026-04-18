from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Query, Session
from pymongo.database import Database
import redis

from app.core.logging import get_logger
from app.db.session import get_db
from app.db.mongo import get_mongo_db
from app.db.redis import get_redis_client

logger = get_logger(__name__)


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def get_tenant_id(request: Request) -> str:
    """Legacy accessor — returns the canonical tenant id on ``request.state``.

    Prefer :func:`get_current_context` in new code.
    """
    return getattr(request.state, "tenant_id", "anonymous")


def get_database_session() -> Session:
    """Dependency to get SQLAlchemy database session."""
    return next(get_db())


def get_mongo_database() -> Database:
    """Dependency to get MongoDB database."""
    return get_mongo_db()


def get_redis() -> redis.Redis:
    """Dependency to get Redis client."""
    return get_redis_client()


# ---------------------------------------------------------------------------
# Multi-tenant request context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestContext:
    """Authenticated request context derived from the bearer JWT.

    Populated by :class:`app.core.middleware.TenantContextMiddleware` and
    materialised by :func:`get_current_context`.
    """

    user_id: str
    tenant_id: str | None
    role: str | None
    is_admin: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "is_admin": self.is_admin,
        }


def get_current_context(request: Request) -> RequestContext:
    """Return the authenticated context for the current request.

    Raises ``401`` when no valid JWT was decoded by the middleware.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    return RequestContext(
        user_id=str(user_id),
        tenant_id=(
            str(getattr(request.state, "tenant_id_from_token", None))
            if getattr(request.state, "tenant_id_from_token", None)
            else None
        ),
        role=getattr(request.state, "role", None),
        is_admin=bool(getattr(request.state, "is_admin", False)),
    )


def require_tenant_context(request: Request) -> RequestContext:
    """Dependency for tenant-scoped routes.

    Behaviour:

    * Regular users — must carry a JWT containing ``tenant_id``; otherwise the
      request is rejected with 403.
    * Admin users — may impersonate any tenant by passing ``?tenant_id=`` in
      the query string (already validated by the middleware). The returned
      context will reflect that override.
    """
    ctx = get_current_context(request)

    if ctx.is_admin:
        override = request.query_params.get("tenant_id")
        if not override:
            logger.warning(
                "tenant_isolation_violation reason=admin_missing_tenant_param "
                "user_id=%s path=%s",
                ctx.user_id,
                request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin must specify tenant_id query parameter for tenant routes",
            )
        return RequestContext(
            user_id=ctx.user_id,
            tenant_id=override,
            role=ctx.role,
            is_admin=True,
        )

    if not ctx.tenant_id:
        logger.warning(
            "tenant_isolation_violation reason=missing_tenant_id user_id=%s path=%s",
            ctx.user_id,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id is required",
        )

    return ctx


def require_admin_context(request: Request) -> RequestContext:
    """Dependency for admin routes — rejects non-admin callers."""
    ctx = get_current_context(request)
    if not ctx.is_admin:
        logger.warning(
            "tenant_isolation_violation reason=non_admin_on_admin_route "
            "user_id=%s path=%s",
            ctx.user_id,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return ctx


def assert_same_tenant(ctx: RequestContext, resource_tenant_id: str | None) -> None:
    """Raise 403 if ``resource_tenant_id`` does not belong to ``ctx``.

    Admins are allowed through (they have already opted-in to a tenant via
    the query parameter, which is reflected in ``ctx.tenant_id``).
    """
    if ctx.is_admin and ctx.tenant_id and resource_tenant_id and str(resource_tenant_id) == str(ctx.tenant_id):
        return
    if ctx.is_admin and not ctx.tenant_id:
        # Pure cross-tenant admin (no override) — allow.
        return
    if str(resource_tenant_id) != str(ctx.tenant_id):
        logger.warning(
            "tenant_isolation_violation reason=cross_tenant_access "
            "user_id=%s ctx_tenant=%s resource_tenant=%s",
            ctx.user_id,
            ctx.tenant_id,
            resource_tenant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant access denied",
        )


def filter_by_tenant(query: Query, model: Any, tenant_id: str | None) -> Query:
    """Apply the canonical ``WHERE tenant_id = :tenant_id`` filter.

    Repositories MUST funnel every tenant-scoped query through this helper so
    cross-tenant leakage is impossible by construction.
    """
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id is required",
        )
    if not hasattr(model, "tenant_id"):
        raise ValueError(
            f"Model {getattr(model, '__name__', model)!r} has no tenant_id column"
        )
    return query.filter(model.tenant_id == tenant_id)
