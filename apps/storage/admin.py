from django.contrib import admin
from django.utils.html import mark_safe

from . import models


@admin.register(models.Storage)
class StorageAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "is_active")
    list_filter = ("workspace", "is_active")
    search_fields = ("name", "workspace__slug")
    autocomplete_fields = ("workspace",)


@admin.register(models.PositionNomenclature)
class PositionNomenclatureAdmin(admin.ModelAdmin):
    list_display = (
        "workspace",
        "separator",
        "cava_prefix",
        "aisle_prefix",
        "rack_prefix",
        "level_prefix",
    )
    autocomplete_fields = ("workspace",)


@admin.register(models.Cava)
class CavaAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "condition", "total_levels", "is_virtual")
    list_filter = ("condition", "is_virtual")
    search_fields = ("name", "code")


@admin.register(models.CavaBlock)
class CavaBlockAdmin(admin.ModelAdmin):
    list_display = (
        "cava",
        "sort_order",
        "kind",
        "section_type",
        "name",
        "code",
        "rack_count",
        "levels",
        "depth",
        "orientation",
    )
    list_filter = ("kind",)
    autocomplete_fields = ("cava",)


@admin.register(models.CavaGenerationJob)
class CavaGenerationJobAdmin(admin.ModelAdmin):
    list_display = ("cava", "workspace", "status", "expected_count", "created_count", "skipped_count")
    list_filter = ("status", "workspace")
    readonly_fields = ("cava", "workspace", "status", "expected_count", "created_count", "skipped_count")


@admin.register(models.Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ('code', 'level', 'place', 'rack', 'hall',
                    'cava', 'is_active', 'status')
    list_filter = ('status', 'is_active', 'hall', 'cava')
    search_fields = ('code',)
    readonly_fields = ('qrcode_image_preview',)

    def qrcode_image_preview(self, obj):
        if obj.qr_code_image:
            return mark_safe(
                f'<a href="{obj.qr_code_image.url}" target="_blank"><img src="{
                    obj.qr_code_image.url}" width="250" /></a>'
            )
        return "No Image"

    qrcode_image_preview.short_description = 'imagen de código QR'
