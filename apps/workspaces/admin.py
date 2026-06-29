from django.contrib import admin

from apps.workspaces.models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "is_active", "created_at")
    search_fields = ("slug", "name")
    prepopulated_fields = {"slug": ("name",)}
