from django.contrib import admin

from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "workspace",
        "recipient",
        "event",
        "artifact",
        "message",
        "read_at",
        "created_at",
    )
    list_filter = ("event", "artifact", "workspace")
    search_fields = ("message", "resource_code", "actor_display")
    readonly_fields = ("created_at",)
