from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

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


def notify_bin_created(*, bin_obj, actor=None) -> list[Notification]:
    workspace = getattr(bin_obj, "workspace", None)
    if workspace is None:
        workspace = _workspace_from_client(getattr(bin_obj, "client", None))
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
