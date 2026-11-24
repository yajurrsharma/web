from django.contrib import admin

from .models import SkuMapping


@admin.register(SkuMapping)
class SkuMappingAdmin(admin.ModelAdmin):
    list_display = ("external_sku", "channel", "variant", "asin", "sync_inventory")
    search_fields = ("external_sku", "variant__sku", "asin")
    autocomplete_fields = ()
