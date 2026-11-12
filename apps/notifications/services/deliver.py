from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

from apps.core import choices
from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer
from apps.notifications.services.recipients import workspace_staff_recipients

logger = logging.getLogger(__name__)


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


def _choice_label(choice_enum, value: str) -> str:
    return dict(choice_enum.choices).get(value, value or "")


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def _push_notification(notification: Notification) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    payload = NotificationSerializer(notification).data
    try:
        async_to_sync(channel_layer.group_send)(
            f"notifications_{notification.recipient_id}",
            {"type": "notification.message", "payload": payload},
        )
    except Exception:
        logger.exception(
            "No se pudo empujar notificación %s al channel layer",
            notification.pk,
        )


def deliver_notification(
    *,
    workspace,
    recipient,
    event: str,
    target: str,
    message: str,
    actor_display: str = "",
    artifact: str = "",
    resource_id: int | None = None,
    resource_code: str = "",
    affected_count: int = 1,
) -> Notification:
    notification = Notification.objects.create(
        workspace=workspace,
        recipient=recipient,
        event=event,
        artifact=artifact,
        target=target,
        message=message,
        actor_display=actor_display,
        resource_id=resource_id,
        resource_code=resource_code or "",
        affected_count=max(int(affected_count), 1),
    )
    transaction.on_commit(lambda: _push_notification(notification))
    return notification


def broadcast_workspace_staff(
    *,
    workspace,
    event: str,
    target: str,
    message: str,
    actor=None,
    artifact: str = "",
    resource_id: int | None = None,
    resource_code: str = "",
    affected_count: int = 1,
) -> list[Notification]:
    if workspace is None:
        return []
    actor_display = _actor_label(actor)
    recipients = workspace_staff_recipients(
        workspace=workspace,
        exclude_user=actor,
    )
    created: list[Notification] = []
    for recipient in recipients:
        created.append(
            deliver_notification(
                workspace=workspace,
                recipient=recipient,
                event=event,
                target=target,
                message=message,
                actor_display=actor_display,
                artifact=artifact,
                resource_id=resource_id,
                resource_code=resource_code,
                affected_count=affected_count,
            )
        )
    return created


def mark_notification_read(notification: Notification) -> Notification:
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return notification


def _workspace_from_client(client):
    if client is None:
        return None
    return getattr(client, "workspace", None)


def _workspace_from_bin(bin_obj):
    workspace = getattr(bin_obj, "workspace", None)
    if workspace is not None:
        return workspace
    return _workspace_from_client(getattr(bin_obj, "client", None))


def _workspace_from_position(position):
    workspace = getattr(position, "workspace", None)
    if workspace is not None:
        return workspace
    cava = getattr(position, "cava", None)
    storage = getattr(cava, "storage", None) if cava else None
    return getattr(storage, "workspace", None)


# --- Entradas ---


def notify_entry_created(*, entry, actor=None) -> list[Notification]:
    workspace = _workspace_from_client(getattr(entry, "client", None))
    code = getattr(entry, "code", "") or str(entry.pk)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.CREATED,
        artifact=Notification.Artifact.ENTRY,
        target=Notification.Target.ENTRY_DETAIL,
        message=f"{actor_name} registró la entrada {code}",
        actor=actor,
        resource_id=entry.pk,
        resource_code=code,
    )


def notify_entry_updated(*, entry, actor=None) -> list[Notification]:
    workspace = _workspace_from_client(getattr(entry, "client", None))
    code = getattr(entry, "code", "") or str(entry.pk)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.UPDATED,
        artifact=Notification.Artifact.ENTRY,
        target=Notification.Target.ENTRY_DETAIL,
        message=f"{actor_name} actualizó la entrada {code}",
        actor=actor,
        resource_id=entry.pk,
        resource_code=code,
    )


def notify_entry_status(*, entry, new_status: str, actor=None) -> list[Notification]:
    workspace = _workspace_from_client(getattr(entry, "client", None))
    code = getattr(entry, "code", "") or str(entry.pk)
    label = _choice_label(choices.OrderStatus, new_status)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.STATUS,
        artifact=Notification.Artifact.ENTRY,
        target=Notification.Target.ENTRY_DETAIL,
        message=f"{actor_name} cambió la entrada {code} a {label}",
        actor=actor,
        resource_id=entry.pk,
        resource_code=code,
    )


def notify_entry_deleted(*, workspace, code: str, resource_id: int | None = None, actor=None) -> list[Notification]:
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.DELETED,
        artifact=Notification.Artifact.ENTRY,
        target=Notification.Target.ENTRIES_LIST,
        message=f"{actor_name} eliminó la entrada {code}",
        actor=actor,
        resource_id=resource_id,
        resource_code=code,
    )


# --- Salidas ---


def notify_exit_created(*, exit_order, actor=None) -> list[Notification]:
    workspace = _workspace_from_client(getattr(exit_order, "client", None))
    code = getattr(exit_order, "code", "") or str(exit_order.pk)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.CREATED,
        artifact=Notification.Artifact.EXIT,
        target=Notification.Target.EXIT_DETAIL,
        message=f"{actor_name} registró la salida {code}",
        actor=actor,
        resource_id=exit_order.pk,
        resource_code=code,
    )


def notify_exit_updated(*, exit_order, actor=None) -> list[Notification]:
    workspace = _workspace_from_client(getattr(exit_order, "client", None))
    code = getattr(exit_order, "code", "") or str(exit_order.pk)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.UPDATED,
        artifact=Notification.Artifact.EXIT,
        target=Notification.Target.EXIT_DETAIL,
        message=f"{actor_name} actualizó la salida {code}",
        actor=actor,
        resource_id=exit_order.pk,
        resource_code=code,
    )


def notify_exit_status(*, exit_order, new_status: str, actor=None) -> list[Notification]:
    workspace = _workspace_from_client(getattr(exit_order, "client", None))
    code = getattr(exit_order, "code", "") or str(exit_order.pk)
    label = _choice_label(choices.OrderStatus, new_status)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.STATUS,
        artifact=Notification.Artifact.EXIT,
        target=Notification.Target.EXIT_DETAIL,
        message=f"{actor_name} cambió la salida {code} a {label}",
        actor=actor,
        resource_id=exit_order.pk,
        resource_code=code,
    )


def notify_exit_deleted(*, workspace, code: str, resource_id: int | None = None, actor=None) -> list[Notification]:
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.DELETED,
        artifact=Notification.Artifact.EXIT,
        target=Notification.Target.EXITS_LIST,
        message=f"{actor_name} eliminó la salida {code}",
        actor=actor,
        resource_id=resource_id,
        resource_code=code,
    )


# --- Bins ---


def notify_bin_created(*, bin_obj, actor=None) -> list[Notification]:
    workspace = _workspace_from_bin(bin_obj)
    code = getattr(bin_obj, "code", "") or str(bin_obj.pk)
    entry = getattr(bin_obj, "entry", None)
    entry_code = getattr(entry, "code", "") if entry else ""
    actor_name = _actor_label(actor)
    if entry_code:
        message = f"{actor_name} creó el bin {code} (entrada {entry_code})"
    else:
        message = f"{actor_name} creó el bin {code}"
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.CREATED,
        artifact=Notification.Artifact.BIN,
        target=Notification.Target.BINS_LIST,
        message=message,
        actor=actor,
        resource_id=bin_obj.pk,
        resource_code=code,
    )


def notify_bin_updated(*, bin_obj, actor=None) -> list[Notification]:
    workspace = _workspace_from_bin(bin_obj)
    code = getattr(bin_obj, "code", "") or str(bin_obj.pk)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.UPDATED,
        artifact=Notification.Artifact.BIN,
        target=Notification.Target.BINS_LIST,
        message=f"{actor_name} actualizó el bin {code}",
        actor=actor,
        resource_id=bin_obj.pk,
        resource_code=code,
    )


def notify_bin_status(*, bin_obj, new_status: str, actor=None) -> list[Notification]:
    workspace = _workspace_from_bin(bin_obj)
    code = getattr(bin_obj, "code", "") or str(bin_obj.pk)
    label = _choice_label(choices.BinStatus, new_status)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.STATUS,
        artifact=Notification.Artifact.BIN,
        target=Notification.Target.BINS_LIST,
        message=f"{actor_name} cambió el bin {code} a {label}",
        actor=actor,
        resource_id=bin_obj.pk,
        resource_code=code,
    )


def notify_bin_deleted(*, workspace, code: str, resource_id: int | None = None, actor=None) -> list[Notification]:
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.DELETED,
        artifact=Notification.Artifact.BIN,
        target=Notification.Target.BINS_LIST,
        message=f"{actor_name} eliminó el bin {code}",
        actor=actor,
        resource_id=resource_id,
        resource_code=code,
    )


def notify_bins_bulk(
    *,
    workspace,
    event: str,
    message: str,
    count: int,
    actor=None,
) -> list[Notification]:
    if count <= 0 or workspace is None:
        return []
    return broadcast_workspace_staff(
        workspace=workspace,
        event=event,
        artifact=Notification.Artifact.BIN,
        target=Notification.Target.BINS_LIST,
        message=message,
        actor=actor,
        affected_count=count,
    )


# --- Posiciones (manual; nunca cascadas desde bins) ---


def notify_position_updated(*, position, actor=None) -> list[Notification]:
    workspace = _workspace_from_position(position)
    code = getattr(position, "code", "") or str(position.pk)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.UPDATED,
        artifact=Notification.Artifact.POSITION,
        target=Notification.Target.POSITION_DETAIL,
        message=f"{actor_name} actualizó la posición {code}",
        actor=actor,
        resource_id=position.pk,
        resource_code=code,
    )


def notify_position_status(*, position, new_status: str, actor=None) -> list[Notification]:
    workspace = _workspace_from_position(position)
    code = getattr(position, "code", "") or str(position.pk)
    label = _choice_label(choices.PositionStatus, new_status)
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.STATUS,
        artifact=Notification.Artifact.POSITION,
        target=Notification.Target.POSITION_DETAIL,
        message=f"{actor_name} cambió la posición {code} a {label}",
        actor=actor,
        resource_id=position.pk,
        resource_code=code,
    )


def notify_position_deleted(*, workspace, code: str, resource_id: int | None = None, actor=None) -> list[Notification]:
    actor_name = _actor_label(actor)
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.DELETED,
        artifact=Notification.Artifact.POSITION,
        target=Notification.Target.POSITIONS_LIST,
        message=f"{actor_name} eliminó la posición {code}",
        actor=actor,
        resource_id=resource_id,
        resource_code=code,
    )


def notify_positions_generated(*, cava, created_count: int, actor=None) -> list[Notification]:
    """Una sola notificación agrupada: N posiciones en cava X."""
    if created_count <= 0:
        return []
    storage = getattr(cava, "storage", None)
    workspace = getattr(storage, "workspace", None) if storage else None
    cava_name = (getattr(cava, "name", None) or getattr(cava, "code", None) or str(cava.pk))
    actor_name = _actor_label(actor)
    noun = _plural(created_count, "posición", "posiciones")
    return broadcast_workspace_staff(
        workspace=workspace,
        event=Notification.Event.CREATED,
        artifact=Notification.Artifact.POSITION,
        target=Notification.Target.POSITIONS_LIST,
        message=f"{actor_name} generó {created_count} {noun} en {cava_name}",
        actor=actor,
        resource_id=cava.pk,
        resource_code=str(cava_name)[:64],
        affected_count=created_count,
    )


def notify_positions_bulk(
    *,
    workspace,
    event: str,
    message: str,
    count: int,
    actor=None,
) -> list[Notification]:
    if count <= 0 or workspace is None:
        return []
    return broadcast_workspace_staff(
        workspace=workspace,
        event=event,
        artifact=Notification.Artifact.POSITION,
        target=Notification.Target.POSITIONS_LIST,
        message=message,
        actor=actor,
        affected_count=count,
    )


def notify_bulk_job_finished(job) -> list[Notification]:
    """Aviso agrupado + audit macro/lote al terminar un BulkOperationJob."""
    from apps.audit.models import ActivityBatch, ActivitySection
    from apps.audit.services.view_log import log_bulk
    from apps.common.models import BulkOperationJob

    if job is None or job.status != BulkOperationJob.Status.SUCCEEDED:
        return []
    result = job.result or {}
    actor = getattr(job, "created_by", None)
    workspace = job.workspace
    kind = job.kind
    items = result.get("items") or []

    if kind == BulkOperationJob.Kind.BIN_DELETE:
        count = int(result.get("deleted") or 0)
        noun = _plural(count, "bin", "bins")
        message = f"{_actor_label(actor)} eliminó {count} {noun}"
        if items:
            log_bulk(
                workspace=workspace,
                section=ActivitySection.BIN,
                action="bulk_delete",
                kind=ActivityBatch.Kind.BULK_DELETE,
                summary=message,
                items=items,
                actor=actor,
            )
        return notify_bins_bulk(
            workspace=workspace,
            event=Notification.Event.DELETED,
            message=message,
            count=count,
            actor=actor,
        )

    if kind == BulkOperationJob.Kind.POSITION_DELETE:
        count = int(result.get("deleted") or 0)
        noun = _plural(count, "posición", "posiciones")
        message = f"{_actor_label(actor)} eliminó {count} {noun}"
        if items:
            log_bulk(
                workspace=workspace,
                section=ActivitySection.POSITION,
                action="bulk_delete",
                kind=ActivityBatch.Kind.BULK_DELETE,
                summary=message,
                items=items,
                actor=actor,
            )
        return notify_positions_bulk(
            workspace=workspace,
            event=Notification.Event.DELETED,
            message=message,
            count=count,
            actor=actor,
        )

    if kind == BulkOperationJob.Kind.BIN_PRINT:
        count = int(result.get("printed") or 0)
        if count and items:
            message = f"{_actor_label(actor)} imprimió {count} etiquetas de bins"
            log_bulk(
                workspace=workspace,
                section=ActivitySection.BIN,
                action="bulk_print",
                kind=ActivityBatch.Kind.BULK_PRINT,
                summary=message,
                items=items,
                actor=actor,
            )
        return []

    if kind == BulkOperationJob.Kind.POSITION_PRINT:
        count = int(result.get("printed") or 0)
        if count and items:
            message = f"{_actor_label(actor)} imprimió {count} etiquetas de posiciones"
            log_bulk(
                workspace=workspace,
                section=ActivitySection.POSITION,
                action="bulk_print",
                kind=ActivityBatch.Kind.BULK_PRINT,
                summary=message,
                items=items,
                actor=actor,
            )
        return []

    if kind == BulkOperationJob.Kind.BIN_REGENERATE_TAGS:
        count = int(result.get("regenerated") or 0)
        if count and items:
            message = f"{_actor_label(actor)} regeneró {count} etiquetas de bins"
            log_bulk(
                workspace=workspace,
                section=ActivitySection.BIN,
                action="bulk_regenerate",
                kind=ActivityBatch.Kind.BULK_REGENERATE,
                summary=message,
                items=items,
                actor=actor,
            )
        return []

    if kind == BulkOperationJob.Kind.POSITION_REGENERATE_TAGS:
        count = int(result.get("regenerated") or 0)
        if count and items:
            message = f"{_actor_label(actor)} regeneró {count} etiquetas de posiciones"
            log_bulk(
                workspace=workspace,
                section=ActivitySection.POSITION,
                action="bulk_regenerate",
                kind=ActivityBatch.Kind.BULK_REGENERATE,
                summary=message,
                items=items,
                actor=actor,
            )
        return []

    return []
