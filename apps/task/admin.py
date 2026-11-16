from django.contrib import admin

from . import models


class TaskLogInline(admin.TabularInline):
    model = models.TaskLog
    extra = 0
    readonly_fields = (
        "message",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
    )
    fields = ("message", "created_at", "created_by")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TaskLineInline(admin.TabularInline):
    model = models.TaskLine
    extra = 0
    autocomplete_fields = ("position", "bin")
    fields = ("sort_order", "position", "bin", "line_status", "notes")


@admin.register(models.Task)
class TaskAdmin(admin.ModelAdmin):
    inlines = [TaskLineInline, TaskLogInline]

    list_display = (
        "code",
        "title",
        "workspace",
        "task_type",
        "status",
        "priority",
        "client",
        "get_cavas",
        "get_assigned_users",
        "created_at",
    )

    list_filter = ("workspace", "task_type", "status", "priority")
    search_fields = ("code", "title", "assigned_to__username")
    filter_horizontal = ("assigned_to", "cavas", "products")
    autocomplete_fields = ("workspace", "client")
    readonly_fields = ("code", "created_at", "updated_at", "created_by", "updated_by")

    def get_assigned_users(self, obj):
        return ", ".join([user.username for user in obj.assigned_to.all()])

    get_assigned_users.short_description = "Asignado a"

    def get_cavas(self, obj):
        return ", ".join(
            [c.name or c.code or str(c.pk) for c in obj.cavas.all()]
        ) or "—"

    get_cavas.short_description = "Cámaras"

    fieldsets = (
        (
            "Información básica",
            {
                "fields": (
                    "code",
                    "workspace",
                    "title",
                    "task_type",
                    "status",
                    "priority",
                )
            },
        ),
        (
            "Alcance",
            {
                "fields": (
                    "client",
                    "cavas",
                    "halls",
                    "products",
                    "include_empty_positions",
                    "require_approval",
                )
            },
        ),
        ("Asignación", {"fields": ("assigned_to",)}),
        ("Tiempo", {"fields": ("started_at", "completed_at")}),
        ("Observaciones", {"fields": ("observations",)}),
        (
            "Información extra",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                    "created_by",
                    "updated_by",
                )
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_fields + ("task_type", "workspace")
        return self.readonly_fields


@admin.register(models.TaskLog)
class TaskLogAdmin(admin.ModelAdmin):
    list_display = ("task", "message", "created_at", "created_by")
    list_filter = ("task__workspace", "created_at")
    search_fields = ("task__code", "message")
    readonly_fields = (
        "task",
        "message",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
        "is_active",
    )
    autocomplete_fields = ("task",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.TaskLine)
class TaskLineAdmin(admin.ModelAdmin):
    list_display = (
        "task",
        "position",
        "bin",
        "line_status",
        "sort_order",
        "created_at",
    )
    list_filter = ("line_status", "task__workspace")
    search_fields = ("task__code", "position__code", "bin__code")
    autocomplete_fields = ("task", "position", "bin")
