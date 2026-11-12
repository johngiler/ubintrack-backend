"""Helpers cortos para cablear CRUD macro en viewsets."""

from __future__ import annotations

from apps.audit.models import ActivitySection
from apps.audit.services.view_log import log_unit


def actor_label(user) -> str:
    from apps.core.utils.display_name import user_display_name

    return user_display_name(user)


def log_crud(
    *,
    workspace,
    section: str,
    action: str,
    verb: str,
    label: str,
    actor,
    resource_id=None,
    resource_code: str = "",
):
    name = actor_label(actor)
    return log_unit(
        workspace=workspace,
        section=section,
        action=action,
        summary=f"{name} {verb} {label}",
        actor=actor,
        resource_id=resource_id,
        resource_code=resource_code or "",
    )


SECTION = ActivitySection
