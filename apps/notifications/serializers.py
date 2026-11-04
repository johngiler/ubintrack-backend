from rest_framework import serializers

from apps.notifications.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()

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
