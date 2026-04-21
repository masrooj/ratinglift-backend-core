"""Tenant-facing connector catalog module."""
from app.modules.connectors.routes import tenant_connector_router

__all__ = ["tenant_connector_router"]
