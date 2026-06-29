from django.contrib import admin

from . import models
from . import forms


class TaskLogInline(admin.TabularInline):
    model = models.TaskLog
    extra = 0
    readonly_fields = ("message", "created_at", "updated_at",
                       "created_by", "updated_by")
    fields = ("message", "created_at", "created_by")


@admin.register(models.Task)
class TaskAdmin(admin.ModelAdmin):
    inlines = [TaskLogInline,]

    list_display = (
        "code",
        "task_type",
        "status",
        "priority",
        "get_assigned_users",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    )

    list_filter = ("task_type", "status", "priority")
    search_fields = ("code", "assigned_to__username", "entry__code", "exit__code")
    filter_horizontal = ("assigned_to",)
    autocomplete_fields = ("entry", "exit")
    readonly_fields = ("code", "created_at", "updated_at", "created_by", "updated_by")

    def get_assigned_users(self, obj):
        """Mostrar usuarios asignados en el list_display"""
        return ", ".join([user.username for user in obj.assigned_to.all()])
    get_assigned_users.short_description = 'Asignado a'

    fieldsets = (
        ("Información básica", {
            "fields": ("code", "task_type", "status", "priority")
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

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        new_fieldsets = []
        for title, options in fieldsets:
            new_title = title.upper() if title else title
            new_fieldsets.append((new_title, options))
        return new_fieldsets

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_fields + ("task_type",)
        return self.readonly_fields


@admin.register(models.UserTaskTracking)
class UserTaskTrackingAdmin(admin.ModelAdmin):
    form = forms.UserTaskTrackingForm

    list_display = (
        "user",
        "task",
        "timer_start",
        "timer_stop",
        "duration",
        "time_since_last_task",
        "created_at",
        "updated_at",
    )

    list_filter = ("user", "timer_start", "timer_stop")
    search_fields = ("user__username", "task__code")
    autocomplete_fields = ("user",)
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

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        new_fieldsets = []
        for title, options in fieldsets:
            new_title = title.upper() if title else title
            new_fieldsets.append((new_title, options))
        return new_fieldsets
