from rest_framework import serializers

from apps.audit.models import ActivityBatch, ActivityBatchItem, ViewActivityEntry


def _actor_display(user) -> str:
    if user is None:
        return "Sistema"
    full = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    if full:
        return full
    return getattr(user, "email", None) or getattr(user, "username", None) or "Usuario"


class ViewActivityEntrySerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()
    class Meta:
        model = ViewActivityEntry
        fields = (
            "id",
            "section",
            "action",
            "summary",
            "changes",
            "resource_id",
            "resource_code",
            "affected_count",
            "batch_id",
            "actor_display",
            "created_at",
        )

    def get_actor_display(self, obj) -> str:
        return _actor_display(obj.actor)


class ActivityBatchSerializer(serializers.ModelSerializer):
    actor_display = serializers.SerializerMethodField()

    class Meta:
        model = ActivityBatch
        fields = (
            "id",
            "section",
            "kind",
            "summary",
            "item_count",
            "actor_display",
            "created_at",
        )

    def get_actor_display(self, obj) -> str:
        return _actor_display(obj.actor)


class ActivityBatchItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityBatchItem
        fields = ("id", "resource_id", "resource_code", "extra")
