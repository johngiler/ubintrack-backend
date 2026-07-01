"""Helpers centralizados para roles de usuario WMS."""

from __future__ import annotations

from apps.core import choices


def is_workspace_administrator(user) -> bool:
    return getattr(user, "user_type", None) == choices.UserType.ADMINISTRATOR


def is_client_user(user) -> bool:
    return getattr(user, "user_type", None) == choices.UserType.CLIENT


def user_bypasses_permissions(user) -> bool:
    """Superuser (Django admin) o administrador del owner: sin restricción de permisos."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return is_workspace_administrator(user)


def can_manage_as_administrator(user) -> bool:
    """Acciones reservadas al administrador del owner (no empleado ni cliente)."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return is_workspace_administrator(user)


def get_user_client(user):
    """Cliente asociado al usuario tipo Cliente."""
    if user is None:
        return None
    client_id = getattr(user, "client_id", None)
    if client_id:
        return user.client
    return None


def get_user_client_id(user) -> int | None:
    client = get_user_client(user)
    return client.id if client else None
