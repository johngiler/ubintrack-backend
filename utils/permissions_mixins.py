from typing import Any

from django.db.models import QuerySet
from rest_framework.permissions import DjangoModelPermissions, BasePermission

from apps.core.user_roles import (
    get_user_client_id,
    is_client_user,
    user_bypasses_permissions,
)
from apps.workspaces.tenant import get_workspace_for_request, is_platform_staff


class WorkspaceScopeMixin:
    """
    Filtra queryset al workspace resuelto en la petición.
    Superusuarios/staff de plataforma ven todo.
    """

    workspace_field: str = "workspace"

    def scope_to_workspace(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        request = getattr(self, "request", None)
        if not request:
            return queryset
        user = request.user
        if user and user.is_authenticated and is_platform_staff(user):
            return queryset
        ws = get_workspace_for_request(request)
        if ws is None:
            return queryset.none()
        field = getattr(self, "workspace_field", "workspace")
        return queryset.filter(**{field: ws})


class ClientScopeMixin(WorkspaceScopeMixin):
    """
    Limita queryset al cliente del usuario cuando el tipo es Cliente.
    Administradores y empleados ven todos los clientes del owner (con permisos).
    """

    client_field: str = "client"
    workspace_field: str | None = None

    def resolve_workspace_field(self) -> str:
        explicit = getattr(type(self), "workspace_field", None)
        if explicit is not None:
            return explicit
        client_field = getattr(self, "client_field", "client")
        if client_field == "client":
            return "client__workspace"
        return f"{client_field}__workspace"

    def scope_to_workspace(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        request = getattr(self, "request", None)
        if not request:
            return queryset
        user = request.user
        if user and user.is_authenticated and is_platform_staff(user):
            return queryset
        ws = get_workspace_for_request(request)
        if ws is None:
            return queryset.none()
        return queryset.filter(**{self.resolve_workspace_field(): ws})

    def scope_to_user(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        queryset = self.scope_to_workspace(queryset)
        user = getattr(self, "request", None) and self.request.user or None
        if not user:
            return queryset

        if is_platform_staff(user):
            return queryset

        if is_client_user(user):
            client_id = get_user_client_id(user)
            if client_id:
                field = getattr(self, "client_field", "client")
                return queryset.filter(**{f"{field}_id": client_id})

        return queryset


class StrictDjangoModelPermissions(DjangoModelPermissions):
    """Exige permiso view_* para GET. Administrador del owner omite permisos."""

    perms_map = {
        'GET': ['%(app_label)s.view_%(model_name)s'],
        'OPTIONS': [],
        'HEAD': [],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }

    def has_permission(self, request, view):
        if user_bypasses_permissions(getattr(request, "user", None)):
            return True
        return super().has_permission(request, view)


class DenyClientUsers(BasePermission):
    """Niega acceso a usuarios tipo Cliente."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if is_client_user(user) and not user.is_superuser:
            return False
        return True
