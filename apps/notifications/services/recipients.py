from __future__ import annotations

from django.contrib.auth import get_user_model

from apps.core.choices import UserType


def workspace_staff_recipients(*, workspace, exclude_user=None):
    """Staff del workspace (admin + empleado), sin usuarios cliente."""
    if workspace is None:
        return get_user_model().objects.none()

    User = get_user_model()
    qs = User.objects.filter(
        is_active=True,
        workspace_id=workspace.id,
        user_type__in=(UserType.ADMINISTRATOR, UserType.EMPLOYEE),
    ).exclude(user_type=UserType.CLIENT)

    if exclude_user is not None:
        qs = qs.exclude(pk=getattr(exclude_user, "pk", exclude_user))
    return qs
