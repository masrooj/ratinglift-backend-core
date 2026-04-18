"""HTTP middleware for request tracing and tenant-context injection.

Two middlewares are exposed:

* ``RequestContextMiddleware`` — assigns a request id and echoes the (legacy)
  ``X-Tenant-ID`` header back to the client. Kept for backwards compatibility
  with existing logging.
* ``TenantContextMiddleware`` — extracts the JWT from the ``Authorization``
  header, decodes it, and injects ``user_id``, ``tenant_id``, ``role`` and
  ``is_admin`` onto ``request.state``. It also performs coarse path-based
  isolation between tenant routes and ``/api/v1/admin/*`` routes so that
  violations are rejected and logged before any route handler runs.
"""
from __future__ import annotations

from typing import Iterable
from uuid import uuid4

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

ADMIN_PATH_PREFIX = "/api/v1/admin"
TENANT_PATH_PREFIX = "/api/v1"

# Paths that are reachable without authentication. The tenant-context
# middleware will not enforce JWT presence on these.
PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth",
    "/api/v1/admin/auth",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
    "/ready",
    "/live",
)

_PUBLIC_EXACT_PATHS = {"/", "/docs", "/redoc", "/openapi.json", "/health", "/ready", "/live"}


def _decode_jwt(token: str) -> dict | None:
    """Decode a JWT against either the user or admin signing secret.

    Returns the decoded payload on success, ``None`` if the token is invalid
    or expired. Decoding never raises — auth dependencies are responsible for
    rejecting requests when context is required.
    """
    for secret in (settings.jwt_secret, settings.admin_jwt_secret):
        try:
            return jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
        except ExpiredSignatureError:
            return None
        except InvalidTokenError:
            continue
    return None


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id and echoes the legacy tenant header."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        legacy_tenant_header = request.headers.get("X-Tenant-ID", "anonymous")

        request.state.request_id = request_id
        # Only set the legacy tenant id if ``TenantContextMiddleware`` hasn't
        # already populated it from a JWT. Avoids clobbering authenticated
        # context with the anonymous header default.
        if not getattr(request.state, "tenant_id", None) or request.state.tenant_id == "anonymous":
            request.state.tenant_id = legacy_tenant_header

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        # Echo the canonical tenant id (token-derived if available, else
        # whatever the header had).
        response.headers["X-Tenant-ID"] = str(getattr(request.state, "tenant_id", legacy_tenant_header))
        return response


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Decode the bearer JWT and enforce coarse admin/tenant path isolation.

    Fine-grained enforcement (route-level, query-level) lives in
    ``app.core.dependencies``. This middleware handles only the cross-cutting
    rules:

    * Non-admin users cannot reach ``/api/v1/admin/*``.
    * Admin users that hit a tenant route must provide ``?tenant_id=`` in the
      query string (so they explicitly opt-in to a tenant scope).
    """

    def __init__(
        self,
        app,
        public_paths: Iterable[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._public_paths = tuple(public_paths) if public_paths else PUBLIC_PATH_PREFIXES

    def _is_public(self, path: str) -> bool:
        if path in _PUBLIC_EXACT_PATHS:
            return True
        return any(path.startswith(prefix) for prefix in self._public_paths)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Reset auth state on every request — never trust previous values.
        request.state.user_id = None
        request.state.role = None
        request.state.is_admin = False
        request.state.jwt_payload = None
        request.state.tenant_id_from_token = None

        token = _extract_bearer(request)
        if token:
            payload = _decode_jwt(token)
            if payload is not None:
                request.state.user_id = payload.get("user_id")
                request.state.role = payload.get("role")
                request.state.is_admin = bool(payload.get("is_admin"))
                request.state.tenant_id_from_token = payload.get("tenant_id")
                request.state.jwt_payload = payload
                if payload.get("tenant_id"):
                    request.state.tenant_id = payload["tenant_id"]

        if self._is_public(path):
            return await call_next(request)

        is_admin_path = path.startswith(ADMIN_PATH_PREFIX)
        is_tenant_path = path.startswith(TENANT_PATH_PREFIX) and not is_admin_path

        if not request.state.user_id:
            logger.warning(
                "tenant_isolation_violation reason=missing_token path=%s",
                path,
            )
            return JSONResponse({"detail": "Authentication required"}, status_code=401)

        if is_admin_path and not request.state.is_admin:
            logger.warning(
                "tenant_isolation_violation reason=non_admin_on_admin_route "
                "user_id=%s tenant_id=%s path=%s",
                request.state.user_id,
                request.state.tenant_id_from_token,
                path,
            )
            return JSONResponse({"detail": "Admin privileges required"}, status_code=403)

        if is_tenant_path and request.state.is_admin:
            override = request.query_params.get("tenant_id")
            if not override:
                logger.warning(
                    "tenant_isolation_violation reason=admin_missing_tenant_param "
                    "user_id=%s path=%s",
                    request.state.user_id,
                    path,
                )
                return JSONResponse(
                    {
                        "detail": (
                            "Admin must specify ?tenant_id=<uuid> when "
                            "calling tenant routes"
                        )
                    },
                    status_code=403,
                )
            request.state.tenant_id = override
            request.state.tenant_id_from_token = override

        return await call_next(request)
