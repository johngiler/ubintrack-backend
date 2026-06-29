from django.contrib import admin
from django.utils.html import mark_safe
from apps.core.admin_mixins import ClientFilterMixin

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
    list_display = ('code', 'client', 'storage', 'status',
                    'entry_date', 'products_with_quantities')
    search_fields = ('code', 'client__name', 'storage__name')
    autocomplete_fields = ('client',)

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
    list_display = ('code', 'client', 'branch', 'status',
                    'exit_date', 'products_with_quantities')
    search_fields = ('code', 'client__name')
    autocomplete_fields = ('client',)

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
