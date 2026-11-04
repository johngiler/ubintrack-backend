from __future__ import annotations

import json
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    user_group: str

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        self.user_group = f"notifications_{user.pk}"
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)

    async def notification_message(self, event):
        payload = event.get("payload") or {}
        await self._send_json_safe({"type": "notification", "payload": payload})

    async def _send_json_safe(self, data: dict) -> None:
        try:
            await self.send(text_data=json.dumps(data))
        except Exception:
            logger.debug(
                "WebSocket send skipped; connection already closed",
                exc_info=True,
            )
