from fastapi import Request


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def get_tenant_id(request: Request) -> str:
    return getattr(request.state, "tenant_id", "anonymous")
