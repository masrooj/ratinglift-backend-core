from uuid import uuid4
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        tenant_id = request.headers.get("X-Tenant-ID", "anonymous")

        request.state.request_id = request_id
        request.state.tenant_id = tenant_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Tenant-ID"] = tenant_id
        return response
