from rest_framework import serializers

from apps.notifications.models import Notification
from apps.notifications.utils import resolve_actor_display, strip_leading_actor


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()
    actor_display = serializers.SerializerMethodField()
    message = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "event",
            "artifact",
            "target",
            "message",
            "actor_display",
            "resource_id",
            "resource_code",
            "affected_count",
            "read_at",
            "is_read",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_read(self, obj) -> bool:
        return obj.read_at is not None

    def get_actor_display(self, obj) -> str:
        return resolve_actor_display(obj.actor_display)

    def get_message(self, obj) -> str:
        actor = self.get_actor_display(obj)
        return strip_leading_actor(obj.message, actor)
