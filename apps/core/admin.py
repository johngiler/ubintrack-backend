from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.core import models


@admin.register(models.CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = (
        "username",
        "email",
        "full_name",
        "user_type",
        "workspace",
        "client",
        "profile",
        "is_active",
        "is_staff",
    )
    list_filter = ("user_type", "workspace", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email", "full_name")
    ordering = ("-date_joined",)
    filter_horizontal = ()

    fieldsets = (
        ("Inicio de sesión", {"fields": ("username", "password")}),
        ("Información personal", {"fields": ("full_name", "email")}),
        ("Owner / WMS", {"fields": ("workspace", "user_type", "profile", "client")}),
        ("Permisos", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("Fechas", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "password1",
                    "password2",
                    "full_name",
                    "email",
                    "workspace",
                    "user_type",
                    "profile",
                    "client",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
    )


@admin.register(models.Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "is_active")
    list_filter = ("workspace",)


@admin.register(models.Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "random_id", "is_active")
    list_filter = ("workspace", "is_active")
    search_fields = ("name", "random_id")


@admin.register(models.Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "is_active")


admin.site.register(models.CustomPermission)
admin.site.register(models.PermissionMeta)
