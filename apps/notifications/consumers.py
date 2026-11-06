from __future__ import annotations

import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.notifications.services.frontend_build import (
    APP_UPDATES_GROUP,
    get_frontend_build_id,
)
from apps.notifications.services.presence import (
    presence_group,
    register_presence,
    unregister_presence,
    update_presence_status,
    workspace_id_for_connection,
)

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    user_group: str
    updates_group: str
    presence_group_name: str
    presence_user_id: int
    presence_workspace_id: int

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        self.user_group = f"notifications_{user.pk}"
        self.updates_group = APP_UPDATES_GROUP
        self.presence_user_id = int(user.pk)
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.channel_layer.group_add(self.updates_group, self.channel_name)

        workspace_id = await database_sync_to_async(workspace_id_for_connection)(user, self.scope)
        build_id = await database_sync_to_async(get_frontend_build_id)()
        online_users = []
        is_first = False
        if workspace_id:
            self.presence_workspace_id = int(workspace_id)
            self.presence_group_name = presence_group(self.presence_workspace_id)
            await self.channel_layer.group_add(self.presence_group_name, self.channel_name)
            is_first, online_users = await database_sync_to_async(register_presence)(
                user, self.channel_name, self.presence_workspace_id
            )

        await self.accept()

        if build_id:
            await self._send_json_safe(
                {"type": "app_update", "payload": {"build_id": build_id}}
            )
        if workspace_id:
            await self._send_json_safe(
                {
                    "type": "presence",
                    "payload": {"action": "snapshot", "users": online_users},
                }
            )
            if is_first:
                await self.channel_layer.group_send(
                    self.presence_group_name,
                    {
                        "type": "presence.message",
                        "payload": {"action": "join", "users": online_users},
                    },
                )

    async def disconnect(self, close_code):
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)
        if hasattr(self, "updates_group"):
            await self.channel_layer.group_discard(self.updates_group, self.channel_name)
        if hasattr(self, "presence_group_name"):
            await self.channel_layer.group_discard(self.presence_group_name, self.channel_name)
        if hasattr(self, "presence_user_id") and hasattr(self, "presence_workspace_id"):
            went_offline, online_users = await database_sync_to_async(unregister_presence)(
                self.presence_user_id,
                self.channel_name,
                self.presence_workspace_id,
            )
            action = "leave" if went_offline else "update"
            await self.channel_layer.group_send(
                presence_group(self.presence_workspace_id),
                {
                    "type": "presence.message",
                    "payload": {"action": action, "users": online_users},
                },
            )

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            return
        if content.get("type") != "presence_status":
            return
        if not hasattr(self, "presence_workspace_id"):
            return
        changed, online_users = await database_sync_to_async(update_presence_status)(
            self.presence_user_id,
            self.channel_name,
            str(content.get("status") or ""),
            self.presence_workspace_id,
        )
        if not changed:
            return
        await self.channel_layer.group_send(
            self.presence_group_name,
            {
                "type": "presence.message",
                "payload": {"action": "update", "users": online_users},
            },
        )

    async def notification_message(self, event):
        payload = event.get("payload") or {}
        await self._send_json_safe({"type": "notification", "payload": payload})

    async def app_update_message(self, event):
        payload = event.get("payload") or {}
        await self._send_json_safe({"type": "app_update", "payload": payload})

    async def presence_message(self, event):
        payload = event.get("payload") or {}
        await self._send_json_safe({"type": "presence", "payload": payload})

    async def _send_json_safe(self, data: dict) -> None:
        try:
            await self.send(text_data=json.dumps(data))
        except Exception:
            logger.debug(
                "WebSocket send skipped; connection already closed",
                exc_info=True,
            )
