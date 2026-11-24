from django.contrib import admin

from .models import StockMovement


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    """Read-only ledger. Stock is adjusted from the staff dashboard (Inventory page)."""

    list_display = ("created_at", "variant", "change", "stock_before", "stock_after", "reason", "order",
                    "created_by", "note")
    list_filter = ("reason",)
    search_fields = ("variant__sku", "variant__product__name", "order__order_number", "note")
    list_select_related = ("variant__product", "order", "created_by")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
