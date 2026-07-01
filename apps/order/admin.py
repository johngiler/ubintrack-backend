from django.contrib import admin
from django.utils.html import mark_safe
from apps.core.admin.mixins import ClientFilterMixin

from . import models


class EntryProductInline(admin.TabularInline):
    model = models.EntryProduct
    extra = 1
    fields = ('product', 'quantity', 'real_quantity', 'expiration_date', 'batch')


class EntryDetailInline(admin.StackedInline):
    model = models.EntryDetail
    fields = ('arrival_temperature', 'description',
              'image_preview', 'image', 'pdf_document')
    readonly_fields = ('image_preview',)

    def image_preview(self, obj):
        if obj.image:
            return mark_safe(
                f'''<img
                    src="{obj.image.url}"
                    width="300"
                    height="300"
                    style="object-fit: contain;"
                />'''
            )
        return "-"

    image_preview.short_description = "vista previa de la imagen"


@admin.register(models.Entry)
class EntryAdmin(ClientFilterMixin, admin.ModelAdmin):
    inlines = [EntryProductInline, EntryDetailInline]
    list_display = ('code', 'client', 'workspace', 'storage', 'status',
                    'entry_date', 'products_with_quantities')
    list_filter = ('client__workspace', 'status')
    search_fields = ('code', 'client__name', 'storage__name')
    autocomplete_fields = ('client',)

    def workspace(self, obj):
        if obj.client and obj.client.workspace:
            return obj.client.workspace
        return '-'

    workspace.short_description = 'owner'
    workspace.admin_order_field = 'client__workspace'

    def products_with_quantities(self, obj):
        if hasattr(obj, 'entry_products') and obj.entry_products.exists():
            products = obj.entry_products.all()
            product_list = [
                f"{product} / {product.quantity} {product.product.uom}"
                for product in products
            ]
            return mark_safe("<br>".join(product_list))
        return "-"

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if not request.user.is_superuser:
            fields = [field for field in fields if field not in ['status',]]
        return fields

    products_with_quantities.short_description = 'productos'


class ExitProductGroupInline(admin.TabularInline):
    model = models.ExitProductGroup
    extra = 1
    fields = ('product', 'quantity')
    autocomplete_fields = ('product',)


class ExitProductInline(admin.TabularInline):
    model = models.ExitProduct
    extra = 1
    fields = ('bin_product', 'quantity', 'is_picking', 'bin_position')
    readonly_fields = ('bin_position',)

    def bin_position(self, obj):
        if obj.bin_product and obj.bin_product.bin.position:
            return obj.bin_product.bin.position.code
        else:
            return "-"

    bin_position.short_description = "posición del bin"


class ExitDetailInline(admin.StackedInline):
    model = models.ExitDetail
    fields = ('temperature', 'description', 'image_preview', 'image', 'pdf_document')
    readonly_fields = ('image_preview',)

    def image_preview(self, obj):
        if obj.image:
            return mark_safe(
                f'''<img
                    src="{obj.image.url}"
                    width="300"
                    height="300"
                    style="object-fit: contain;"
                />'''
            )
        return "-"

    image_preview.short_description = "vista previa de la imagen"


@admin.register(models.Exit)
class ExitAdmin(ClientFilterMixin, admin.ModelAdmin):
    inlines = [ExitProductGroupInline, ExitDetailInline]
    list_display = ('code', 'client', 'workspace', 'branch', 'status',
                    'exit_date', 'products_with_quantities')
    list_filter = ('client__workspace', 'status')
    search_fields = ('code', 'client__name')
    autocomplete_fields = ('client',)

    def workspace(self, obj):
        if obj.client and obj.client.workspace:
            return obj.client.workspace
        return '-'

    workspace.short_description = 'owner'
    workspace.admin_order_field = 'client__workspace'

    def products_with_quantities(self, obj):
        if hasattr(obj, 'exit_product_groups') and obj.exit_product_groups.exists():
            groups = obj.exit_product_groups.all()
            product_list = []
            for group in groups:
                product_list.append(
                    f"{group.product.name} / {group.quantity} {group.product.uom}")
            return mark_safe("<br>".join(product_list))
        return "-"

    def get_fields(self, request, obj=None):
        fields = super().get_fields(request, obj)
        if not request.user.is_superuser:
            fields = [field for field in fields if field not in ['status',]]
        return fields

    products_with_quantities.short_description = 'productos'


@admin.register(models.ExitProductGroup)
class ExitProductGroupAdmin(admin.ModelAdmin):
    inlines = [ExitProductInline]
    list_display = ('id', 'exit', 'product', 'quantity')
    search_fields = ('exit__code', 'product__name')
    autocomplete_fields = ('exit', 'product')

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('exit', 'product')
        return []


@admin.register(models.ExitMovement)
class ExitMovementAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'exit', 'movement_type', 'old_status', 'new_status',
        'bin_code', 'product_name', 'quantity',
    )
    list_filter = ('movement_type', 'exit__client__workspace')
    search_fields = ('exit__code', 'bin_code', 'product_name', 'notes')
    readonly_fields = (
        'exit', 'movement_type', 'old_status', 'new_status',
        'product', 'product_sku', 'product_name', 'bin', 'bin_code',
        'old_bin_code', 'new_bin_code', 'quantity', 'old_quantity',
        'new_quantity', 'is_picking', 'notes', 'metadata',
        'created_at', 'updated_at',
    )
    autocomplete_fields = ('exit',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
