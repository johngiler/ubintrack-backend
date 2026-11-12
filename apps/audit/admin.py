from django.contrib import admin

from apps.audit.models import ActivityBatch, ActivityBatchItem, ViewActivityEntry
from apps.audit.services.deletion import allow_audit_deletion


class ImmutableAuditAdminMixin:
    def delete_model(self, request, obj):
        with allow_audit_deletion():
            super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        with allow_audit_deletion():
            super().delete_queryset(request, queryset)


@admin.register(ViewActivityEntry)
class ViewActivityEntryAdmin(ImmutableAuditAdminMixin, admin.ModelAdmin):
    list_display = ("id", "section", "action", "summary", "affected_count", "actor", "created_at")
    list_filter = ("section", "action")
    search_fields = ("summary", "resource_code")
    readonly_fields = (
        "workspace",
        "section",
        "action",
        "summary",
        "changes",
        "resource_id",
        "resource_code",
        "affected_count",
        "batch",
        "actor",
        "created_at",
    )


@admin.register(ActivityBatch)
class ActivityBatchAdmin(ImmutableAuditAdminMixin, admin.ModelAdmin):
    list_display = ("id", "section", "kind", "summary", "item_count", "actor", "created_at")
    list_filter = ("section", "kind")
    readonly_fields = (
        "workspace",
        "section",
        "kind",
        "summary",
        "item_count",
        "actor",
        "created_at",
    )


@admin.register(ActivityBatchItem)
class ActivityBatchItemAdmin(ImmutableAuditAdminMixin, admin.ModelAdmin):
    list_display = ("id", "batch", "resource_id", "resource_code")
    search_fields = ("resource_code",)
