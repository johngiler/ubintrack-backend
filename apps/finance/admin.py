from django.contrib import admin

from . import models


@admin.register(models.Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = ('name', 'workspace', 'has_picking_tariff', 'created_at')
    list_filter = ('workspace', 'has_picking_tariff')
    search_fields = ('name', 'workspace__slug', 'workspace__name')
    autocomplete_fields = ('workspace',)


@admin.register(models.Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        'number', 'client', 'workspace', 'billing_start_date',
        'billing_end_date', 'total', 'created_at',
    )
    list_filter = ('client__workspace', 'created_at')
    search_fields = ('number', 'client__name', 'client_ruc')
    readonly_fields = (
        'number', 'client', 'billing_start_date', 'billing_end_date',
        'client_ruc', 'client_dv', 'client_address', 'billing_point',
        'vendor', 'comment', 'subtotal', 'tax_exempt', 'tax_amount',
        'total', 'line_items', 'billing_data', 'created_at', 'created_by',
    )
    autocomplete_fields = ('client',)

    def workspace(self, obj):
        if obj.client and obj.client.workspace:
            return obj.client.workspace
        return '-'

    workspace.short_description = 'owner'
    workspace.admin_order_field = 'client__workspace'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
