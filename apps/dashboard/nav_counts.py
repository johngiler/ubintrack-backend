"""Conteos ligeros para ítems del menú lateral (scoped por usuario y permisos)."""

from __future__ import annotations

from django.db.models import QuerySet

from apps.core import choices as core_choices
from apps.core.models import Client, CustomUser, Profile
from apps.core.utils.user_roles import (
    can_manage_as_administrator,
    get_user_client_id,
    is_client_user,
)
from apps.finance.models import Tariff
from apps.inventory.models import Bin, Product
from apps.order.models import Entry, Exit
from apps.storage.models import Cava, Position, Storage
from apps.task.models import Task, UserTaskTracking
from apps.workspaces.utils.tenant import get_workspace_for_request


def _can_see(*, user, admin_only=False, staff_only=False, django_perm: str | None = None) -> bool:
    if admin_only and not can_manage_as_administrator(user):
        return False
    if staff_only and is_client_user(user):
        return False
    if django_perm and not user.has_perm(django_perm):
        return False
    return True


def _scope_client_qs(qs: QuerySet, user, client_field: str = "client") -> QuerySet:
    if is_client_user(user):
        client_id = get_user_client_id(user)
        if not client_id:
            return qs.none()
        return qs.filter(**{f"{client_field}_id": client_id})
    return qs


def build_nav_counts(request) -> dict[str, int]:
    user = request.user
    workspace = get_workspace_for_request(request)
    if not workspace or not user or not user.is_authenticated:
        return {}

    counts: dict[str, int] = {}

    def put(href: str, total: int) -> None:
        if total > 0:
            counts[href] = total

    if _can_see(user=user, admin_only=True):
        put(
            "/core/clients",
            Client.objects.filter(workspace=workspace, is_active=True).count(),
        )
        put(
            "/core/users",
            CustomUser.objects.filter(workspace=workspace, is_active=True)
            .exclude(user_type=core_choices.UserType.CLIENT)
            .count(),
        )
        put(
            "/core/profiles",
            Profile.objects.filter(workspace=workspace, is_active=True).count(),
        )
        put(
            "/finance/tariffs",
            Tariff.objects.filter(workspace=workspace).count(),
        )

    if _can_see(user=user, django_perm="inventory.view_product"):
        qs = Product.objects.filter(client__workspace=workspace, is_active=True)
        put("/inventory/products", _scope_client_qs(qs, user).count())

    if _can_see(user=user, django_perm="inventory.view_bin"):
        qs = Bin.objects.filter(client__workspace=workspace, is_active=True)
        put("/inventory/bins", _scope_client_qs(qs, user).count())

    if _can_see(user=user, django_perm="order.view_entry"):
        qs = Entry.objects.filter(client__workspace=workspace, is_active=True)
        put("/order/entries", _scope_client_qs(qs, user).count())

    if _can_see(user=user, django_perm="order.view_exit"):
        qs = Exit.objects.filter(client__workspace=workspace, is_active=True)
        put("/order/exits", _scope_client_qs(qs, user).count())

    if _can_see(user=user, staff_only=True, django_perm="storage.view_storage"):
        put(
            "/storage/storages",
            Storage.objects.filter(workspace=workspace, is_active=True).count(),
        )

    if _can_see(user=user, staff_only=True, django_perm="storage.view_cava"):
        put(
            "/storage/cavas",
            Cava.objects.filter(storage__workspace=workspace, is_active=True).count(),
        )

    if _can_see(user=user, staff_only=True, django_perm="storage.view_position"):
        put(
            "/storage/positions",
            Position.objects.filter(
                cava__storage__workspace=workspace,
                cava__is_virtual=False,
                is_active=True,
            ).count(),
        )

    if _can_see(user=user, staff_only=True, django_perm="task.view_task"):
        put(
            "/tasks/tasks",
            Task.objects.filter(workspace=workspace, is_active=True).count(),
        )

    if not is_client_user(user) and (
        user.has_perm("task.view_usertasktracking") or user.has_perm("task.view_tasklog")
    ):
        put(
            "/tasks/trackings",
            UserTaskTracking.objects.filter(user__workspace=workspace, is_active=True).count(),
        )

    return counts
