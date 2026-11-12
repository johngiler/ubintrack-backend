from django.contrib import admin

from apps.workspaces.models import Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "printer_ip", "is_active", "created_at")
    search_fields = ("slug", "name", "printer_ip")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        (None, {"fields": ("slug", "name", "legal_name", "rif", "is_active")}),
        (
            "Marca",
            {
                "fields": (
                    "app_title",
                    "app_tagline",
                    "primary_color",
                    "secondary_color",
                    "sidebar_logo_gradient_mode",
                    "logo",
                    "logo_mark",
                    "favicon",
                )
            },
        ),
        ("Contacto", {"fields": ("support_email", "phone", "invoice_prefix")}),
        (
            "Impresora",
            {
                "fields": (
                    "printer_ip",
                    "printer_port",
                    "label_width_in",
                    "label_height_in",
                    "printer_dpi",
                    "printer_language",
                )
            },
        ),
    )
