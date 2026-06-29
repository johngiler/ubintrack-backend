from django.contrib import admin

from . import models


@admin.register(models.Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = ('name',)
