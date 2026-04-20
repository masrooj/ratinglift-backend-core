"""Reusable audit logging service.

Public API:
    from app.modules.audit import log_action, log_admin_action
"""
from app.modules.audit.service import log_action, log_admin_action

__all__ = ["log_action", "log_admin_action"]
