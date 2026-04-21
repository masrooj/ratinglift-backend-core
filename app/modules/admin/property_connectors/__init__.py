"""Super-admin cross-tenant view of property → connector bindings."""
from app.modules.admin.property_connectors.routes import (
    admin_property_connector_router,
)

__all__ = ["admin_property_connector_router"]
