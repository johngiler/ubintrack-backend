"""Helpers de alto nivel: resumen macro por vista (sin duplicar timeline de ítem)."""

from __future__ import annotations

from apps.audit.models import ActivityBatch, ActivitySection
from apps.audit.services.record import record_activity, record_activity_batch


def _actor_name(actor) -> str:
    from apps.core.utils.display_name import user_display_name

    return user_display_name(actor)


def log_unit(
    *,
    workspace,
    section: str,
    action: str,
    summary: str,
    actor=None,
    resource_id=None,
    resource_code: str = "",
    changes: dict | None = None,
):
    return record_activity(
        workspace=workspace,
        section=section,
        action=action,
        summary=summary,
        actor=actor,
        resource_id=resource_id,
        resource_code=resource_code,
        affected_count=1,
        changes=changes,
    )


def log_bulk(
    *,
    workspace,
    section: str,
    action: str,
    kind: str,
    summary: str,
    items: list[dict],
    actor=None,
    changes: dict | None = None,
):
    return record_activity_batch(
        workspace=workspace,
        section=section,
        kind=kind or ActivityBatch.Kind.OTHER,
        summary=summary,
        action=action,
        items=items,
        actor=actor,
        changes=changes,
    )


def workspace_from_client(client):
    return getattr(client, "workspace", None) if client else None


SECTION = ActivitySection
