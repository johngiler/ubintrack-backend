from django.contrib import admin
from django.utils.html import mark_safe

from . import models


@admin.register(models.Storage)
class StorageAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "is_active")
    list_filter = ("workspace", "is_active")
    search_fields = ("name", "workspace__slug")
    autocomplete_fields = ("workspace",)


@admin.register(models.Cava)
class CavaAdmin(admin.ModelAdmin):
    list_display = ('name', 'condition', 'total_levels', 'is_virtual')
    list_filter = ('condition', 'is_virtual')


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
