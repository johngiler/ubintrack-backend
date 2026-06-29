"""Compat: scripts y código que no tienen `request` (middleware)."""

from apps.workspaces.tenant import get_default_workspace_safely


def get_default_workspace():
    """Workspace por `DEFAULT_WORKSPACE_SLUG` o el primero activo (seeds, shell)."""
    return get_default_workspace_safely()
