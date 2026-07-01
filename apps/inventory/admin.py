from django.contrib import admin
from django.utils.html import mark_safe
from apps.core.admin_mixins import ClientFilterMixin

from . import models


@admin.register(models.Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'category_code')
    filter_horizontal = ('coexistence',)


@admin.register(models.Product)
class ProductAdmin(ClientFilterMixin, admin.ModelAdmin):
    list_filter = ('rotation_type', 'condition', 'category')
    list_display = ('name', 'client', 'code', 'client_sku', 'rotation_type')
    search_fields = ('name', 'client_sku')
    autocomplete_fields = ('client',)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.request = request
        return form


class BinProductInline(admin.TabularInline):
    model = models.BinProduct
    extra = 1
    fields = ('entry_product', 'batch', 'expiration_date', 'quantity')

    def get_max_num(self, request, obj=None, **kwargs):
        return 1

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.request = request
        formset.form.bin_instance = obj
        return formset


@admin.register(models.Bin)
class BinAdmin(admin.ModelAdmin):
    list_display = ('code', 'client', 'entry', 'status',
                    'position', 'product_with_quantities')
    search_fields = ('code', 'entry__code', 'position__code', 'client__name')
    autocomplete_fields = ('entry',)
    readonly_fields = ('tag_image_preview',)
    inlines = [BinProductInline,]

    def tag_image_preview(self, obj):
        if obj.tag_image:
            return mark_safe(
                f'<a href="{obj.tag_image.url}" target="_blank"><img src="{
                    obj.tag_image.url}" width="400" /></a>'
            )
        return "No Image"

    tag_image_preview.short_description = 'imagen del tag'

    def product_with_quantities(self, obj):
        if hasattr(obj, 'bin_products') and obj.bin_products.exists():
            products = obj.bin_products.all()
            product_list = [
                f"{product} / {product.quantity} {product.entry_product.product.uom}"
                for product in products
            ]
            return mark_safe("<br>".join(product_list))
        return "-"

    product_with_quantities.short_description = 'productos'
