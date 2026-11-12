from __future__ import annotations

from typing import Any, Iterable

from apps.audit.models import (
    ActivityBatch,
    ActivityBatchItem,
    ActivitySection,
    ViewActivityEntry,
)


def _actor_label(user) -> str:
    if user is None:
        return "Sistema"
    full = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    if full:
        return full
    return (
        getattr(user, "email", None)
        or getattr(user, "username", None)
        or "Usuario"
    )


def record_activity(
    *,
    workspace,
    section: str,
    action: str,
    summary: str,
    actor=None,
    resource_id: int | None = None,
    resource_code: str = "",
    affected_count: int = 1,
    changes: dict | None = None,
    batch: ActivityBatch | None = None,
) -> ViewActivityEntry | None:
    if workspace is None:
        return None
    return ViewActivityEntry.objects.create(
        workspace=workspace,
        section=section,
        action=action,
        summary=summary[:255],
        changes=changes or {},
        resource_id=resource_id,
        resource_code=(resource_code or "")[:64],
        affected_count=max(int(affected_count or 1), 1),
        batch=batch,
        actor=actor,
    )


def record_activity_batch(
    *,
    workspace,
    section: str,
    kind: str,
    summary: str,
    action: str,
    items: Iterable[dict[str, Any]],
    actor=None,
    changes: dict | None = None,
) -> tuple[ActivityBatch | None, ViewActivityEntry | None]:
    """Crea lote + ítems + una fila macro. items: {resource_id, resource_code, extra?}."""
    if workspace is None:
        return None, None

    item_list = list(items)
    batch = ActivityBatch.objects.create(
        workspace=workspace,
        section=section,
        kind=kind or ActivityBatch.Kind.OTHER,
        summary=summary[:255],
        item_count=len(item_list),
        actor=actor,
    )
    ActivityBatchItem.objects.bulk_create(
        [
            ActivityBatchItem(
                batch=batch,
                resource_id=row.get("resource_id"),
                resource_code=str(row.get("resource_code") or "")[:64],
                extra=row.get("extra") or {},
            )
            for row in item_list
        ]
    )
    entry = record_activity(
        workspace=workspace,
        section=section,
        action=action,
        summary=summary,
        actor=actor,
        affected_count=len(item_list) or 1,
        changes=changes or {},
        batch=batch,
    )
    return batch, entry


# Convenience aliases matching section constants
SECTION = ActivitySection
