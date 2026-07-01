from django.contrib import admin

from . import models


class TaskLogInline(admin.TabularInline):
    model = models.TaskLog
    extra = 0
    readonly_fields = ("message", "created_at", "updated_at",
                       "created_by", "updated_by")
    fields = ("message", "created_at", "created_by")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.Task)
class TaskAdmin(admin.ModelAdmin):
    inlines = [TaskLogInline]

    list_display = (
        "code",
        "workspace",
        "task_type",
        "status",
        "priority",
        "get_assigned_users",
        "started_at",
        "completed_at",
        "created_at",
    )

    list_filter = ("workspace", "task_type", "status", "priority")
    search_fields = ("code", "assigned_to__username",
                     "entry__code", "exit__code")
    filter_horizontal = ("assigned_to",)
    autocomplete_fields = ("workspace", "entry", "exit")
    readonly_fields = ("code", "created_at", "updated_at",
                       "created_by", "updated_by")

    def get_assigned_users(self, obj):
        return ", ".join([user.username for user in obj.assigned_to.all()])

    get_assigned_users.short_description = 'Asignado a'

    fieldsets = (
        ("Información básica", {
            "fields": ("code", "workspace", "task_type", "status", "priority")
        }),
        ("Asignación", {
            "fields": ("assigned_to", "entry", "exit")
        }),
        ("Tiempo", {
            "fields": ("started_at", "completed_at")
        }),
        ("Observaciones", {
            "fields": ("observations",)
        }),
        ("Información extra", {
            "fields": (
                "created_at",
                "updated_at",
                "created_by",
                "updated_by"
            )
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_fields + ("task_type", "workspace")
        return self.readonly_fields


@admin.register(models.TaskLog)
class TaskLogAdmin(admin.ModelAdmin):
    list_display = ('task', 'message', 'created_at', 'created_by')
    list_filter = ('task__workspace', 'created_at')
    search_fields = ('task__code', 'message')
    readonly_fields = (
        'task', 'message', 'created_at', 'updated_at',
        'created_by', 'updated_by', 'is_active',
    )
    autocomplete_fields = ('task',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.UserTaskTracking)
class UserTaskTrackingAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "task",
        "timer_start",
        "timer_stop",
        "duration",
        "time_since_last_task",
        "created_at",
    )

    list_filter = ("user__workspace", "timer_start", "timer_stop")
    search_fields = ("user__username", "task__code")
    autocomplete_fields = ("user", "task")
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")

    fieldsets = (
        ("Información de seguimiento", {
            "fields": (
                "user",
                "task",
                "timer_start",
                "timer_stop",
                "duration",
                "time_since_last_task",
            )
        }),
        ("Información extra", {
            "fields": (
                "created_at",
                "updated_at",
                "created_by",
                "updated_by",
            )
        }),
    )
